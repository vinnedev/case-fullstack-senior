ALTER TABLE jobs ADD CONSTRAINT ck_jobs_status
  CHECK (status IN ('queued', 'running', 'done', 'failed'));
ALTER TABLE job_results ADD CONSTRAINT uq_job_results_job_id UNIQUE (job_id);
DROP INDEX ix_job_results_job_id;
ALTER TABLE job_results DROP CONSTRAINT job_results_job_id_fkey;
ALTER TABLE job_results ADD CONSTRAINT job_results_job_id_fkey
  FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE;
ALTER TABLE users ADD CONSTRAINT uq_users_email UNIQUE (email);
