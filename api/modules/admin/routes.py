from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from modules.admin.service import AdminService
from modules.auth.context import AuthContext, current_ctx
from modules.jobs.routes import TOTAL_COUNT_HEADER
from modules.jobs.schemas import AdminJob
from shared.db.pool import DictConnection, get_db
from shared.logging.wide_event import current_event

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    responses={
        401: {"description": "Header X-Auth ausente ou inválido"},
        503: {"description": "Servidor encerrando (graceful shutdown) — repita a requisição em outra réplica"},
    },
)


def admin_service(conn: DictConnection = Depends(get_db)) -> AdminService:
    return AdminService(conn)


def require_admin(ctx: AuthContext = Depends(current_ctx)) -> AuthContext:
    if ctx.role != "admin":
        raise HTTPException(403, "requer role admin")
    return ctx


class AdminCompany(BaseModel):
    """Empresa com o resumo de processamento (contagens por status)."""

    id: int = Field(ge=1, description="Identificador da empresa")
    name: str = Field(description="Nome da empresa")
    max_concurrent_jobs: int = Field(ge=0, description="Limite de jobs concorrentes")
    job_quota: int = Field(description="Cota restante de jobs (a base original apenas decrementa)")
    total_jobs: int = Field(ge=0, description="Total de jobs da empresa")
    queued: int = Field(ge=0, description="Jobs na fila")
    running: int = Field(ge=0, description="Jobs em processamento")
    done: int = Field(ge=0, description="Jobs concluídos")
    failed: int = Field(ge=0, description="Jobs com falha")
    cancelled: int = Field(ge=0, description="Jobs cancelados")


class DeadLetterJob(BaseModel):
    """Registro imutável de auditoria: job que esgotou as tentativas."""

    id: int = Field(description="Identificador da entrada de auditoria")
    job_id: int | None = Field(description="Job de origem; `null` se o job já foi expurgado")
    company_id: int = Field(description="Empresa dona do job (preservada mesmo após expurgo)")
    kind: str = Field(description="Tipo do job no momento da falha")
    attempts: int = Field(description="Tentativas consumidas até esgotar o limite")
    last_error: str | None = Field(description="Erro que causou o esgotamento das tentativas")
    trace_id: str | None = Field(description="Correlação com a request HTTP que criou o job")
    job_created_at: datetime | None = Field(description="Criação do job de origem")
    failed_at: datetime = Field(description="Momento em que entrou na DLQ")


@router.get(
    "/dlq",
    summary="Dead Letter Queue: jobs que esgotaram as tentativas (auditoria)",
    responses={
        200: {"description": "Página da DLQ", "headers": TOTAL_COUNT_HEADER},
        403: {"description": "Requer role admin"},
    },
)
def dead_letter_queue(
    response: Response,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: AuthContext = Depends(require_admin),
    service: AdminService = Depends(admin_service),
) -> list[DeadLetterJob]:
    current_event().add(company_id=ctx.company_id, role=ctx.role, action="dlq")
    response.headers["X-Total-Count"] = str(service.count_dead_letters())
    return [DeadLetterJob.model_validate(row) for row in service.list_dead_letters(limit=limit, offset=offset)]


@router.get(
    "/companies",
    summary="Lista empresas com o resumo de processamento",
    responses={
        200: {"description": "Página de empresas com contagens de jobs por status", "headers": TOTAL_COUNT_HEADER},
        403: {"description": "Requer role admin"},
    },
)
def admin_companies(
    response: Response,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: AuthContext = Depends(require_admin),
    service: AdminService = Depends(admin_service),
) -> list[AdminCompany]:
    current_event().add(company_id=ctx.company_id, role=ctx.role, action="companies")
    response.headers["X-Total-Count"] = str(service.count_companies())
    return [AdminCompany.model_validate(row) for row in service.list_companies(limit=limit, offset=offset)]


@router.get(
    "/jobs",
    summary="Lista todos os jobs de todas as empresas",
    responses={
        200: {"description": "Página de jobs de todas as empresas", "headers": TOTAL_COUNT_HEADER},
        403: {"description": "Requer role admin"},
    },
)
def admin_jobs(
    response: Response,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    company_id: int | None = Query(default=None, ge=1, description="Filtra pelos jobs de uma empresa"),
    status: Literal["queued", "running", "done", "failed", "cancelled"] | None = Query(default=None, description="Filtra por status"),
    ctx: AuthContext = Depends(require_admin),
    service: AdminService = Depends(admin_service),
) -> list[AdminJob]:
    current_event().add(company_id=ctx.company_id, role=ctx.role, filter_company_id=company_id, status_filter=status)
    response.headers["X-Total-Count"] = str(service.count_all_jobs(company_id=company_id, status=status))
    rows = service.list_all_jobs(limit=limit, offset=offset, company_id=company_id, status=status)
    return [AdminJob.model_validate(row) for row in rows]
