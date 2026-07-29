from typing import Literal

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, ValidationError

# Esquema de segurança (não "parâmetro opcional"): o OpenAPI passa a declarar a
# credencial como exigência da operação e o Swagger ganha o botão Authorize.
# auto_error=False para respondermos 401 (credencial ausente), não o 403 padrão.
x_auth_scheme = APIKeyHeader(
    name="X-Auth",
    scheme_name="X-Auth",
    description=(
        "**Obrigatório.** Contexto de autenticação no formato `<company_id>:<role>`, "
        "com role `user` ou `admin` — ex.: `1:user`, `2:admin`. "
        "Simplificação do case: sem assinatura criptográfica. "
        "Ausente ou malformado → `401`."
    ),
    auto_error=False,
)

ASCII_ZERO = ord("0")
ASCII_NINE = ord("9")


class AuthContext(BaseModel):
    model_config = {"frozen": True}

    company_id: int = Field(gt=0)
    role: Literal["user", "admin"]


def current_ctx(x_auth: str | None = Security(x_auth_scheme)) -> AuthContext:
    # formato: "<company_id>:<role>"  (sem assinatura — simplificação do case)
    if not x_auth or ":" not in x_auth:
        raise HTTPException(401, "missing X-Auth")

    company_id, role = x_auth.split(":", 1)
    canonical_company_id = bool(company_id) and all(ASCII_ZERO <= ord(char) <= ASCII_NINE for char in company_id) and company_id[0] != "0"
    if not canonical_company_id:
        raise HTTPException(401, "invalid X-Auth")

    try:
        return AuthContext.model_validate({"company_id": company_id, "role": role})
    except ValidationError:
        raise HTTPException(401, "invalid X-Auth") from None
