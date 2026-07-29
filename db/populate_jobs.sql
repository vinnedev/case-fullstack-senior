-- Carga de dados para reproduzir o benchmark de listagem.
-- Os jobs são concluídos sem resultado para manter o foco no volume da fila.
INSERT INTO jobs (company_id, kind, status)
SELECT 1, 'report', 'done'
FROM generate_series(1, 20000);
