ALTER TABLE jobs DROP CONSTRAINT ck_jobs_status;
ALTER TABLE jobs ADD CONSTRAINT ck_jobs_status
  CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled'));
