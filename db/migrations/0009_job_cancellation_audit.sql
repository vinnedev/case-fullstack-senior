CREATE TABLE job_cancellation_audits (
  id BIGSERIAL PRIMARY KEY,
  job_id INT NOT NULL UNIQUE REFERENCES jobs(id),
  company_id INT NOT NULL REFERENCES companies(id),
  cancelled_by TEXT NOT NULL,
  cancelled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_job_cancellation_audits_actor
    CHECK (char_length(cancelled_by) BETWEEN 3 AND 100 AND cancelled_by !~ '[[:cntrl:]]')
);

CREATE INDEX ix_job_cancellation_audits_company_time
  ON job_cancellation_audits (company_id, cancelled_at DESC);
