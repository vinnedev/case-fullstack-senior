ALTER TABLE users DROP CONSTRAINT uq_users_email;
ALTER TABLE job_results DROP CONSTRAINT job_results_job_id_fkey;
ALTER TABLE job_results ADD CONSTRAINT job_results_job_id_fkey
  FOREIGN KEY (job_id) REFERENCES jobs(id);
CREATE INDEX ix_job_results_job_id ON job_results (job_id);
ALTER TABLE job_results DROP CONSTRAINT uq_job_results_job_id;
ALTER TABLE jobs DROP CONSTRAINT ck_jobs_status;
