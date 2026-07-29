ALTER TABLE jobs ADD COLUMN idempotency_key TEXT;
CREATE UNIQUE INDEX uq_jobs_company_idempotency_key
  ON jobs (company_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;
