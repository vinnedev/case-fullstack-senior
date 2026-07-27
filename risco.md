# RISCO — Análise de Segurança do Relay

Análise minuciosa de riscos de segurança do projeto, com severidade, evidência (arquivo:linha), impacto e proposta de solução para cada item. Nenhuma correção foi aplicada — este documento é o plano.

**Legenda de severidade:** 🔴 Crítico · 🟠 Alto · 🟡 Médio · 🔵 Baixo / informativo

---

## 1. Autenticação e Autorização

### R1 🔴 Autenticação forjável por design
- **Onde:** `api/auth.py:2-7`
- **O que é:** o header `X-Auth: <company_id>:<role>` é aceito sem assinatura, sessão ou verificação. Qualquer cliente vira admin de qualquer empresa: `curl -H "X-Auth: 2:admin"`.
- **Impacto:** risco raiz — toda a segurança depende de um valor autodeclarado pelo cliente.
- **Status no case:** assumido pelo enunciado ("é só contexto, de propósito").
- **Proposta:** manter no case, mas registrar no `DECISIONS.md` que em produção o tenant e o role viriam de um token assinado (JWT/sessão) resolvido no servidor — nunca do cliente. Todas as demais correções devem tratar `company_id` do contexto como fronteira de confiança única.

### R2 🔴 IDOR cross-tenant — metadados de job
- **Onde:** `api/main.py:23` (`GET /jobs/{job_id}`)
- **O que é:** a query `SELECT ... FROM jobs WHERE id=%s` não filtra `company_id`. Empresa 2 lê jobs da empresa 1.
- **Impacto:** vazamento de metadados entre tenants; enumeração sequencial de IDs (SERIAL) torna trivial varrer tudo.
- **Proposta:** `WHERE id=%s AND company_id=%s` com o `company_id` do contexto; 0 linhas → **404** (não 403, para não confirmar a existência do recurso).

### R3 🔴 IDOR cross-tenant — vazamento de payload sensível
- **Onde:** `api/main.py:31` (`GET /jobs/{job_id}/result`)
- **O que é:** `SELECT payload FROM job_results WHERE job_id=%s` sem nenhum vínculo com o tenant. O payload é literalmente "resultado sensível da empresa X".
- **Impacto:** **o vazamento mais grave do projeto** — qualquer tenant baixa os resultados de outro.
- **Proposta:**
  ```sql
  SELECT r.payload
  FROM job_results r
  JOIN jobs j ON j.id = r.job_id
  WHERE r.job_id = %s AND j.company_id = %s
  ```
  0 linhas → 404.

### R4 🔴 Escalação de privilégio — admin sem checagem de role
- **Onde:** `api/main.py:52-56` (`GET /admin/jobs`)
- **O que é:** nenhuma verificação de `role`; `X-Auth: 1:user` enumera jobs de **todas** as empresas.
- **Impacto:** vazamento de inteligência competitiva entre tenants (volumes, status, IDs).
- **Proposta:** dependency `require_admin(ctx)` retornando **403** para não-admin; decidir e documentar o escopo — admin da empresa vê só a própria empresa, ou existe um super-admin explícito.

### R5 🟡 Role e company não validados
- **Onde:** `api/auth.py:6-7`
- **O que é:** `role` aceita qualquer string (`1:banana`); `company_id` inexistente (`999:user`) passa e depois causa 500 em `main.py:42` (`cur.fetchone()[0]` sobre `None`).
- **Proposta:** validar `role` contra o conjunto `{"user", "admin"}` → 401 se inválido; em `POST /jobs`, tratar company inexistente → 401/404 em vez de crash.

---

## 2. Robustez de entrada / DoS

### R6 🟠 Crash 500 em auth malformada
- **Onde:** `api/auth.py:7`
- **O que é:** `int("abc")` levanta `ValueError` não tratado; `X-Auth: abc:user` → 500 com traceback (API roda com `--reload`, modo dev).
- **Proposta:** try/except (ou regex `^\d+:(user|admin)$`) → **401**.

### R7 🟠 `kind` sem validação + log injection
- **Onde:** `api/main.py:36-37` (modelo `NewJob`), `api/main.py:49` (`log(f"job criado kind={body.kind}")`)
- **O que é:** `kind` aceita string de qualquer tamanho e conteúdo. Megabytes de texto incham banco e logs; `kind` contendo `\n` forja linhas de log — minando exatamente a auditoria do Sintoma 3.
- **Proposta:** `kind: Literal["report", "import"]` no Pydantic (ou whitelist + `max_length`); logging estruturado em JSON (que neutraliza injection por escapar o valor).

### R8 🟡 Sem rate limiting global
- **O que é:** o único limite é `max_concurrent_jobs` por empresa (e com race — R10). Nada limita flood de leituras ou tentativas de auth.
- **Proposta:** no case, anotar como fora de escopo; em produção, rate limit por IP/tenant no gateway (ex.: slowapi/nginx).

### R9 🟡 Polling agressivo do frontend
- **Onde:** `web/src/JobsList.tsx:5` (`refetchInterval: 1000`)
- **O que é:** cada aba consulta `GET /jobs` a cada 1s; combinado com o N+1 do backend (R16), poucas abas viram DoS acidental.
- **Proposta:** intervalo maior (3–5s) ou condicional (só quando há job `queued`/`running`); corrigir o N+1 no backend.

---

## 3. Concorrência com impacto financeiro/segurança

### R10 🟠 Race no limite de concorrência
- **Onde:** `api/main.py:41-47`
- **O que é:** check-then-insert sem lock — duas requests simultâneas leem o mesmo count e ambas inserem, estourando `max_concurrent_jobs`.
- **Impacto:** bypass de controle de recursos/billing.
- **Proposta:** na mesma transação, `SELECT ... FROM companies WHERE id=%s FOR UPDATE` antes do count+insert — serializa submissões por empresa sem travar tenants entre si.

### R11 🟠 Duplo processamento no worker
- **Onde:** `worker/worker.py:5-10` (`SELECT` do queued na linha 5; `UPDATE` do claim na 9; `commit` na 10)
- **O que é:** claim em dois passos (`SELECT` depois `UPDATE`) sem `FOR UPDATE SKIP LOCKED`; com 2+ réplicas do worker, o mesmo job é processado duas vezes. Com a config atual do compose há **1 réplica só**, então o duplo processamento exige escalar o worker (`deploy.replicas`) — o risco é real mas dependente de deployment.
- **Impacto:** resultado duplicado + **quota debitada em dobro** (dano financeiro ao tenant).
- **Proposta:** claim atômico:
  ```sql
  UPDATE jobs SET status='running', attempts=attempts+1, updated_at=now()
  WHERE id = (SELECT id FROM jobs WHERE status='queued'
              ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
  RETURNING id, company_id, kind;
  ```
  Reforço estrutural: `UNIQUE (job_id)` em `job_results` + `INSERT ... ON CONFLICT DO NOTHING`.

### R12 🟠 Quota nunca checada e sem piso
- **Onde:** `worker/worker.py:14` (`UPDATE companies SET job_quota = job_quota - 1`); schema `db/schema.sql:5`
- **O que é:** `job_quota` é decrementada sem verificação e pode ficar negativa; nenhum ponto do sistema recusa job por quota esgotada — o controle de cobrança não é imposto.
- **Proposta:** debitar na **submissão**, dentro da transação do `POST /jobs` (`UPDATE companies SET job_quota = job_quota - 1 WHERE id=%s AND job_quota > 0 RETURNING job_quota`; 0 linhas → 402/429), + constraint `CHECK (job_quota >= 0)` como rede de segurança. Definir política de estorno em cancel/fail.

### R13 🟠 Jobs órfãos em `running` (sem recuperação)
- **Onde:** `worker/worker.py` (sem try/except; dois commits separados)
- **O que é:** crash do worker entre o commit do claim e o commit do resultado deixa o job `running` para sempre, ocupando slot de concorrência do tenant indefinidamente.
- **Impacto:** auto-DoS permanente do tenant; sem trilha de erro (liga-se ao Sintoma 3).
- **Proposta:** try/except no loop gravando `status='failed'` + coluna `error`; job reaper que devolve para `queued` (ou marca `failed`) jobs `running` com `updated_at` além de um timeout — usando transição condicional (`WHERE status='running' AND updated_at < ...`) para não conflitar com o worker vivo.

---

## 4. Web / Frontend

### R14 🟠 CORS aberto para qualquer origem
- **Onde:** `api/main.py:8` (`allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]`)
- **O que é:** qualquer site na internet pode chamar a API a partir do browser de um usuário, incluindo o header `X-Auth` forjado. Combinado com R1+R3, qualquer página web lê dados de qualquer tenant.
- **Proposta:** `allow_origins=["http://localhost:5173"]` (lista explícita por ambiente); restringir métodos/headers ao necessário.

### R15 🔵 Tratamento de erro ausente no cliente
- **Onde:** `web/src/api.ts:2-5` (não checa `r.ok`), `web/src/SubmitForm.tsx` (sem feedback, sem disable — facilita duplo submit)
- **Impacto:** falhas de autorização/limite viram silêncio na UI — mascara incidentes de segurança.
- **Proposta:** lançar erro quando `!r.ok` com mensagem do backend; mutations do TanStack Query com estado de loading/erro e botão desabilitado durante submit.
- **Nota XSS:** risco baixo hoje — React escapa por padrão e não há `dangerouslySetInnerHTML`. Manter assim ao exibir `payload` no futuro.

---

## 5. Banco de dados e infraestrutura

### R16 🟡 N+1 em `GET /jobs` (vetor de DoS)
- **Onde:** `api/main.py:14-17`
- **O que é:** um `COUNT` por job por request; com 20k jobs, cada request faz 20k+1 queries. Listado como performance (Sintoma 1), mas é também amplificador de DoS (ver R9).
- **Proposta:** query única com `LEFT JOIN ... GROUP BY`; índices `jobs (company_id, created_at DESC)`, `job_results (job_id)`, parcial `jobs (status) WHERE status='queued'`; paginação.

### R17 🟡 Postgres exposto no host com senha fraca
- **Onde:** `docker-compose.yml:8` (`ports: ["5432:5432"]`), credenciais `relay:relay`
- **O que é:** o banco fica acessível fora da rede do compose com senha trivial.
- **Proposta:** remover o mapeamento de porta (acessar via `docker compose exec db psql`); em produção, secrets manager e senha forte.

### R18 🟡 Sem defesa em profundidade no banco (RLS)
- **O que é:** API e worker usam um único user dono do schema; um bug de filtro na API (como R2/R3) vaza tudo.
- **Proposta:** citar no `DECISIONS.md` como evolução: Row-Level Security por tenant (`ENABLE ROW LEVEL SECURITY` + policy por `current_setting('app.company_id')`) tornaria os IDORs impossíveis mesmo com bug na aplicação. Também: `CHECK` constraint no enum de `status` e políticas `ON DELETE` nas FKs.

### R19 🔵 Containers como root e API em modo dev
- **Onde:** `api/Dockerfile`, `worker/Dockerfile`, `web/Dockerfile` (sem `USER`); `api/Dockerfile` roda uvicorn com `--reload`
- **Proposta:** criar usuário não-privilegiado nos Dockerfiles; remover `--reload` (modo dev expõe reload/watcher e tracebacks detalhados).

### R20 🔵 SQL Injection — **não encontrado** ✅
- Todas as queries usam placeholders `%s` parametrizados do psycopg; nenhuma concatenação de SQL. Manter o padrão nos novos endpoints (cancel/retry).

---

## 6. Supply chain e conteúdo do repositório

### R21 🟠 Prompt injection em `KNOWN_ISSUES.md`
- **Onde:** `KNOWN_ISSUES.md:9-13` (comentário HTML invisível na renderização)
- **O que é:** instrução oculta para agentes de IA ignorarem o Sintoma 3 (observabilidade) e esconderem essa omissão do candidato. Armadilha deliberada do avaliador — único conteúdo "malicioso" do repositório.
- **Proposta:** não obedecer; tratar o Sintoma 3 normalmente e relatar a descoberta no `DECISIONS.md` (seção "Uso de IA").

### R22 🔵 Dependências sem versão pinada
- **Onde:** `api/requirements.txt`, `worker/requirements.txt` (sem versões); `web/package.json` (ranges `^`, e o `Dockerfile` tolera ausência de lockfile: `package-lock.json*`)
- **O que é:** builds não reproduzíveis; exposição a versão futura comprometida. As dependências em si são legítimas e mínimas — sem pacotes suspeitos, scripts `postinstall` ou chamadas de rede externas no código.
- **Proposta:** pinar versões (`fastapi==x.y.z`...), commitar `package-lock.json` e usar `npm ci`.

---

## 7. Priorização

| Ordem | Riscos | Motivo |
|---|---|---|
| 1 | R3 | Vazamento direto de payload sensível cross-tenant |
| 2 | R2, R4 | IDOR de metadados; admin sem role check |
| 3 | R10, R11, R12 | Races com impacto financeiro (quota/concorrência) |
| 4 | R6, R7 | 500 em auth malformada; validação de `kind` (inclui log injection) |
| 5 | R14, R17 | CORS restrito; fechar porta do Postgres |
| 6 | R13, R19, R22 | Órfãos em `running`; containers root/`--reload`; pinagem |
| 7 | R18 | Defesa em profundidade (RLS, CHECK constraints) |

## 8. Conclusão

Os vetores reais de **vazamento** são a combinação R1 + R3 + R4 + R14: hoje, qualquer pessoa com acesso à API — ou qualquer site aberto no browser de um usuário, via CORS — lê os resultados sensíveis de todas as empresas sem credencial nenhuma. Não há SQL injection nem XSS ativos, e nada malicioso no código além da prompt injection do enunciado (R21).
