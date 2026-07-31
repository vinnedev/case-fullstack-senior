-- migration: non-transactional
-- Separa jobs prontos dos agendados sem predicados dependentes de now().
DROP INDEX CONCURRENTLY IF EXISTS ix_jobs_queued_ready;
-- migration: next-statement
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_jobs_queued_ready
  ON jobs (id)
  WHERE status = 'queued' AND next_attempt_at IS NULL;
-- migration: next-statement
DROP INDEX CONCURRENTLY IF EXISTS ix_jobs_queued_scheduled;
-- migration: next-statement
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_jobs_queued_scheduled
  ON jobs (next_attempt_at, id)
  WHERE status = 'queued' AND next_attempt_at IS NOT NULL;
