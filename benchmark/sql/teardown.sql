-- Remove todos os dados da empresa de benchmark, deixando as empresas demo intactas.
DELETE FROM job_audit_events WHERE company_id >= 900;
DELETE FROM dead_letter_jobs WHERE company_id >= 900;
DELETE FROM job_results r USING jobs j WHERE r.job_id = j.id AND j.company_id >= 900;
DELETE FROM jobs WHERE company_id >= 900;
DELETE FROM companies WHERE id >= 900;
