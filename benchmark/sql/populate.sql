-- Carga da empresa de benchmark (id 900) com mix de status, para os cenários
-- exercitarem listagem em volume, retry real, cancel e corridas.
-- Cada bloco é um top-up idempotente: completa até a meta, nunca duplica.

-- Base de volume: 100k jobs concluídos (mede listagem/paginação/count).
INSERT INTO jobs (company_id, kind, status, created_at)
SELECT 900, 'report', 'done', now() - (g || ' seconds')::interval
FROM generate_series(
    1,
    GREATEST(0, 100000 - (SELECT count(*) FROM jobs WHERE company_id = 900 AND status = 'done'))::int
) AS g;

-- Failed retryável (attempts 1..2): alvo dos cenários de retry e do caos.
INSERT INTO jobs (company_id, kind, status, attempts, last_error, created_at)
SELECT 900, 'report', 'failed', 1 + (g % 2),
       'BenchmarkInjectedFailure: falha semeada para exercitar retry',
       now() - (g || ' seconds')::interval
FROM generate_series(
    1,
    GREATEST(0, 1500 - (SELECT count(*) FROM jobs WHERE company_id = 900 AND status = 'failed' AND attempts < 3))::int
) AS g;

-- Failed esgotado (attempts = 3): retry deve responder 409.
INSERT INTO jobs (company_id, kind, status, attempts, last_error, created_at)
SELECT 900, 'report', 'failed', 3,
       'BenchmarkInjectedFailure: tentativas esgotadas',
       now() - (g || ' seconds')::interval
FROM generate_series(
    1,
    GREATEST(0, 300 - (SELECT count(*) FROM jobs WHERE company_id = 900 AND status = 'failed' AND attempts >= 3))::int
) AS g;

-- Cancelled: cancel e retry devem responder 409.
INSERT INTO jobs (company_id, kind, status, created_at)
SELECT 900, 'report', 'cancelled', now() - (g || ' seconds')::interval
FROM generate_series(
    1,
    GREATEST(0, 1000 - (SELECT count(*) FROM jobs WHERE company_id = 900 AND status = 'cancelled'))::int
) AS g;

ANALYZE jobs;
