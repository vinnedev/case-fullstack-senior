-- Empresa dedicada ao benchmark, isolada das empresas demo (Acme/Globex).
-- Limites altos para o gargalo medido ser o sistema, não a regra de negócio.
INSERT INTO companies (id, name, max_concurrent_jobs, job_quota)
VALUES (900, 'Benchmark', 100000, 10000000)
ON CONFLICT (id) DO UPDATE
SET max_concurrent_jobs = EXCLUDED.max_concurrent_jobs,
    job_quota = EXCLUDED.job_quota;

SELECT setval(
    pg_get_serial_sequence('companies', 'id'),
    GREATEST(900, (SELECT max(id) FROM companies))
);
