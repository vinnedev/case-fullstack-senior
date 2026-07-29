-- Invariantes que também precisam sobreviver a imports, scripts e bugs na aplicação.
-- job_quota permanece sem piso: neste case ela é um contador de consumo e pode ficar negativa.

-- Saneamento antes das constraints: bases já em uso podem conter dado gravado
-- quando a validação ainda não existia (ex.: kind só com espaços). Sem este
-- backfill a migration falharia em qualquer ambiente com histórico.
UPDATE companies SET name = btrim(regexp_replace(name, '[[:cntrl:]]', '', 'g'));
UPDATE companies SET name = 'empresa ' || id WHERE name !~ '[^[:space:]]';
UPDATE companies SET max_concurrent_jobs = 1 WHERE max_concurrent_jobs < 1;

UPDATE users SET email = btrim(regexp_replace(email, '[[:cntrl:]]', '', 'g'));
UPDATE users SET email = 'user' || id || '@invalido.local' WHERE email !~ '[^[:space:]]';
UPDATE users SET role = 'user' WHERE role NOT IN ('user', 'admin');

UPDATE jobs SET kind = left(btrim(regexp_replace(kind, '[[:cntrl:]]', '', 'g')), 100);
UPDATE jobs SET kind = 'desconhecido' WHERE kind !~ '[^[:space:]]';
UPDATE jobs SET attempts = least(greatest(attempts, 0), 3) WHERE attempts NOT BETWEEN 0 AND 3;
UPDATE jobs SET idempotency_key = NULL
  WHERE idempotency_key IS NOT NULL
    AND (
      idempotency_key !~ '[^[:space:]]'
      OR idempotency_key ~ '[[:cntrl:]]'
      OR char_length(idempotency_key) > 200
    );

UPDATE job_results SET payload = regexp_replace(payload, '[[:cntrl:]]', '', 'g');
DELETE FROM job_results WHERE payload !~ '[^[:space:]]';

UPDATE dead_letter_jobs SET kind = left(btrim(regexp_replace(kind, '[[:cntrl:]]', '', 'g')), 100);
UPDATE dead_letter_jobs SET kind = 'desconhecido' WHERE kind !~ '[^[:space:]]';
UPDATE dead_letter_jobs SET attempts = least(greatest(attempts, 1), 3) WHERE attempts NOT BETWEEN 1 AND 3;

-- Entradas órfãs impedem a FK composta (created_by, company_id)
UPDATE jobs j SET created_by = NULL
  WHERE created_by IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = j.created_by AND u.company_id = j.company_id);

-- Duplicatas impedem o índice único de job_id na DLQ (mantém a entrada mais recente)
DELETE FROM dead_letter_jobs d
  WHERE job_id IS NOT NULL
    AND EXISTS (
      SELECT 1 FROM dead_letter_jobs o
      WHERE o.job_id = d.job_id AND (o.failed_at, o.id) > (d.failed_at, d.id)
    );
ALTER TABLE companies ADD CONSTRAINT ck_companies_name_content
  CHECK (name ~ '[^[:space:]]' AND name !~ '[[:cntrl:]]');
ALTER TABLE companies ADD CONSTRAINT ck_companies_max_concurrent_jobs
  CHECK (max_concurrent_jobs > 0);

ALTER TABLE users ADD CONSTRAINT ck_users_email_content
  CHECK (email ~ '[^[:space:]]' AND email !~ '[[:cntrl:]]');
ALTER TABLE users ADD CONSTRAINT ck_users_role
  CHECK (role IN ('user', 'admin'));
ALTER TABLE users ADD CONSTRAINT uq_users_id_company UNIQUE (id, company_id);

ALTER TABLE jobs ADD CONSTRAINT ck_jobs_kind_content
  CHECK (
    char_length(kind) BETWEEN 1 AND 100
    AND kind ~ '[^[:space:]]'
    AND kind !~ '[[:cntrl:]]'
  );
ALTER TABLE jobs ADD CONSTRAINT ck_jobs_attempts
  CHECK (attempts BETWEEN 0 AND 3);
ALTER TABLE jobs ADD CONSTRAINT ck_jobs_idempotency_key
  CHECK (
    idempotency_key IS NULL
    OR (
      char_length(idempotency_key) BETWEEN 1 AND 200
      AND idempotency_key ~ '[^[:space:]]'
      AND idempotency_key !~ '[[:cntrl:]]'
    )
  );
ALTER TABLE jobs DROP CONSTRAINT jobs_created_by_fkey;
ALTER TABLE jobs ADD CONSTRAINT fk_jobs_creator_company
  FOREIGN KEY (created_by, company_id) REFERENCES users(id, company_id);

ALTER TABLE job_results ADD CONSTRAINT ck_job_results_payload_content
  CHECK (payload ~ '[^[:space:]]' AND payload !~ '[[:cntrl:]]');

ALTER TABLE dead_letter_jobs ADD CONSTRAINT ck_dead_letter_jobs_kind_content
  CHECK (
    char_length(kind) BETWEEN 1 AND 100
    AND kind ~ '[^[:space:]]'
    AND kind !~ '[[:cntrl:]]'
  );
ALTER TABLE dead_letter_jobs ADD CONSTRAINT ck_dead_letter_jobs_attempts
  CHECK (attempts BETWEEN 1 AND 3);
CREATE UNIQUE INDEX uq_dead_letter_jobs_job_id
  ON dead_letter_jobs (job_id)
  WHERE job_id IS NOT NULL;
