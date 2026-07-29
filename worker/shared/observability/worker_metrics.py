"""Métricas do processo do worker (fluxo de processamento).

O ESTADO da fila (jobs por status / profundidade) vem do postgres-exporter
(query custom pg_jobs_total) — verdade no momento do scrape, independente do
loop do worker. Aqui ficam apenas métricas de fluxo.
"""

from prometheus_client import Counter, Histogram

JOB_DURATION_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0)

job_processing_duration_seconds = Histogram(
    "job_processing_duration_seconds",
    "Duração do processamento de jobs",
    labelnames=("outcome",),
    buckets=JOB_DURATION_BUCKETS,
)

jobs_processed_total = Counter(
    "jobs_processed_total",
    "Jobs processados pelo worker",
    labelnames=("outcome",),
)

job_queue_wait_seconds = Histogram(
    "job_queue_wait_seconds",
    "Tempo entre enfileirar e o worker reivindicar o job",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 15.0, 60.0, 300.0),
)

worker_wakeups_total = Counter(
    "worker_wakeups_total",
    "Ciclos de trabalho do worker por origem",
    labelnames=("reason",),
)
