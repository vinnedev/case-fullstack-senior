import pytest
from fastapi import HTTPException

from modules.auth.context import AuthContext, current_ctx


def test_valid_header_returns_typed_context():
    ctx = current_ctx("1:admin")
    assert isinstance(ctx, AuthContext)
    assert ctx.company_id == 1
    assert ctx.role == "admin"


def test_missing_header_rejected():
    with pytest.raises(HTTPException) as exc:
        current_ctx(None)
    assert exc.value.status_code == 401


def test_header_without_separator_rejected():
    with pytest.raises(HTTPException) as exc:
        current_ctx("admin")
    assert exc.value.status_code == 401


def test_non_numeric_company_rejected():
    with pytest.raises(HTTPException) as exc:
        current_ctx("abc:admin")
    assert exc.value.status_code == 401


def test_empty_role_rejected():
    with pytest.raises(HTTPException) as exc:
        current_ctx("1:")
    assert exc.value.status_code == 401


def test_unknown_role_rejected():
    with pytest.raises(HTTPException) as exc:
        current_ctx("1:banana")
    assert exc.value.status_code == 401


@pytest.mark.parametrize("header", [" 1:user", "1 :user", "+1:user", "01:user", "1.0:user", "١:user"])
def test_non_canonical_company_id_rejected(header):
    with pytest.raises(HTTPException) as exc:
        current_ctx(header)
    assert exc.value.status_code == 401


def test_context_is_immutable():
    from pydantic import ValidationError

    ctx = current_ctx("2:user")
    with pytest.raises(ValidationError):
        ctx.company_id = 3
