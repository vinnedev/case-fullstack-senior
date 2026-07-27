import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from auth import current_ctx
from db import get_conn, close_all_connections
from shared.logging.wide_event import WideEvent, start_event, current_event

SERVICE = "api"
SHUTDOWN_DRAIN_TIMEOUT = 30.0


class GracefulShutdown:
    def __init__(self):
        self.shutting_down = False
        self._in_flight = 0
        self._lock = asyncio.Lock()
        self._drained = asyncio.Event()
        self._drained.set()

    async def request_started(self) -> bool:
        if self.shutting_down:
            return False
        async with self._lock:
            self._in_flight += 1
            self._drained.clear()
        return True

    async def request_finished(self):
        async with self._lock:
            self._in_flight -= 1
            if self._in_flight <= 0:
                self._drained.set()

    async def drain(self, timeout: float):
        self.shutting_down = True
        if self._in_flight <= 0:
            return
        try:
            await asyncio.wait_for(self._drained.wait(), timeout)
        except asyncio.TimeoutError:
            WideEvent(SERVICE, "shutdown_drain_timeout").error(timeout_s=timeout, in_flight=self._in_flight).emit()


shutdown = GracefulShutdown()


@asynccontextmanager
async def lifespan(app: FastAPI):
    WideEvent(SERVICE, "startup").emit()
    yield
    event = WideEvent(SERVICE, "shutdown")
    await shutdown.drain(SHUTDOWN_DRAIN_TIMEOUT)
    close_all_connections()
    event.add(drained=True, db_connections_closed=True).emit()


API_DESCRIPTION = """
API do **Relay**: submissão e acompanhamento de jobs assíncronos por empresa.

## Autenticação

Todas as rotas exigem o header `X-Auth` no formato `<company_id>:<role>`
(ex.: `1:admin`). Simplificação do case — sem assinatura.

## Fluxo

1. `POST /jobs` cria um job com status `queued` (sujeito ao limite de
   concorrência da empresa).
2. O worker processa a fila e grava o resultado.
3. `GET /jobs/{id}` acompanha o status; `GET /jobs/{id}/result` lê o payload.
"""

TAGS_METADATA = [
    {"name": "jobs", "description": "Criação e consulta de jobs da empresa autenticada."},
    {"name": "admin", "description": "Visão administrativa de todos os jobs."},
]

app = FastAPI(
    title="Relay API",
    version="0.1.0",
    description=API_DESCRIPTION,
    openapi_tags=TAGS_METADATA,
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def request_lifecycle_middleware(request, call_next):
    if not await shutdown.request_started():
        return JSONResponse({"detail": "servidor encerrando"}, status_code=503, headers={"Connection": "close"})
    event = start_event(
        SERVICE,
        "http_request",
        request_id=str(uuid.uuid4()),
        http_method=request.method,
        http_path=request.url.path,
    )
    try:
        response = await call_next(request)
        event.add(http_status=response.status_code)
        if response.status_code >= 500:
            event.error()
        return response
    except Exception as exc:
        event.error(exc, http_status=500)
        raise
    finally:
        event.emit()
        await shutdown.request_finished()


class JobSummary(BaseModel):
    id: int
    kind: str
    status: str
    created_at: str
    result_count: int


class JobDetail(BaseModel):
    id: int
    company_id: int
    kind: str
    status: str


class JobResult(BaseModel):
    payload: str


class NewJob(BaseModel):
    kind: str = Field(description="Tipo do job a processar", examples=["report"])


class JobCreated(BaseModel):
    id: int
    status: str = Field(examples=["queued"])


class AdminJob(BaseModel):
    id: int
    company_id: int
    status: str


@app.get(
    "/jobs",
    tags=["jobs"],
    summary="Lista os jobs da empresa autenticada",
    response_model=list[JobSummary],
    responses={401: {"description": "Header X-Auth ausente ou inválido"}},
)
def list_jobs(ctx=Depends(current_ctx)):
    current_event().add(company_id=ctx["company_id"], role=ctx["role"])
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, kind, status, created_at FROM jobs WHERE company_id=%s ORDER BY created_at DESC", (ctx["company_id"],))
        rows = cur.fetchall()
        out = []
        for r in rows:
            cur.execute("SELECT count(*) FROM job_results WHERE job_id=%s", (r[0],))
            out.append({"id": r[0], "kind": r[1], "status": r[2], "created_at": r[3].isoformat(), "result_count": cur.fetchone()[0]})
        current_event().add(jobs_returned=len(out))
        return out

@app.get(
    "/jobs/{job_id}",
    tags=["jobs"],
    summary="Consulta o status de um job",
    response_model=JobDetail,
    responses={404: {"description": "Job não encontrado"}},
)
def get_job(job_id: int, ctx=Depends(current_ctx)):
    current_event().add(company_id=ctx["company_id"], job_id=job_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, company_id, kind, status FROM jobs WHERE id=%s", (job_id,))
        row = cur.fetchone()
        if not row: raise HTTPException(404, "not found")
        current_event().add(job_kind=row[2], job_status=row[3])
        return {"id": row[0], "company_id": row[1], "kind": row[2], "status": row[3]}

@app.get(
    "/jobs/{job_id}/result",
    tags=["jobs"],
    summary="Lê o resultado de um job concluído",
    response_model=JobResult,
    responses={404: {"description": "Job sem resultado disponível"}},
)
def get_result(job_id: int, ctx=Depends(current_ctx)):
    current_event().add(company_id=ctx["company_id"], job_id=job_id)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT payload FROM job_results WHERE job_id=%s", (job_id,))
        row = cur.fetchone()
        if not row: raise HTTPException(404, "no result")
        return {"payload": row[0]}

@app.post(
    "/jobs",
    tags=["jobs"],
    summary="Cria um novo job",
    status_code=201,
    response_model=JobCreated,
    responses={429: {"description": "Limite de jobs concorrentes da empresa atingido"}},
)
def create_job(body: NewJob, ctx=Depends(current_ctx)):
    event = current_event().add(company_id=ctx["company_id"], job_kind=body.kind)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT max_concurrent_jobs FROM companies WHERE id=%s", (ctx["company_id"],))
        limit = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM jobs WHERE company_id=%s AND status IN ('queued','running')", (ctx["company_id"],))
        running = cur.fetchone()[0]
        event.add(concurrency_limit=limit, jobs_running=running)
        if running >= limit:
            raise HTTPException(429, "limite de jobs concorrentes atingido")
        cur.execute("INSERT INTO jobs (company_id, kind, status) VALUES (%s,%s,'queued') RETURNING id", (ctx["company_id"], body.kind))
        conn.commit()
        job_id = cur.fetchone()[0]
        event.add(job_id=job_id)
        return {"id": job_id, "status": "queued"}

@app.get(
    "/admin/jobs",
    tags=["admin"],
    summary="Lista todos os jobs de todas as empresas",
    response_model=list[AdminJob],
)
def admin_jobs(ctx=Depends(current_ctx)):
    current_event().add(company_id=ctx["company_id"], role=ctx["role"])
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, company_id, status FROM jobs ORDER BY id")
        return [{"id": r[0], "company_id": r[1], "status": r[2]} for r in cur.fetchall()]
