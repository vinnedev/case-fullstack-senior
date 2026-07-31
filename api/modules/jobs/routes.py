from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response

from modules.auth.context import AuthContext, current_ctx
from modules.jobs.errors import (
    CompanyUnknownError,
    ConcurrencyLimitError,
    IdempotencyKeyConflictError,
    InvalidJobStateError,
    JobNotFoundError,
    ResultNotFoundError,
    RetryLimitError,
)
from modules.jobs.schemas import (
    IdempotencyKey,
    JobCancelled,
    JobCreated,
    JobDetail,
    JobResultOut,
    JobRetried,
    JobSummary,
    NewJob,
)
from modules.jobs.service import JobsService
from shared.db.pool import DictConnection, get_db
from shared.logging.wide_event import current_event
from shared.observability.api_metrics import jobs_created_total

TOTAL_COUNT_HEADER = {
    "X-Total-Count": {
        "description": "Total de itens da consulta (respeitando os filtros), não apenas da página",
        "schema": {"type": "integer", "minimum": 0},
    }
}

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    responses={
        401: {"description": "Header X-Auth ausente ou inválido"},
        503: {"description": "Servidor encerrando (graceful shutdown) — repita a requisição em outra réplica"},
    },
)

JobId = Path(description="Identificador do job", ge=1, examples=[42])


def jobs_service(conn: DictConnection = Depends(get_db)) -> JobsService:
    return JobsService(conn)


@router.get(
    "",
    summary="Lista os jobs da empresa autenticada",
    responses={200: {"description": "Página de jobs", "headers": TOTAL_COUNT_HEADER}},
)
def list_jobs(
    response: Response,
    limit: int = Query(default=50, ge=1, le=200, description="Máximo de jobs por página"),
    offset: int = Query(default=0, ge=0, description="Deslocamento para paginação"),
    status: Literal["queued", "running", "done", "failed", "cancelled"] | None = Query(default=None, description="Filtra por status"),
    ctx: AuthContext = Depends(current_ctx),
    service: JobsService = Depends(jobs_service),
) -> list[JobSummary]:
    current_event().add(company_id=ctx.company_id, role=ctx.role, limit=limit, offset=offset, status_filter=status)
    rows = service.list_jobs(ctx.company_id, limit=limit, offset=offset, status=status)
    response.headers["X-Total-Count"] = str(service.count_jobs(ctx.company_id, status=status))
    current_event().add(jobs_returned=len(rows))
    return [JobSummary.model_validate(row) for row in rows]


@router.get(
    "/{job_id}",
    summary="Consulta o status de um job",
    responses={404: {"description": "Job não encontrado nesta empresa"}},
)
def get_job(job_id: int = JobId, *, ctx: AuthContext = Depends(current_ctx), service: JobsService = Depends(jobs_service)) -> JobDetail:
    current_event().add(company_id=ctx.company_id, job_id=job_id)
    try:
        job = service.get_job(ctx.company_id, job_id)
    except JobNotFoundError:
        raise HTTPException(404, "not found") from None
    current_event().add(job_kind=job["kind"], job_status=job["status"])
    return JobDetail.model_validate(job)


@router.get(
    "/{job_id}/result",
    summary="Lê o resultado de um job concluído",
    responses={404: {"description": "Job sem resultado disponível nesta empresa"}},
)
def get_result(
    job_id: int = JobId, *, ctx: AuthContext = Depends(current_ctx), service: JobsService = Depends(jobs_service)
) -> JobResultOut:
    current_event().add(company_id=ctx.company_id, job_id=job_id)
    try:
        result = service.get_result(ctx.company_id, job_id)
    except ResultNotFoundError:
        raise HTTPException(404, "no result") from None
    return JobResultOut.model_validate(result)


@router.post(
    "",
    summary="Cria um novo job",
    status_code=201,
    responses={
        401: {"description": "Header X-Auth ausente/inválido ou empresa desconhecida"},
        409: {"description": "Idempotency-Key já utilizada com payload diferente"},
        429: {"description": "Limite de jobs concorrentes da empresa atingido"},
    },
)
def create_job(
    body: NewJob,
    idempotency_key: Annotated[
        IdempotencyKey | None,
        Header(
            alias="Idempotency-Key",
            description=(
                "**Opcional, recomendado.** Chave de idempotência por empresa: repetir a mesma "
                "chave com o mesmo payload devolve o job original em vez de criar outro (protege "
                "contra duplo-clique e retry de rede); a mesma chave com payload diferente "
                "responde 409. Sem o header a API gera uma chave própria e a requisição não tem "
                "proteção contra reenvio."
            ),
            examples=["3f2c1e9a-duplo-clique"],
        ),
    ] = None,
    ctx: AuthContext = Depends(current_ctx),
    service: JobsService = Depends(jobs_service),
    conn: DictConnection = Depends(get_db),
) -> JobCreated:
    if idempotency_key is None:
        idempotency_key = f"srv-{uuid4().hex}"
        current_event().add(idempotency_key_generated=True)
    event = current_event().add(company_id=ctx.company_id, job_kind=body.kind, idempotency_key=idempotency_key)
    try:
        job, created = service.create_job(
            company_id=ctx.company_id,
            kind=body.kind,
            trace_id=str(current_event().fields.get("request_id") or ""),
            idempotency_key=idempotency_key,
            submitted_by=f"{ctx.company_id}:{ctx.role}",
        )
    except CompanyUnknownError:
        raise HTTPException(401, "empresa desconhecida") from None
    except IdempotencyKeyConflictError as exc:
        event.add(existing_kind=exc.existing_kind)
        raise HTTPException(409, "Idempotency-Key já utilizada com payload diferente") from exc
    except ConcurrencyLimitError as exc:
        event.add(concurrency_limit=exc.limit, jobs_running=exc.running)
        raise HTTPException(429, "limite de jobs concorrentes atingido") from exc
    event.add(job_id=job["id"], idempotency_replay=not created)
    response = JobCreated.model_validate(job)
    conn.commit()
    if created:
        jobs_created_total.inc()
    return response


@router.post(
    "/{job_id}/cancel",
    summary="Cancela um job em queued ou running",
    responses={
        404: {"description": "Job não encontrado nesta empresa"},
        409: {"description": "Job em estado não cancelável"},
    },
)
def cancel_job(
    job_id: int = JobId,
    *,
    ctx: AuthContext = Depends(current_ctx),
    service: JobsService = Depends(jobs_service),
    conn: DictConnection = Depends(get_db),
) -> JobCancelled:
    event = current_event().add(company_id=ctx.company_id, job_id=job_id, action="cancel")
    try:
        job = service.cancel_job(
            ctx.company_id,
            job_id,
            f"{ctx.company_id}:{ctx.role}",
            str(current_event().fields.get("request_id") or ""),
        )
    except JobNotFoundError:
        raise HTTPException(404, "not found") from None
    except InvalidJobStateError as exc:
        event.add(job_status=exc.status)
        raise HTTPException(409, f"job em estado '{exc.status}' não pode ser cancelado") from exc
    event.add(job_status=job["status"])
    response = JobCancelled.model_validate(job)
    conn.commit()
    return response


@router.post(
    "/{job_id}/retry",
    summary="Reenfileira um job que falhou (idempotente)",
    responses={
        404: {"description": "Job não encontrado nesta empresa"},
        409: {"description": "Job não está em failed ou atingiu o máximo de tentativas"},
    },
)
def retry_job(
    job_id: int = JobId,
    *,
    ctx: AuthContext = Depends(current_ctx),
    service: JobsService = Depends(jobs_service),
    conn: DictConnection = Depends(get_db),
) -> JobRetried:
    event = current_event().add(company_id=ctx.company_id, job_id=job_id, action="retry")
    try:
        job = service.retry_job(
            ctx.company_id,
            job_id,
            f"{ctx.company_id}:{ctx.role}",
            str(current_event().fields.get("request_id") or ""),
        )
    except JobNotFoundError:
        raise HTTPException(404, "not found") from None
    except RetryLimitError as exc:
        event.add(attempts=exc.attempts)
        raise HTTPException(409, f"máximo de tentativas atingido ({exc.attempts}/{exc.limit})") from exc
    except InvalidJobStateError as exc:
        event.add(job_status=exc.status)
        raise HTTPException(409, f"job em estado '{exc.status}' não pode ser reprocessado") from exc
    event.add(job_status=job["status"], attempts=job["attempts"])
    response = JobRetried.model_validate(job)
    conn.commit()
    return response
