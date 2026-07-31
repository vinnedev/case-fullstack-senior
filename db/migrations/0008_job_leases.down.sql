DROP INDEX ix_jobs_running_lease;
ALTER TABLE jobs DROP COLUMN locked_at;
ALTER TABLE jobs DROP COLUMN worker_id;
