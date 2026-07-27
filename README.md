# Relay — Serviço de Processamento de Jobs em Background

## O que é Relay?

Relay é um **serviço multi-tenant de processamento de jobs em background**. Empresas (tenants) submetem jobs para processamento assíncrono; um worker processa em background; usuários acompanham o status e baixam resultados — tudo isolado por empresa.

Este repositório contém uma **base de case deliberadamente imperfeita**. Não é um modelo a seguir em produção — é um ponto de partida educacional desenhado para ser estendido.

## Stack

| Camada | Tecnologia |
|---|---|
| **API** | FastAPI (Python 3.12) |
| **Banco de dados** | PostgreSQL 16 |
| **Worker** | Processo Python separado |
| **Frontend** | React 18 + Vite + TanStack Query + shadcn/ui |
| **Orquestração** | Docker Compose |

## Como rodar

### Pré-requisitos
- Docker e Docker Compose recentes (com `docker compose`, não `docker-compose`)

### Start

```bash
docker compose up --build
```

Isso sobe:
- **API** em `http://localhost:8000` (docs OpenAPI em `/docs`) — aplica as migrations (`alembic upgrade head`) antes de iniciar
- **PostgreSQL** em `localhost:5433` (5432 dentro da rede do compose)
- **Worker** (background, sem HTTP)
- **Web** em `http://localhost:5173`

Para popular o banco com os dados de exemplo:

```bash
docker compose --profile seed run --rm seed
```

### Rodando localmente (sem Docker)

Cada serviço Python usa [uv](https://docs.astral.sh/uv/) e carrega automaticamente o `.env` da raiz (`shared/config/env.py`); copie `.env.example` para `.env` se necessário.

```bash
docker compose up -d db                      # Postgres em localhost:5433
cd api && uv run alembic upgrade head        # aplica migrations
docker compose --profile seed run --rm seed  # dados de exemplo
cd api && uv run uvicorn main:app --reload   # API
cd worker && uv run main.py                  # worker
```

## Banco de dados: pool, ORM e migrations

### Camada de acesso (`shared/db/`)

Ambos os serviços (api e worker) acessam o Postgres via **SQLAlchemy 2.0** com pool de conexões (`QueuePool`), configurável por variáveis de ambiente:

| Variável | Default | Descrição |
|---|---|---|
| `DB_POOL_SIZE` | 5 | Conexões persistentes no pool |
| `DB_MAX_OVERFLOW` | 10 | Conexões extras sob pico |
| `DB_POOL_TIMEOUT_S` | 30 | Espera máxima por conexão livre |
| `DB_POOL_RECYCLE_S` | 1800 | Recicla conexões antigas |

- `shared/db/engine.py` — engine singleton (`pool_pre_ping` habilitado), `session_scope()` (commit no sucesso, rollback em exceção), dependency `get_session` para o FastAPI e `dispose_engine()` chamado no graceful shutdown.
- Models por feature (package-by-feature): `features/companies/models.py` (Company, User) e `features/jobs/models.py` (Job, JobResult).
- O worker reivindica jobs com `SELECT ... FOR UPDATE SKIP LOCKED` — é seguro rodar múltiplas réplicas sem processar o mesmo job duas vezes.

### Migrations (Alembic)

A **api é a dona do schema**; as migrations vivem em `api/migrations/` e são a fonte de verdade (não existe mais `db/schema.sql`).

```bash
cd api
uv run alembic upgrade head                          # aplica
uv run alembic revision --autogenerate -m "mudança"  # gera a partir dos models
uv run alembic downgrade -1                          # desfaz a última
```

A migration inicial (`0001_initial_schema`) mantém a modelagem original e adiciona apenas índices dirigidos pelas queries reais da aplicação:

| Índice | Query que atende | Custo de manutenção |
|---|---|---|
| `ix_jobs_company_created (company_id, created_at)` | `GET /jobs` (lista por empresa ordenada por data) | um índice B-tree padrão |
| `ix_jobs_queued (id) WHERE status='queued'` | worker: `WHERE status='queued' ORDER BY id LIMIT 1` | parcial — só indexa jobs na fila (quase sempre poucos), escrita e RAM mínimas |
| `ix_jobs_company_active (company_id) WHERE status IN ('queued','running')` | `POST /jobs` (count do limite de concorrência) | parcial — só jobs ativos |
| `ix_job_results_job_id (job_id)` | `GET /jobs/{id}/result` e o count de resultados no `GET /jobs` | B-tree padrão |

Lookups por PK (`GET /jobs/{id}`, updates do worker) e o `GET /admin/jobs` (`ORDER BY id`) já são cobertos pela PK. Não há índice em colunas de baixa cardinalidade isoladas (`status`, `kind`, `role`) — seriam varridos quase inteiros e só custariam escrita e memória.

No Docker, o container da api roda `alembic upgrade head` automaticamente antes do uvicorn.

### Autenticação fake

Relay usa um sistema de autenticação **simplificado** para fins educacionais. Toda requisição HTTP precisa do header `X-Auth` no formato:

```
X-Auth: <company_id>:<role>
```

Exemplos:
- `X-Auth: 1:user` — usuário comum da empresa 1
- `X-Auth: 2:admin` — admin da empresa 2

**Não há assinatura nem validação criptográfica — é só um contexto.** Na web UI, existe um dropdown para trocar entre 4 usuários fake:
- Empresa 1 (Acme): `user@acme.test` (user), `admin@acme.test` (admin)
- Empresa 2 (Globex): `user@globex.test` (user), `admin@globex.test` (admin)

## Dados seeded

Duas empresas estão pré-criadas:

| ID | Nome | Max concorrentes | Cota inicial |
|---|---|---|---|
| 1 | Acme | 2 | 20 |
| 2 | Globex | 2 | 100 |

Cada empresa tem um usuário comum e um admin. ~30 jobs distribuídos entre os dois tenants em vários status (done, failed, running) para você explorar.

## Endpoints principais

### Listagem de jobs

```
GET /jobs
```

Retorna todos os jobs da empresa do usuário autenticado, ordenados por `created_at` DESC.

**Resposta:**
```json
[
  {
    "id": 1,
    "kind": "report",
    "status": "done",
    "created_at": "2026-07-20T12:34:56.000Z",
    "result_count": 1
  }
]
```

**Status de job:** `queued` | `running` | `done` | `failed`

### Detalhe de um job

```
GET /jobs/{job_id}
```

**Resposta:**
```json
{
  "id": 1,
  "company_id": 1,
  "kind": "report",
  "status": "done"
}
```

### Baixar resultado de um job

```
GET /jobs/{job_id}/result
```

**Resposta:**
```json
{
  "payload": "resultado sensível da empresa 1"
}
```

### Submeter novo job

```
POST /jobs
```

**Body:**
```json
{
  "kind": "report"
}
```

**Resposta:**
```json
{
  "id": 42,
  "status": "queued"
}
```

### Admin: ver todos os jobs

```
GET /admin/jobs
```

Endpoint administrativo: retorna jobs de todas as empresas.

**Resposta:**
```json
[
  {
    "id": 1,
    "company_id": 1,
    "status": "done"
  },
  {
    "id": 2,
    "company_id": 2,
    "status": "running"
  }
]
```

## O que precisa ser feito

Veja o arquivo `TASKS.md` para o enunciado completo do case. Em resumo:

1. **Feature A:** Implementar cancelamento de jobs em processamento.
2. **Feature B:** Implementar retry de jobs com idempotência (não duplicar resultado, não cobrar quota em dobro).
3. **Diagnosticar e corrigir** os problemas listados em `KNOWN_ISSUES.md`.
4. **`DECISIONS.md`:** Documentar achados, correções e trade-offs.

## Problemas conhecidos

Veja `KNOWN_ISSUES.md` para uma lista de sintomas operacionais reportados.

## Segurança

**Importante:** Reporte e corrija **qualquer problema de segurança ou corretude que encontrar, mesmo os não listados em KNOWN_ISSUES.md.** A base é intencionalmente imperfeita — faz parte do case encontrar e corrigir todos os problemas.

## Estrutura do repositório

```
relay/
├─ README.md                    # Este arquivo
├─ TASKS.md                     # Enunciado: features, correções, extras
├─ KNOWN_ISSUES.md             # Sintomas reportados
├─ DECISIONS.md                # (stub) A preencher pelo candidato
├─ docker-compose.yml
├─ .env.example
├─ db/
│  └─ seed.sql                 # Dados de exemplo (via `--profile seed`)
├─ api/                        # API FastAPI (dona do schema)
│  ├─ features/                # Models por feature (jobs, companies)
│  ├─ shared/                  # config (env), db (pool/ORM), logging (wide events)
│  ├─ migrations/              # Alembic (env.py é convenção do Alembic)
│  └─ tests/                   # Espelha a estrutura do código
├─ worker/                     # Processador de jobs (mesmo layout shared/features/tests)
└─ web/                        # React SPA
```

## Perguntas?

Este case é auto-contido. Qualquer informação que você precisar está nestes arquivos ou é deduzível rodando o stack.
