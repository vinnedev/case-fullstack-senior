# state.md — Alteração da modelagem do banco

## Contexto

A migration `0001_initial_schema` reproduz fielmente a modelagem original do case
(`db/schema.sql`), acrescida apenas de índices dirigidos pelas queries. A migration
`0002_integrity_constraints` evolui essa modelagem com constraints de integridade.
Este documento respalda cada mudança.

## Mudanças

### 1. `jobs.status`: de TEXT livre para TEXT com CHECK

**De:** `status TEXT NOT NULL DEFAULT 'queued'` — aceita qualquer string.
**Para:** mesmo tipo, com `CHECK (status IN ('queued', 'running', 'done', 'failed'))`.

**Motivos:**
- Todo o código (API, worker, frontend) assume exatamente esses 4 estados; um typo
  em um UPDATE criaria um job invisível para o worker (nunca `queued`) e para os
  filtros da API, sem nenhum erro — o CHECK transforma esse bug silencioso em erro
  imediato no banco.
- CHECK em vez de ENUM nativo: alterar a lista de estados vira uma migration
  trivial (`DROP/ADD CONSTRAINT`), enquanto `ALTER TYPE ... ADD VALUE` tem
  limitações transacionais e não permite remoção de valores.
- Custo: validação O(1) por escrita, sem impacto de RAM ou leitura.

### 2. `job_results.job_id`: de FK N:1 para 1:1 (`UNIQUE`)

**De:** `job_id INT NOT NULL REFERENCES jobs(id)` + índice não-único — permite
vários resultados para o mesmo job.
**Para:** `job_id` com `UNIQUE` (que substitui o índice, ver nota abaixo).

**Motivos:**
- O domínio é 1:1: o worker grava exatamente um resultado por job, e
  `GET /jobs/{id}/result` retorna um único payload (`scalar_one_or_none`). Se um
  retry do worker inserisse um segundo resultado, a API retornaria um dos dois de
  forma não-determinística. O UNIQUE garante o invariante no único lugar que não
  tem race condition: o banco.
- Elimina a necessidade do count no `GET /jobs` ser "count" — hoje `result_count`
  só pode ser 0 ou 1, e a constraint documenta isso no schema.
- **Nota de custo:** UNIQUE em Postgres é implementado como índice B-tree único —
  o índice `ix_job_results_job_id` foi removido na mesma migration porque seria
  100% redundante. Saldo de RAM/CPU: neutro (troca um índice por outro).

### 3. `job_results.job_id`: FK com `ON DELETE CASCADE`

**De:** FK sem ação de deleção (deletar um job com resultado → erro de FK).
**Para:** `ON DELETE CASCADE`.

**Motivos:**
- `job_results` é dado dependente sem ciclo de vida próprio: não existe resultado
  sem job. Cascade permite expurgo de jobs antigos (retenção/LGPD) com um único
  `DELETE FROM jobs`, sem ordenação manual de deletes na aplicação.
- Custo: nenhum em operação normal; o cascade usa o índice único do item 2.

### 4. `users.email`: `UNIQUE`

**De:** `email TEXT NOT NULL` — permite dois usuários com o mesmo e-mail.
**Para:** `UNIQUE`.

**Motivos:**
- E-mail é o identificador natural de login; quando a autenticação real substituir
  o header `X-Auth`, e-mail duplicado tornaria o lookup de login ambíguo. Corrigir
  dados duplicados depois de existirem é muito mais caro do que impedir agora.
- Custo: um índice B-tree em tabela minúscula (dezenas/centenas de linhas) — e o
  índice ainda acelera o futuro lookup por e-mail.

## O que deliberadamente NÃO mudou

- **Tipos das colunas** (`TEXT`, `INT`, `TIMESTAMPTZ`) e nomes de tabelas/colunas:
  compatibilidade total com API, worker, seed e frontend existentes.
- **`SERIAL` como PK**: `IDENTITY` seria o padrão moderno, mas a troca não traz
  benefício funcional e tocaria todas as sequences — risco sem retorno.
- **Sem novos índices**: a análise de queries (ver README, seção de índices) já
  cobriu todos os caminhos de leitura; constraints daqui existem por integridade,
  não por performance.

## Verificação

- `0002` aplicada sobre banco com seed (dados reais do case) sem violações.
- Suite de testes das duas stacks (api e worker) verde após a mudança.
- `EXPLAIN` re-executado: `GET /jobs/{id}/result` passou a usar o índice único
  `uq_job_results_job_id` (mesmo plano, índice diferente).
- Rollback testado: `alembic downgrade -1` restaura a modelagem da `0001`.
