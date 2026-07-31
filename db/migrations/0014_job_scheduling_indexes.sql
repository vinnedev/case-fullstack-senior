-- Separa jobs prontos dos agendados sem predicados dependentes de now().
CREATE INDEX ix_jobs_queued_ready
  ON jobs (id)
  WHERE status = 'queued' AND next_attempt_at IS NULL;

CREATE INDEX ix_jobs_queued_scheduled
  ON jobs (next_attempt_at, id)
  WHERE status = 'queued' AND next_attempt_at IS NOT NULL;
