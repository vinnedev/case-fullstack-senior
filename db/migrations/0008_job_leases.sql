-- Lease persistente do worker. Um processo que morre depois do claim deixa
-- evidência suficiente para outro worker recuperar o job após o timeout.
ALTER TABLE jobs ADD COLUMN locked_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN worker_id TEXT;

CREATE INDEX ix_jobs_running_lease
  ON jobs (locked_at)
  WHERE status = 'running';
