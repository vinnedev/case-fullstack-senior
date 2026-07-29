import unicodedata
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints


# Texto de negócio: espaços nas pontas são ruído; só-espaços é entrada inválida.
def _reject_control_characters(value: str) -> str:
    if any(unicodedata.category(char) == "Cc" for char in value):
        raise ValueError("caracteres de controle não são permitidos")
    return value


def _require_visible_content(value: str) -> str:
    if not value.strip():
        raise ValueError("o texto deve conter conteúdo além de espaços")
    return value


JobKind = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
    AfterValidator(_reject_control_characters),
]
IdempotencyKey = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    AfterValidator(_reject_control_characters),
]
JobSearch = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
JobPayload = Annotated[
    str,
    StringConstraints(min_length=1),
    AfterValidator(_reject_control_characters),
    AfterValidator(_require_visible_content),
]
PositiveId = Annotated[int, Field(gt=0, description="Identificador (inteiro positivo)")]
AttemptCount = Annotated[int, Field(ge=0, le=3, description="Tentativas de processamento consumidas (máximo 3)")]

# Enum no contrato: clientes gerados ganham union type em vez de string livre.
JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]
JOB_STATUSES = ("queued", "running", "done", "failed", "cancelled")


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobSummary(ApiModel):
    """Item da listagem paginada de jobs da empresa autenticada."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"id": 42, "kind": "report", "status": "done", "created_at": "2026-07-27T12:00:00Z", "result_count": 1}]
        }
    )

    id: PositiveId = Field(description="Identificador do job")
    kind: JobKind = Field(description="Tipo do job informado na criação")
    status: JobStatus = Field(description="Estado atual do job", examples=list(JOB_STATUSES))
    created_at: datetime = Field(description="Momento da criação (UTC)")
    result_count: int = Field(ge=0, le=1, description="0 ou 1 — um job tem no máximo um resultado")


class AuditEvent(ApiModel):
    """Evento de auditoria do ciclo de vida do job."""

    event_type: Literal["submitted", "cancelled", "retry_requested", "completed", "failed"]
    actor: str = Field(description="Principal autenticado no formato canônico empresa:role")
    occurred_at: datetime = Field(description="Momento do evento (UTC)")
    trace_id: str | None = Field(description="Identificador de correlação da operação")


class CancellationAudit(ApiModel):
    """Evento de cancelamento do job."""

    cancelled_by: str = Field(description="Principal autenticado no formato canônico empresa:role")
    cancelled_at: datetime = Field(description="Momento da solicitação de cancelamento (UTC)")


class JobDetail(ApiModel):
    """Detalhe do job, incluindo diagnóstico de falhas."""

    id: PositiveId = Field(description="Identificador do job")
    company_id: PositiveId = Field(description="Empresa dona do job (sempre a do contexto autenticado)")
    kind: JobKind = Field(description="Tipo do job informado na criação")
    status: JobStatus = Field(description="Estado atual do job")
    attempts: AttemptCount = Field(description="Tentativas de processamento consumidas (máximo 3)")
    # sem default: o campo está SEMPRE na resposta; o que varia é o valor ser null
    last_error: str | None = Field(description="Último erro registrado pelo worker; `null` se nunca falhou")
    cancellation: CancellationAudit | None = Field(description="Auditoria do cancelamento; `null` se o job não foi cancelado")
    audit_events: list[AuditEvent] = Field(description="Eventos auditáveis do ciclo de vida do job")


class JobResultOut(ApiModel):
    """Resultado gravado pelo worker ao concluir o job."""

    payload: JobPayload = Field(description="Conteúdo produzido pelo processamento", examples=["resultado sensível da empresa 1"])


class NewJob(ApiModel):
    """Corpo da criação de job. Exige também o header `Idempotency-Key`."""

    kind: JobKind = Field(description="Tipo do job a processar (1–100 caracteres, não pode ser só espaços)", examples=["report"])


class JobCreated(ApiModel):
    """Job criado — ou o job original, em replay da mesma `Idempotency-Key`."""

    id: PositiveId = Field(description="Identificador do job criado")
    status: Literal["queued"] = Field(description="Sempre `queued` na criação", examples=["queued"])


class JobCancelled(ApiModel):
    """Confirmação do cancelamento."""

    id: PositiveId = Field(description="Identificador do job cancelado")
    status: Literal["cancelled"] = Field(description="Sempre `cancelled`", examples=["cancelled"])


class JobRetried(ApiModel):
    """Confirmação do reenfileiramento de um job que falhou."""

    id: PositiveId = Field(description="Identificador do job reenfileirado")
    status: Literal["queued"] = Field(description="Sempre `queued`", examples=["queued"])
    attempts: AttemptCount = Field(description="Tentativas já consumidas; a partir de 3 o retry é recusado")


class AdminJob(ApiModel):
    """Job na visão administrativa (todas as empresas)."""

    id: PositiveId = Field(description="Identificador do job")
    company_id: PositiveId = Field(description="Empresa dona do job")
    status: JobStatus = Field(description="Estado atual do job")
