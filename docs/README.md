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
| [benchmark/README.md](../benchmark/README.md) | Carga com k6: cenários, resultados e análise de gargalo |

O [README raiz](../README.md) é o ponto de entrada operacional. O registro de
decisões, evidências e trade-offs está em
[docs/case/DECISIONS.md](case/DECISIONS.md); os demais arquivos de `case/`
preservam o enunciado original.

## System design

```mermaid
flowchart LR
    subgraph Cliente["Cliente"]
        W["Web — React + Vite&nbsp;&nbsp;:5173\npolling adaptativo\nIdempotency-Key por intenção"]:::web
    end
    subgraph Backend["Backend"]
        A["API — FastAPI&nbsp;&nbsp;:8000\nvalidação na borda · wide events\ncommit antes da resposta"]:::api
        K["Worker Python — réplicas 1..N\nlease 30s · cancel cooperativo\nDLQ na 3ª falha"]:::worker
    end
    subgraph Dados["Dados"]
        P[("PostgreSQL 16\njobs = fila durável\nresultados · auditoria · DLQ")]:::db
    end

    W ==>|"HTTP · X-Auth (tenant:role)"| A
    A ==>|"SQL parametrizado (pool psycopg)\npg_notify('jobs_queued') no commit"| P
    P -. "LISTEN jobs_queued\nwake-up (fallback: polling 30s)" .-> K
    K ==>|"claim: FOR UPDATE SKIP LOCKED\nfinalize: UPDATE condicional"| P

    classDef web fill:#ECFEFF,stroke:#0891B2,color:#164E63
    classDef api fill:#EAF2FF,stroke:#0065FF,color:#0B2A5B
    classDef worker fill:#F5F3FF,stroke:#7C3AED,color:#312E81
    classDef db fill:#E6F7F5,stroke:#0F766E,color:#134E4A
    style Cliente fill:transparent,stroke:#CBD5E1,color:#64748B
    style Backend fill:transparent,stroke:#CBD5E1,color:#64748B
    style Dados fill:transparent,stroke:#CBD5E1,color:#64748B
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
    [*] --> queued : POST /jobs (201)\nIdempotency-Key + limite de concorrência

    queued --> running : worker claim\nSKIP LOCKED · attempts + 1
    queued --> cancelled : POST /cancel (200)

    running --> done : finalize\nresultado + quota na mesma tx
    running --> cancelled : POST /cancel\n(token cooperativo interrompe)
    running --> failed : falha + last_error\nou lease vencida

    failed --> queued : POST /retry (200)\nattempts < 3 · backoff 5s/10s
    failed --> [*] : attempts >= 3\nregistro imutável na DLQ

    done --> [*]
    cancelled --> [*]

    note right of done
        [*] encerra as transições, não é o banco:
        a linha permanece na tabela jobs.
    end note

    classDef sQueued fill:#E8F1FF,stroke:#0065FF,color:#0B2A5B
    classDef sRunning fill:#FEF3C7,stroke:#D97706,color:#7C2D12
    classDef sDone fill:#DCFCE7,stroke:#16A34A,color:#14532D
    classDef sFailed fill:#FEE2E2,stroke:#DC2626,color:#7F1D1D
    classDef sCancelled fill:#E5E7EB,stroke:#6B7280,color:#1F2937
    class queued sQueued
    class running sRunning
    class done sDone
    class failed sFailed
    class cancelled sCancelled
```

O worker não reprocessa automaticamente uma falha. A primeira e a segunda falha
deixam o job em `failed`; o usuário ou uma integração deve chamar `POST /retry`.
O retry agenda o job para depois de 5 segundos na primeira repetição e 10
segundos na segunda. Ao atingir três tentativas, o job permanece em `failed` e
recebe uma entrada na DLQ.

## Corrida cancelar × finalizar (quem vence: quem escreve primeiro)

```mermaid
sequenceDiagram
    autonumber
    box rgb(248, 250, 252) Cliente
        participant U as Usuário
    end
    box rgb(248, 250, 252) Backend
        participant A as API
    end
    box rgb(248, 250, 252) Dados
        participant P as Postgres
    end
    box rgb(248, 250, 252) Processamento
        participant K as Worker
    end

    K->>+P: claim: UPDATE jobs SET running (tx própria, commit)
    P-->>-K: linha reivindicada (lease + worker_id)
    Note over K: processa fora de qualquer lock<br/>(watcher em conexão separada observa o status)
    U->>+A: POST /jobs/{id}/cancel
    A->>+P: UPDATE ... SET cancelled WHERE status IN (queued, running)
    P-->>-A: 1 linha atualizada
    A-->>-U: 200 { status: cancelled }
    K->>+P: finalize: UPDATE ... SET done WHERE status = 'running'
    P-->>-K: 0 linhas — cancelado no meio
    Note over K: token de cancelamento interrompe o handler<br/>rollback da transação · limpa a lease · quota intacta
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
%%{init: {'theme': 'base', 'themeVariables': {
    'primaryColor': '#E8F1FF', 'primaryBorderColor': '#0065FF',
    'primaryTextColor': '#0B2A5B', 'lineColor': '#64748B',
    'tertiaryColor': '#F0FDFA', 'fontSize': '13px'}}}%%
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
