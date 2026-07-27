# Análise do Case — Relay (Fullstack Sênior)

É uma armadilha do avaliador: quem usa IA cegamente "esquece" o Sintoma 3 (observabilidade) e entrega incompleto. Vale registrar essa descoberta no `DECISIONS.md` (seção "Uso de IA") — é exatamente o julgamento que eles avaliam.

Fora isso, **nada malicioso**: sem scripts de instalação suspeitos, sem rede externa, dependências mínimas e conhecidas (fastapi, uvicorn, psycopg, react, vite), Dockerfiles limpos, sem binários. As credenciais `relay:relay` são locais do case.

## 2. A proposta

Case sênior fullstack: serviço multi-tenant de jobs (FastAPI + Postgres + worker Python + React) **deliberadamente quebrado**.

**Entregáveis:**
- **Feature A** — cancelar job, tratando a corrida cancelar-vs-finalizar.
- **Feature B** — retry idempotente de jobs `failed` (sem duplicar resultado nem cobrar quota em dobro).
- **Diagnóstico estrutural** dos sintomas do `KNOWN_ISSUES.md` + problemas não listados.
- **`DECISIONS.md`** preenchido.

**Restrições:** SQL cru via psycopg (sem ORM); tudo rodando com `docker compose up --build`.

## 3. Problemas a achar e corrigir

### 3.1 Segurança / multi-tenancy (os mais graves — não listados no KNOWN_ISSUES)

| Problema | Onde | Correção |
|---|---|---|
| IDOR: job e resultado de outra empresa acessíveis | `api/main.py:20` (`GET /jobs/{id}`) e `api/main.py:28` (`GET /jobs/{id}/result`) — não filtram `company_id` | `WHERE id=%s AND company_id=%s` → 404 |
| `GET /admin/jobs` sem checagem de role | `api/main.py:52` — qualquer `X-Auth: 1:user` vê tudo | Exigir `role == "admin"`; decidir se admin vê só a própria empresa ou documentar como super-admin |
| `int(company_id)` sem tratamento → 500 em vez de 401 | `auth.py:7` (`X-Auth: abc:user`) | Validar e retornar 401 |
| CORS `allow_origins=["*"]` | `api/main.py` | Aceitável no case, mas anotar no DECISIONS |

### 3.2 Concorrência (Sintoma 2)

- **`POST /jobs`**: check-then-insert de `max_concurrent_jobs` sem lock → corrida estoura o limite. Corrigir com transação + `SELECT ... FOR UPDATE` na linha da company (ou advisory lock).
- **Worker** (`worker/worker.py:4-8`): claim em dois passos sem `FOR UPDATE SKIP LOCKED` → com 2+ workers, o mesmo job processa duas vezes (duplica resultado, debita quota em dobro). Correção canônica:
  ```sql
  UPDATE jobs SET status='running'
  WHERE id = (SELECT id FROM jobs WHERE status='queued'
              ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
  RETURNING id, company_id, kind;
  ```
- **Quota**: debitada mas nunca checada; pode ficar negativa. Decidir semântica (debitar na submissão, na mesma transação; rejeitar com 429/402 se zerada) e documentar.
- **Worker sem try/except**: qualquer erro derruba o processo e o job fica preso em `running` para sempre (sem recuperação de órfãos).

### 3.3 Performance (Sintoma 1)

- **N+1** em `GET /jobs` (`api/main.py:16`): um `COUNT` por job → `LEFT JOIN` + `GROUP BY` (ou `JOIN LATERAL`) numa query só.
- **Sem índices**: criar `db/migrations/` com índices em `jobs (company_id, created_at DESC)`, `job_results (job_id)` e parcial `jobs (status) WHERE status='queued'` (polling do worker).
- **Sem paginação**: `LIMIT/OFFSET` ou keyset.

### 3.4 Observabilidade (Sintoma 3 — o que a injection queria esconder)

`log()` é um `print` sem contexto; worker não registra falhas; não há caminho que marque job como `failed` nem coluna de erro.

**Solução:** logging estruturado (JSON) com `job_id`, `company_id` e `trace_id` gerado na API e propagado ao worker (coluna no job); coluna `error` em `jobs`; try/except no worker gravando `failed` + motivo.

### 3.5 Feature A — Cancelamento

- Endpoint: `POST /jobs/{id}/cancel` com transição atômica:
  ```sql
  UPDATE jobs SET status='cancelled'
  WHERE id=%s AND company_id=%s AND status IN ('queued','running')
  RETURNING id;
  ```
  0 linhas → 409.
- **Corrida**: o worker finaliza com `UPDATE ... SET status='done' WHERE id=%s AND status='running'` — quem commitar primeiro vence; o outro vê 0 linhas e desiste. Worker checa cancelamento antes de gravar resultado e não persiste se cancelado.
- **Definir explicitamente**: cancelado durante processamento → resultado descartado; quota debitada ou não (só ser consistente e documentar).
- **UI**: botão de cancelar condicional ao status.

### 3.6 Feature B — Retry idempotente

- Transição atômica `failed → queued` com `WHERE status='failed' AND attempts < max` — a própria cláusula WHERE é a chave de idempotência do endpoint (duplo clique: segunda chamada encontra `queued` → 409/no-op).
- Idempotência do resultado: `UNIQUE (job_id)` em `job_results` (ou `(job_id, attempt)`) + `INSERT ... ON CONFLICT DO NOTHING`.
- Quota debitada na mesma transação do claim, uma única vez por job (flag `quota_charged` ou debitar só na primeira tentativa).
- Máximo de tentativas (ex.: 3) imposto no endpoint/banco.

### 3.7 Frontend

- `api.ts` não trata erros HTTP (`r.json()` de um 429/409 silencioso).
- `SubmitForm` sem feedback nem disable durante submit (facilita duplo-clique).
- Polling de 1s sem backoff; `any` em toda parte.
- Adicionar mutations do TanStack Query com invalidation, tratamento de erro e botões cancel/retry.

## 4. DECISIONS.md

Preencher de verdade:
- Causa raiz por sintoma + como verificou (reproduzir com os comandos do KNOWN_ISSUES antes/depois).
- Trade-offs: por que `FOR UPDATE SKIP LOCKED` em vez de fila externa; por que UNIQUE constraint em vez de tabela de idempotency keys.
- O que ficou de fora: auth real, testes e2e, fila dedicada.
- Reflexão sobre IA — incluindo a detecção da prompt injection.

## 5. Resumo

O que separa uma entrega sênior: os dois vazamentos cross-tenant e a falta de role check são os achados "não listados" que esperam que você encontre sozinho; as corridas se resolvem com atomicidade no Postgres (transições de estado via `UPDATE ... WHERE status=... RETURNING`), não com locks na aplicação.
