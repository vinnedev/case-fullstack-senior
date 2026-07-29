import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import make_asgi_app

from modules.admin.routes import router as admin_router
from modules.jobs.routes import router as jobs_router
from shared.config.settings import get_settings
from shared.db.pool import dispose_pool
from shared.http.docs import install_branded_docs
from shared.http.graceful_shutdown import GracefulShutdown
from shared.logging.access_log import install_probe_route_access_filter
from shared.logging.wide_event import WideEvent, start_event
from shared.observability.api_metrics import http_request_duration_seconds, http_requests_in_flight

SERVICE = "api"
SHUTDOWN_DRAIN_TIMEOUT = 30.0
PROBE_LOG_PATHS = frozenset({"/health", "/healthz", "/ready", "/readyz", "/live", "/livez"})

shutdown = GracefulShutdown(SERVICE)


def is_probe_route(path: str) -> bool:
    return path in PROBE_LOG_PATHS or path == "/metrics" or path.startswith("/metrics/")


def should_emit_request_log(path: str, suppress_probe_routes: bool, status: int = 200) -> bool:
    """Rotas de scrape/health poluem o Grafana com ruído sem valor de negócio.

    A supressão é opt-in por env e vale apenas para respostas bem-sucedidas:
    um /health ou /metrics quebrado continua logando (é exatamente o momento
    em que o log importa).
    """
    if not suppress_probe_routes or status >= 400:
        return True
    return not is_probe_route(path)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    if get_settings().log_suppress_probe_routes:
        # o access log do uvicorn é um canal separado do wide event: a mesma
        # flag precisa silenciar os dois, senão o ruído continua no Grafana
        install_probe_route_access_filter(is_probe_route)
    WideEvent(SERVICE, "startup").emit()
    yield
    event = WideEvent(SERVICE, "shutdown")
    await shutdown.drain(SHUTDOWN_DRAIN_TIMEOUT)
    dispose_pool()
    event.add(drained=True, db_pool_disposed=True).emit()


API_DESCRIPTION = """
API do **Relay**: submissão e acompanhamento de jobs assíncronos por empresa (multi-tenant).

## Autenticação

Todas as rotas exigem o header **`X-Auth`** (obrigatório — sem ele a resposta é `401`)
no formato `<company_id>:<role>`, ex.: `1:user` ou `2:admin`.
Simplificação do case — sem assinatura. Todo acesso é isolado por empresa:
recursos de outro tenant respondem `404`.

## Ciclo de vida do job

`queued` → `running` → `done` &nbsp;|&nbsp; `failed` (após 3 tentativas) &nbsp;|&nbsp; `cancelled`

1. `POST /jobs` cria um job `queued` (sujeito ao limite de concorrência da empresa — `429`).
2. O worker processa a fila e grava o resultado (um por job).
3. `GET /jobs/{id}` acompanha status/tentativas/último erro; `GET /jobs/{id}/result` lê o payload.
4. `POST /jobs/{id}/cancel` cancela em `queued`/`running`; `POST /jobs/{id}/retry` reenfileira um `failed`.

## Idempotência

O header **`Idempotency-Key`** no `POST /jobs` é **obrigatório** (`422` se ausente):
repetir a mesma chave (por empresa) devolve o job original em vez de criar outro —
proteção contra duplo-clique e retry de rede. Cancel e retry são
idempotentes por estado: a segunda chamada responde `409`.

## Erros

| Código | Quando |
|---|---|
| `401` | `X-Auth` ausente/inválido ou empresa desconhecida |
| `403` | Rota admin sem role `admin` |
| `404` | Job inexistente **ou de outra empresa** (não vazamos existência) |
| `409` | Transição de estado inválida (cancelar `done`, retry sem ser `failed`, máximo de tentativas) |
| `422` | Validação: tipos/formatos/faixas errados, com o campo apontado em `detail` |
| `429` | Limite de jobs concorrentes da empresa atingido |
"""

TAGS_METADATA = [
    {"name": "jobs", "description": "Criação e consulta de jobs da empresa autenticada."},
    {"name": "admin", "description": "Visão administrativa de todos os jobs."},
]

app = FastAPI(
    title="Relay API",
    version="0.1.0",
    summary="Processamento de jobs em background, multi-tenant.",
    description=API_DESCRIPTION,
    openapi_tags=TAGS_METADATA,
    contact={"name": "Relay · Galaxies", "url": "https://www.galaxies.com.br"},
    lifespan=lifespan,
    docs_url=None,
    swagger_ui_parameters={
        "docExpansion": "list",
        "defaultModelsExpandDepth": 0,
        "displayRequestDuration": True,
        "tryItOutEnabled": True,
    },
)
install_branded_docs(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["X-Auth", "Content-Type", "Idempotency-Key"],
    expose_headers=["X-Total-Count"],
    max_age=600,
)


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.middleware("http")
async def request_lifecycle_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    if not await shutdown.request_started():
        return JSONResponse({"detail": "servidor encerrando"}, status_code=503, headers={"Connection": "close"})
    event = start_event(
        SERVICE,
        "http_request",
        request_id=str(uuid.uuid4()),
        http_method=request.method,
        http_path=request.url.path,
    )
    started = time.perf_counter()
    status = 500
    http_requests_in_flight.inc()
    try:
        response = await call_next(request)
        status = response.status_code
        event.add(http_status=status)
        if status >= 500:
            event.error()
        return response
    except Exception as exc:
        event.error(exc, http_status=500)
        raise
    finally:
        if not request.url.path.startswith("/metrics"):
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            http_request_duration_seconds.labels(method=request.method, route=route_path, status=str(status)).observe(
                time.perf_counter() - started
            )
        http_requests_in_flight.dec()
        if should_emit_request_log(request.url.path, get_settings().log_suppress_probe_routes, status):
            event.emit()
        await shutdown.request_finished()


app.include_router(jobs_router)
app.include_router(admin_router)
app.mount("/metrics", make_asgi_app())
