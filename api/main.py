import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from auth import current_ctx
from features.companies.models import Company
from features.jobs.models import Job, JobResult
from shared.db.engine import dispose_engine, get_session
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
    dispose_engine()
    event.add(drained=True, db_pool_disposed=True).emit()


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


class JobResultOut(BaseModel):
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
def list_jobs(ctx=Depends(current_ctx), session: Session = Depends(get_session)):
    current_event().add(company_id=ctx["company_id"], role=ctx["role"])
    rows = session.execute(
        select(Job, func.count(JobResult.id))
        .outerjoin(JobResult, JobResult.job_id == Job.id)
        .where(Job.company_id == ctx["company_id"])
        .group_by(Job.id)
        .order_by(Job.created_at.desc())
    ).all()
    out = [
        {"id": job.id, "kind": job.kind, "status": job.status, "created_at": job.created_at.isoformat(), "result_count": count}
        for job, count in rows
    ]
    current_event().add(jobs_returned=len(out))
    return out

@app.get(
    "/jobs/{job_id}",
    tags=["jobs"],
    summary="Consulta o status de um job",
    response_model=JobDetail,
    responses={404: {"description": "Job não encontrado"}},
)
def get_job(job_id: int, ctx=Depends(current_ctx), session: Session = Depends(get_session)):
    current_event().add(company_id=ctx["company_id"], job_id=job_id)
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "not found")
    current_event().add(job_kind=job.kind, job_status=job.status)
    return {"id": job.id, "company_id": job.company_id, "kind": job.kind, "status": job.status}

@app.get(
    "/jobs/{job_id}/result",
    tags=["jobs"],
    summary="Lê o resultado de um job concluído",
    response_model=JobResultOut,
    responses={404: {"description": "Job sem resultado disponível"}},
)
def get_result(job_id: int, ctx=Depends(current_ctx), session: Session = Depends(get_session)):
    current_event().add(company_id=ctx["company_id"], job_id=job_id)
    result = session.execute(select(JobResult).where(JobResult.job_id == job_id)).scalar_one_or_none()
    if not result:
        raise HTTPException(404, "no result")
    return {"payload": result.payload}

@app.post(
    "/jobs",
    tags=["jobs"],
    summary="Cria um novo job",
    status_code=201,
    response_model=JobCreated,
    responses={429: {"description": "Limite de jobs concorrentes da empresa atingido"}},
)
def create_job(body: NewJob, ctx=Depends(current_ctx), session: Session = Depends(get_session)):
    event = current_event().add(company_id=ctx["company_id"], job_kind=body.kind)
    company = session.get(Company, ctx["company_id"])
    if not company:
        raise HTTPException(401, "empresa desconhecida")
    running = session.execute(
        select(func.count()).select_from(Job).where(Job.company_id == company.id, Job.status.in_(("queued", "running")))
    ).scalar_one()
    event.add(concurrency_limit=company.max_concurrent_jobs, jobs_running=running)
    if running >= company.max_concurrent_jobs:
        raise HTTPException(429, "limite de jobs concorrentes atingido")
    job = Job(company_id=company.id, kind=body.kind, status="queued")
    session.add(job)
    session.flush()
    event.add(job_id=job.id)
    return {"id": job.id, "status": "queued"}

@app.get(
    "/admin/jobs",
    tags=["admin"],
    summary="Lista todos os jobs de todas as empresas",
    response_model=list[AdminJob],
)
def admin_jobs(ctx=Depends(current_ctx), session: Session = Depends(get_session)):
    current_event().add(company_id=ctx["company_id"], role=ctx["role"])
    jobs = session.execute(select(Job).order_by(Job.id)).scalars().all()
    return [{"id": j.id, "company_id": j.company_id, "status": j.status} for j in jobs]
