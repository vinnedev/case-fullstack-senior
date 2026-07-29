# Relay — Documentação

Serviço multi-tenant de processamento de jobs em background: empresas submetem
jobs pela API, um worker processa de forma assíncrona e os usuários acompanham
status e resultado pela web.

| Documento | Conteúdo |
|---|---|
| [api.md](api.md) | Endpoints, validação, camadas e regras de negócio da API |
| [worker.md](worker.md) | Ciclo de processamento, concorrência, retry e pub/sub |
| [web.md](web.md) | SPA React: estrutura, fluxos e integração com a API |
| [observability/README.md](../observability/README.md) | Métricas, logs e dashboards (Grafana, Prometheus, Loki) |

O [README raiz](../README.md) é o ponto de entrada operacional. O registro de
decisões, evidências e trade-offs está em
[docs/case/DECISIONS.md](case/DECISIONS.md); os demais arquivos de `case/`
preservam o enunciado original.

## System design

```mermaid
flowchart LR
    subgraph Cliente
        W["Web (React + Vite)\n:5173"]
    end
    subgraph Backend
        A["API (FastAPI)\n:8000"]
        K["Worker (Python)"]
    end
    subgraph Dados
        P[("PostgreSQL 16\njobs = fila durável")]
    end

    W -- "HTTP + X-Auth" --> A
    A -- "SQL (pool psycopg)\npg_notify('jobs_queued')" --> P
    P -. "LISTEN jobs_queued\n(wake-up)" .-> K
    K -- "FOR UPDATE SKIP LOCKED\nclaim / finalize" --> P
```

Pontos estruturais:

- **A tabela `jobs` é a fila.** LISTEN/NOTIFY é só o sinal de "chegou trabalho"
  (baixa latência); a durabilidade vem da tabela + transações. Notify perdido é
  coberto por polling de fallback (30s).
- **Isolamento por tenant** em toda leitura/mutação (`company_id` do `X-Auth`).
- **Concorrência resolvida no banco**: lock da company no create, `SKIP LOCKED`
  no claim, UPDATEs condicionais para cancel/retry, UNIQUE para idempotência.
- **Observabilidade por wide events**: um JSON por request/job, correlacionados
  por `trace_id` de ponta a ponta.

## Ciclo de vida de um job

```mermaid
stateDiagram-v2
    [*] --> queued : POST /jobs
    queued --> running : worker claim
    queued --> cancelled : POST /cancel
    running --> done : finalize (resultado + quota)
    running --> cancelled : POST /cancel
    running --> failed : falha + last_error
    failed --> queued : POST /retry (attempts < 3, backoff 5s/10s)
    failed --> failed : attempts >= 3 + registro na DLQ
    done --> [*]
    cancelled --> [*]
```

O worker não reprocessa automaticamente uma falha. A primeira e a segunda falha
deixam o job em `failed`; o usuário ou uma integração deve chamar `POST /retry`.
O retry agenda o job para depois de 5 segundos na primeira repetição e 10
segundos na segunda. Ao atingir três tentativas, o job permanece em `failed` e
recebe uma entrada na DLQ.

## Corrida cancelar × finalizar (quem vence: quem escreve primeiro)

```mermaid
sequenceDiagram
    participant U as Usuário
    participant A as API
    participant P as Postgres
    participant K as Worker

    K->>P: claim: UPDATE jobs SET running (tx própria, commit)
    Note over K: processa (fora de lock)
    U->>A: POST /jobs/{id}/cancel
    A->>P: UPDATE ... SET cancelled WHERE status IN (queued, running)
    P-->>A: 1 linha → 200 cancelled
    K->>P: finalize: UPDATE ... SET done WHERE status = 'running'
    P-->>K: 0 linhas → cancelado no meio
    Note over K: token de cancelamento interrompe o handler
    Note over K: rollback da transação, limpa lease, não debita quota
```

Se a ordem inverte (finalize primeiro), o cancel não casa linha e responde 409.
Sem deadlock: é sempre um lock de uma única linha.

Durante o processamento, o worker consulta o estado do job por uma conexão
separada. O handler recebe um token cooperativo e deve verificá-lo em pontos
seguros. Ao detectar cancelamento, o worker faz rollback da transação ativa,
limpa a lease e encerra apenas aquele job; o processo do worker continua
disponível para os próximos jobs.

## Modelo de dados

```mermaid
erDiagram
    companies ||--o{ users : possui
    companies ||--o{ jobs : submete
    jobs |o--o| job_results : "0..1 resultado (UNIQUE job_id)"
    jobs ||--o{ dead_letter_jobs : "auditoria ao esgotar tentativas"
    jobs ||--o{ job_audit_events : "ciclo de vida"

    companies {
        int id PK
        text name
        int max_concurrent_jobs
        int job_quota
    }
    users {
        int id PK
        int company_id FK
        text email UK
        text role
    }
    jobs {
        int id PK
        int company_id FK
        text kind
        text status "CHECK: queued|running|done|failed|cancelled"
        int attempts
        text trace_id
        text last_error
        text idempotency_key "UNIQUE (company_id, key)"
        timestamptz next_attempt_at "backoff entre retries"
        timestamptz created_at
        timestamptz updated_at
    }
    job_results {
        int id PK
        int job_id FK "UNIQUE, ON DELETE CASCADE"
        text payload
    }
    dead_letter_jobs {
        int id PK
        int job_id
        int company_id
        text kind
        int attempts
        text last_error
        text trace_id
        timestamptz job_created_at
        timestamptz failed_at
    }
    job_audit_events {
        bigint id PK
        int job_id FK
        int company_id FK
        text event_type
        text actor
        timestamptz occurred_at
        text trace_id
        jsonb metadata
    }
```

Migrations SQL versionadas em `db/migrations/*.sql`, aplicadas em ordem pelo
runner `shared/db/migrate.py` (tabela `schema_migrations`); o container da API
aplica antes de subir.
