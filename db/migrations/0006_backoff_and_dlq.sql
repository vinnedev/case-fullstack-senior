-- Backoff entre tentativas: o worker só reivindica quando next_attempt_at vence
ALTER TABLE jobs ADD COLUMN next_attempt_at TIMESTAMPTZ;

-- Dead Letter Queue: registro imutável de jobs que esgotaram as tentativas.
-- Colunas desnormalizadas de propósito: a auditoria sobrevive a expurgo de jobs.
CREATE TABLE dead_letter_jobs (
  id SERIAL PRIMARY KEY,
  job_id INT REFERENCES jobs(id) ON DELETE SET NULL,
  company_id INT NOT NULL,
  kind TEXT NOT NULL,
  attempts INT NOT NULL,
  last_error TEXT,
  trace_id TEXT,
  job_created_at TIMESTAMPTZ,
  failed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_dlq_failed_at ON dead_letter_jobs (failed_at DESC);
CREATE INDEX ix_dlq_company ON dead_letter_jobs (company_id);
