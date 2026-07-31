-- migration: non-transactional
DROP INDEX CONCURRENTLY IF EXISTS ix_jobs_queued_scheduled;
-- migration: next-statement
DROP INDEX CONCURRENTLY IF EXISTS ix_jobs_queued_ready;
