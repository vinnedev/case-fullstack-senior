-- O domínio antigo não conhece 'cancelled': o dado é mapeado para 'failed'
-- (com nota em last_error) antes de restaurar a constraint original.
UPDATE jobs
SET status = 'failed',
    last_error = COALESCE(last_error, 'status cancelled revertido pela migration down 0004')
WHERE status = 'cancelled';
ALTER TABLE jobs DROP CONSTRAINT ck_jobs_status;
ALTER TABLE jobs ADD CONSTRAINT ck_jobs_status
  CHECK (status IN ('queued', 'running', 'done', 'failed'));
