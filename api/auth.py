from fastapi import Header, HTTPException
from pydantic import BaseModel, Field, ValidationError


class AuthContext(BaseModel):
    model_config = {"frozen": True}

    company_id: int = Field(gt=0)
    role: str = Field(min_length=1)


def current_ctx(x_auth: str | None = Header(default=None)) -> AuthContext:
    # formato: "<company_id>:<role>"  (sem assinatura — simplificação do case)
    if not x_auth or ":" not in x_auth:
        raise HTTPException(401, "missing X-Auth")
    company_id, role = x_auth.split(":", 1)
    try:
        return AuthContext(company_id=company_id, role=role)
    except ValidationError:
        raise HTTPException(401, "invalid X-Auth")
