BEGIN;

INSERT INTO companies (id, name, max_concurrent_jobs, job_quota) VALUES
  (1, 'Acme', 2, 20),
  (2, 'Globex', 2, 100)
ON CONFLICT (id) DO NOTHING;

INSERT INTO users (id, company_id, email, role) VALUES
  (1, 1, 'user@acme.test', 'user'),
  (2, 1, 'admin@acme.test', 'admin'),
  (3, 2, 'user@globex.test', 'user'),
  (4, 2, 'admin@globex.test', 'admin')
ON CONFLICT (id) DO NOTHING;

INSERT INTO jobs (company_id, created_by, kind, status, idempotency_key)
SELECT 1, 1, 'report', 'done', format('seed-acme-done-%s', n)
FROM generate_series(1, 12) AS s(n)
ON CONFLICT DO NOTHING;

INSERT INTO jobs (company_id, created_by, kind, status, idempotency_key) VALUES
  (1, 1, 'report', 'failed', 'seed-acme-failed-report'),
  (1, 1, 'import', 'failed', 'seed-acme-failed-import'),
  (1, 1, 'report', 'running', 'seed-acme-running')
ON CONFLICT DO NOTHING;

INSERT INTO jobs (company_id, created_by, kind, status, idempotency_key)
SELECT 2, 3, 'report', 'done', format('seed-globex-done-%s', n)
FROM generate_series(1, 12) AS s(n)
ON CONFLICT DO NOTHING;

INSERT INTO job_results (job_id, payload)
SELECT j.id, 'resultado sensível da empresa ' || j.company_id
FROM jobs AS j
WHERE j.idempotency_key LIKE 'seed-%' AND j.status = 'done'
ON CONFLICT (job_id) DO NOTHING;

INSERT INTO job_audit_events (job_id, company_id, event_type, actor, occurred_at, trace_id)
SELECT j.id, j.company_id, 'submitted', 'seed:system', j.created_at, j.trace_id
FROM jobs AS j
WHERE j.idempotency_key LIKE 'seed-%'
ON CONFLICT DO NOTHING;

SELECT setval('companies_id_seq', COALESCE((SELECT max(id) FROM companies), 1), true);
SELECT setval('users_id_seq', COALESCE((SELECT max(id) FROM users), 1), true);
SELECT setval('jobs_id_seq', COALESCE((SELECT max(id) FROM jobs), 1), true);
SELECT setval('job_results_id_seq', COALESCE((SELECT max(id) FROM job_results), 1), true);

COMMIT;
