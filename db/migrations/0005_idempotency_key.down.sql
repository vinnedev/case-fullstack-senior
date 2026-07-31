DROP INDEX uq_jobs_company_idempotency_key;
ALTER TABLE jobs DROP COLUMN idempotency_key;
