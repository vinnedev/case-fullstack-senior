"""Métricas do processo da API. Espelhado entre api e worker (só a API importa).

Labels somente de baixa cardinalidade — nunca IDs nem input de usuário.
"""

from prometheus_client import Counter, Gauge, Histogram

HTTP_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "Duração das requests HTTP",
    labelnames=("method", "route", "status"),
    buckets=HTTP_LATENCY_BUCKETS,
)

http_requests_in_flight = Gauge(
    "http_requests_in_flight",
    "Requests em andamento",
)

# sem label "kind": é input do usuário (cardinalidade não-limitada)
jobs_created_total = Counter(
    "jobs_created_total",
    "Jobs criados via API",
)

db_pool_size = Gauge("api_db_pool_size", "Conexões atualmente abertas no pool da API")
db_pool_available = Gauge("api_db_pool_available", "Conexões livres no pool da API")
db_pool_max = Gauge("api_db_pool_max", "Limite máximo de conexões do pool da API")
db_pool_requests_waiting = Gauge("api_db_pool_requests_waiting", "Requests aguardando conexão no pool da API")
db_pool_utilization_ratio = Gauge(
    "api_db_pool_utilization_ratio",
    "Utilização do pool da API",
)


def _pool_stat(name: str) -> float:
    from shared.db.pool import get_pool

    return float(get_pool().get_stats().get(name, 0))


def _pool_utilization() -> float:
    pool_size = _pool_stat("pool_size")
    pool_available = _pool_stat("pool_available")
    return (pool_size - pool_available) / pool_size if pool_size else 0.0


db_pool_size.set_function(lambda: _pool_stat("pool_size"))
db_pool_available.set_function(lambda: _pool_stat("pool_available"))
db_pool_max.set_function(lambda: _pool_stat("pool_max"))
db_pool_requests_waiting.set_function(lambda: _pool_stat("requests_waiting"))
db_pool_utilization_ratio.set_function(_pool_utilization)
