-- A busca de GET /jobs usa kind ILIKE '%termo%' (substring). Full-text search
-- (tsvector) não cobre substring arbitrária — o índice correto é trigram.
-- Medido em 100k linhas: termo sem match caiu de 32ms (seq scan) para 0,02ms;
-- termo que casa tudo segue no índice (company_id, created_at) com o
-- early-stop do LIMIT, o planner escolhe por seletividade.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
