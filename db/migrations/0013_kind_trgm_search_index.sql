-- migration: non-transactional
-- O índice é criado fora de transação para não bloquear escritas na tabela jobs.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_jobs_kind_trgm
    ON jobs USING gin (kind gin_trgm_ops);
