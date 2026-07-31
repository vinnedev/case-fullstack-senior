-- O saneamento de dados do up é one-way (não há como restaurar os valores
-- originais); este down remove apenas as constraints e restaura a FK simples.
ALTER TABLE companies DROP CONSTRAINT ck_companies_name_content;
ALTER TABLE companies DROP CONSTRAINT ck_companies_max_concurrent_jobs;
ALTER TABLE users DROP CONSTRAINT ck_users_email_content;
ALTER TABLE users DROP CONSTRAINT ck_users_role;
ALTER TABLE jobs DROP CONSTRAINT ck_jobs_kind_content;
ALTER TABLE jobs DROP CONSTRAINT ck_jobs_attempts;
ALTER TABLE jobs DROP CONSTRAINT ck_jobs_idempotency_key;
ALTER TABLE jobs DROP CONSTRAINT fk_jobs_creator_company;
ALTER TABLE jobs ADD CONSTRAINT jobs_created_by_fkey
  FOREIGN KEY (created_by) REFERENCES users(id);
ALTER TABLE users DROP CONSTRAINT uq_users_id_company;
ALTER TABLE job_results DROP CONSTRAINT ck_job_results_payload_content;
ALTER TABLE dead_letter_jobs DROP CONSTRAINT ck_dead_letter_jobs_kind_content;
ALTER TABLE dead_letter_jobs DROP CONSTRAINT ck_dead_letter_jobs_attempts;
DROP INDEX uq_dead_letter_jobs_job_id;
