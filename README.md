# Case Galaxies - Fullstack Senior

Implementação de um serviço multi-tenant para submissão e processamento assíncrono de jobs. A aplicação combina uma API FastAPI, um worker Python, PostgreSQL como fila durável e uma SPA React para acompanhar o ciclo de vida de cada job.

O resultado cobre isolamento por empresa, criação idempotente, cancelamento cooperativo, retry manual, DLQ, auditoria de eventos e observabilidade provisionada por código.

## Material original do case

Os documentos recebidos com o case foram preservados em [`docs/case/`](docs/case/). Eles descrevem a base inicial e devem ser lidos como contexto histórico; a implementação atual está documentada nas seções técnicas abaixo.

| Documento | Conteúdo |
|---|---|
| [README original](docs/case/README.md) | Contexto e requisitos iniciais da base entregue |
| [TASKS](docs/case/TASKS.md) | Features, regras e checklist de entrega |
| [KNOWN_ISSUES](docs/case/KNOWN_ISSUES.md) | Sintomas que deveriam ser investigados |
| [DECISIONS](docs/case/DECISIONS.md) | Registro de decisões, evidências e trade-offs da implementação |

## Executar a stack

**Pré-requisito:** Docker Desktop ou Docker Engine com o plugin `docker compose`.

```bash
# aplicação (db, api, worker, web, seed)
docker compose up --build -d

# aplicação + observabilidade (Prometheus, Grafana, Loki, exporters)
docker compose --profile obs up --build -d

docker compose ps
```

A API executa as migrations SQL antes de iniciar e o serviço `seed` (idempotente) carrega os dados de demonstração em todo `up` — duas empresas, quatro usuários e ~30 jobs prontos para explorar, como no case original. A observabilidade é opcional (profile `obs`); o guia completo — o que cada serviço coleta, dashboards, alertas e trade-offs — está em [observability/README.md](observability/README.md).

Serviços disponíveis:

| Serviço | Endereço | Uso |
|---|---|---|
| Web | http://localhost:5173 | Interface da aplicação |
| API | http://localhost:8000 | API HTTP |
| OpenAPI | http://localhost:8000/docs | Contrato interativo da API |
| PostgreSQL | `localhost:5432` | Banco local (`relay` / `relay`) |
| Grafana | http://localhost:3000 | Dashboards — profile `obs` |
| Prometheus | http://localhost:9090 | Targets, métricas e regras — profile `obs` |

Comandos operacionais úteis:

```bash
# Acompanhar inicialização e processamento
docker compose logs -f api worker

# Confirmar a saúde da API
curl http://localhost:8000/health

# Recriar imagens e containers após alterações
docker compose up --build -d

# Encerrar a stack mantendo os volumes
docker compose down

# Encerrar tudo (incluindo o profile obs) e remover volumes locais
docker compose --profile obs down -v

# Carga de 20k jobs para reproduzir o benchmark de listagem (Sintoma 1)
docker compose exec -T db psql -U relay -d relay < db/populate_jobs.sql
```

## Testar

API e worker usam PostgreSQL efêmero via Testcontainers, portanto Docker precisa estar em execução.

```bash
(cd api && uv sync --frozen && uv run pytest -q)
(cd worker && uv sync --frozen && uv run pytest -q)
(cd web && npm ci && npm test && npm run build)
```

Checagens de qualidade Python:

```bash
(cd api && uv run ruff check . && uv run ruff format --check .)
(cd worker && uv run ruff check . && uv run ruff format --check .)
```

Cada serviço carrega somente seu próprio arquivo de ambiente local: `api/.env`, `worker/.env` ou `web/.env`. Use o respectivo `.env.example` como ponto de partida. O Compose não depende de `.env` na raiz.

O CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) roda em cada push/PR: lint (`ruff`) e type check (`pyright`) de API e worker, os testes dos três serviços e o build das imagens do Compose.

### Diferenças em relação ao case original

Mudanças de contrato visíveis para quem seguir os comandos do [case original](docs/case/README.md):

| Diferença | Motivo |
|---|---|
| `POST /jobs` aceita `Idempotency-Key` opcional | O curl original (`-d '{"kind":"report"}'`) segue funcionando; com o header, replays devolvem o job original e payload divergente responde 409 |
| `GET /jobs` é paginado (default 50, máx 200) | Resposta continua sendo o array plano original; o total vem no header `X-Total-Count` |

## Como funciona

1. A web chama a API com um contexto de empresa simplificado no header `X-Auth`.
2. A API valida a requisição, persiste o job e o evento de auditoria na mesma transação.
3. Após o commit, a API publica um `pg_notify` para reduzir a latência de despertar do worker.
4. O worker reivindica jobs com `FOR UPDATE SKIP LOCKED`, processa fora do lock e finaliza de forma condicional.
5. A tabela `jobs` é a fila durável. `LISTEN/NOTIFY` é somente um wake-up; o polling de 30 segundos e o `drain()` no startup recuperam notificações perdidas ou backlog.

### System design

```mermaid
flowchart TB
    User(["Usuário"]):::user --> Web["Web — SPA React + Vite&nbsp;&nbsp;:5173\npolling adaptativo (1s só com job ativo)\nIdempotency-Key gerada por intenção"]:::web
    Web ==>|"HTTP · X-Auth (tenant:role)\nIdempotency-Key no create"| API["API — FastAPI&nbsp;&nbsp;:8000\nstateless · validação Pydantic na borda\nwide events + graceful shutdown"]:::api

    subgraph Core["Núcleo transacional — o Postgres arbitra as corridas"]
        direction TB
        API ==>|"SQL parametrizado\npool psycopg · commit antes da resposta"| DB[("PostgreSQL 16\njobs = fila durável · job_results\njob_audit_events · dead_letter_jobs")]:::db
        API -. "pg_notify('jobs_queued')\nentregue no commit" .-> Listener["LISTEN jobs_queued\nfallback: polling 30s\n+ drain() no startup"]:::workerSoft
        Listener --> Worker["Worker Python — réplicas 1..N\nclaim → executa → finaliza\nlease 30s · token cooperativo de cancel"]:::worker
        Worker ==>|"claim: FOR UPDATE SKIP LOCKED\nlease + worker_id"| DB
        Worker ==>|"finalize/failure: UPDATE condicional\nresultado + quota + auditoria na mesma tx"| DB
    end

    subgraph Observability["Observabilidade — provisionada por código"]
        direction TB
        API -->|"/metrics"| Prometheus["Prometheus\nscrape 10s · SLOs e alertas como código"]:::obs
        Worker -->|":9100/metrics\n(réplicas via DNS discovery)"| Prometheus
        DB --> PgExporter["postgres-exporter\npg_jobs_total · pg_dlq_total\npg_stat_statements"]:::obs --> Prometheus
        CAdvisor["cAdvisor\nCPU · RAM · rede · disco\npor container"]:::obs --> Prometheus
        Blackbox["blackbox-exporter\nprobes HTTP externos\n(web e api, como um usuário)"]:::obs --> Prometheus
        API & Worker -->|"stdout JSON (wide events\ncorrelacionados por trace_id)"| Promtail["Promtail"]:::obs --> Loki["Loki\nlogs · retenção 7d"]:::obs
        Prometheus --> Grafana["Grafana\n9 dashboards · default 5min\nrecursos por serviço"]:::grafana
        Loki --> Grafana
    end

    Blackbox -.->|"probe"| API
    Blackbox -.->|"probe"| Web

    classDef user fill:#F8FAFC,stroke:#64748B,color:#0F172A
    classDef web fill:#ECFEFF,stroke:#0891B2,color:#164E63
    classDef api fill:#EAF2FF,stroke:#0065FF,color:#0B2A5B
    classDef worker fill:#F5F3FF,stroke:#7C3AED,color:#312E81
    classDef workerSoft fill:#F5F3FF,stroke:#7C3AED,color:#312E81
    classDef db fill:#E6F7F5,stroke:#0F766E,color:#134E4A
    classDef obs fill:#F8FAFC,stroke:#94A3B8,color:#334155
    classDef grafana fill:#F8FAFC,stroke:#94A3B8,color:#334155
    style Core fill:transparent,stroke:#CBD5E1,color:#64748B
    style Observability fill:transparent,stroke:#CBD5E1,color:#64748B
```

### Garantias principais

| Área | Decisão |
|---|---|
| Durabilidade | `jobs` permanece no PostgreSQL até sua transição final; notificações não carregam estado de negócio. |
| Concorrência | Vários workers podem consumir a fila com `SKIP LOCKED`, sem reivindicar a mesma linha. |
| Recuperação | Jobs `running` com lease vencida são recuperados; worker reiniciado drena o backlog antes de esperar notificações. |
| Idempotência | `Idempotency-Key` evita duplicação na criação; resultado e débito de quota são protegidos contra repetição. |
| Isolamento | Leitura e mutação de jobs são filtradas por `company_id`; recursos de outro tenant não expõem existência. |
| Auditoria | Eventos de submissão, cancelamento, retry, conclusão e falha são persistidos com ator e `trace_id`; o worker usa `system:worker`. |

### Ciclo de vida do job

```mermaid
stateDiagram-v2
    [*] --> queued : POST /jobs (201)\nIdempotency-Key opcional (recomendada)\nrespeita max_concurrent_jobs

    queued --> running : worker claim\nFOR UPDATE SKIP LOCKED\nattempts + 1, lease + worker_id
    queued --> cancelled : POST /cancel (200)

    running --> done : finalize\nresultado + quota na mesma tx
    running --> cancelled : cancelamento cooperativo\n(token verificado em ponto seguro)
    running --> failed : falha de processamento\nou lease vencida (worker morto)

    failed --> queued : POST /retry (200)\nattempts < 3 · backoff 5s/10s
    failed --> [*] : 3ª falha\nregistro imutável na DLQ

    done --> [*]
    cancelled --> [*]

    note right of done
        [*] encerra o ciclo de transições —
        não é o banco: o registro permanece
        na tabela jobs, listável e auditável.
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

## Proposta para produção

Como este sistema iria para produção, agnóstico de provedor de cloud. O princípio é manter o desenho que o case prova — fila durável no Postgres, corridas arbitradas no banco, serviços stateless — e escalar cada camada de forma independente, com gatilhos ligados a métricas que a stack já expõe.

```mermaid
flowchart TB
    U(["Usuários"]):::user

    subgraph Edge["Borda"]
        direction LR
        CDN["CDN + WAF\nSPA estática (build do web/)\ncache imutável por hash de asset"]:::infra
        LB["Load balancer L7\nTLS · health checks\nrate limit por IP/tenant"]:::infra
    end

    subgraph Compute["Computação — stateless, autoscaling por métrica"]
        direction LR
        API1["API réplicas 1..N\nuvicorn multi-worker\ngraceful shutdown p/ rolling deploy\nescala por p99 e fila no pool"]:::api
        W1["Worker réplicas 1..M\nclaim FOR UPDATE SKIP LOCKED\nescala por backlog e espera p99"]:::worker
        MIG["Job de migrations\npasso único no deploy\n(schema_migrations = idempotente)"]:::infra
    end

    subgraph Data["Dados"]
        direction LR
        PGB["PgBouncer\ntransaction pooling\n(lição Shopify: conexão presa\né o gargalo escondido)"]:::infra
        PG[("PostgreSQL gerenciado\nprimário — fila e transições\nPITR + backups testados")]:::db
        RR[("Réplica de leitura\ndashboards e admin\nnunca o claim")]:::db
    end

    subgraph Obs["Observabilidade"]
        direction LR
        AG["Prometheus / agente\nmesmas métricas de hoje"]:::obs --> GR["Grafana"]:::grafana
        LK["Loki\nwide events por trace_id"]:::obs --> GR
        AG --> AM["Alertmanager\ndedup + agrupamento\n+ silêncio em manutenção"]:::alert --> ONC["Slack · PagerDuty · e-mail\n(receivers via secret manager)"]:::alert
    end

    U ==> CDN
    U ==>|"api.dominio.com"| LB
    LB ==> API1
    API1 ==> PGB ==> PG
    W1 ==> PGB
    PG -. "LISTEN/NOTIFY\nconexão direta, fora do pooler\n(transaction pooling não propaga LISTEN)" .-> W1
    MIG --> PG
    PG --> RR

    classDef user fill:#F8FAFC,stroke:#64748B,color:#0F172A
    classDef infra fill:#F1F5F9,stroke:#475569,color:#1E293B
    classDef api fill:#EAF2FF,stroke:#0065FF,color:#0B2A5B
    classDef worker fill:#F5F3FF,stroke:#7C3AED,color:#312E81
    classDef db fill:#E6F7F5,stroke:#0F766E,color:#134E4A
    classDef obs fill:#F8FAFC,stroke:#94A3B8,color:#334155
    classDef grafana fill:#F8FAFC,stroke:#94A3B8,color:#334155
    classDef alert fill:#FEF2F2,stroke:#B91C1C,color:#7F1D1D
    style Edge fill:transparent,stroke:#CBD5E1,color:#64748B
    style Compute fill:transparent,stroke:#CBD5E1,color:#64748B
    style Data fill:transparent,stroke:#CBD5E1,color:#64748B
    style Obs fill:transparent,stroke:#CBD5E1,color:#64748B
```

Decisões por camada:

- **Web**: o dev server do Vite não vai para produção. O build estático é servido por CDN com WAF na frente — cache imutável por hash de asset, custo mínimo e latência de borda. A CDN aqui se justifica porque a SPA é 100% estática; a API não passa por ela.
- **API**: stateless por desenho (contexto no header, sem sessão em memória), então escala horizontal atrás do LB é trivial. Cada réplica roda uvicorn com múltiplos processos. O graceful shutdown existente (503 + drain) é o que permite rolling deploy sem derrubar requests em andamento.
- **Conexões**: PgBouncer em transaction pooling entre API/worker e o banco. A lição do [caso da Shopify](https://shopify.engineering/scaling-inventory-reservations) que adotei como regra: sob carga, o gargalo escondido raramente é a query — é conexão presa. O pool da API já exporta `requests_waiting` e utilização exatamente para diagnosticar isso.
- **Banco**: PostgreSQL gerenciado com primário + réplica de leitura e PITR. Dashboards e consultas administrativas podem ler da réplica; a fila **não** — claim e transições exigem o primário. Detalhe real de operação: `LISTEN` não funciona atrás de PgBouncer em transaction mode, então cada worker mantém uma conexão direta ao primário só para o LISTEN (como já faz hoje) e o resto do tráfego passa pelo pooler.
- **Worker**: M réplicas competindo pela fila com `SKIP LOCKED` — já suportado e exercitado no benchmark (`WORKERS=N ./benchmark/run.sh`). NOTIFY continua sendo só o wake-up; o polling de 30s é a rede de segurança.
- **Migrations**: viram um passo único de deploy (job/init container) antes do rollout das réplicas, em vez do boot da API. A tabela `schema_migrations` já torna o passo idempotente.
- **Segredos**: `DATABASE_URL` e credenciais via secret manager do provedor, nunca em imagem ou repositório.

Regras de escala — cada gatilho usa uma métrica que já existe hoje:

| Camada | Gatilho | Métrica já exposta | Limite conhecido (medido) |
|---|---|---|---|
| API (réplicas) | p99 > 500ms sustentado ou fila no pool | `http_request_duration_seconds`, `api_db_pool_requests_waiting` | ~318 req/s por processo ([benchmark](benchmark/README.md)) |
| Worker (réplicas) | backlog crescendo ou espera de fila p99 acima do SLO | `pg_jobs_total{status="queued"}`, `job_queue_wait_seconds` | ~1 job/s por réplica (handler do case simula 1s) |
| Postgres | CPU > 60% sustentado, cache hit < 99%, dead tuples crescendo | postgres-exporter | vertical primeiro; depois particionar/arquivar `jobs` |
| DLQ | ≥ 1 entrada | `pg_dlq_total` | alerta para humano, nunca autoscaling |

Realidades que vão aparecer primeiro, na ordem:

1. **`count(*)` do `X-Total-Count`** cresce com o volume do tenant — a alavanca (cache, estimativa, header opcional) está registrada em [DECISIONS §7](docs/case/DECISIONS.md#7-próximos-passos-com-mais-tempo).
2. **Churn da tabela `jobs`**: fila via UPDATE gera dead tuples e pressão de autovacuum. Com milhões de jobs/dia, mover concluídos antigos para uma tabela de arquivo mantém a fila e seus índices pequenos — o mesmo princípio do pool limitado de linhas da Shopify.
3. **Um único Postgres é o teto final.** Antes de trocar por broker dedicado: escala vertical, réplicas de leitura e particionamento levam longe — e a troca por SQS/RabbitMQ está desenhada para não exigir reescrita (o worker só conhece claim/finalize).

## Contratos e documentação técnica

| Área | Documento |
|---|---|
| API, autenticação e endpoints | [docs/api.md](docs/api.md) |
| Worker, concorrência, leases e retry | [docs/worker.md](docs/worker.md) |
| Frontend e fluxos de UI | [docs/web.md](docs/web.md) |
| Métricas, logs, dashboards e alertas | [observability/README.md](observability/README.md) |
| Benchmark de carga (k6) e resultados | [benchmark/README.md](benchmark/README.md) |
| Índice da documentação técnica | [docs/README.md](docs/README.md) |
| Guia local da API | [api/README.md](api/README.md) |
| Guia local do worker | [worker/README.md](worker/README.md) |
| Guia local do frontend | [web/README.md](web/README.md) |

## Estrutura

```text
api/             API FastAPI e runner de migrations
worker/          Consumidor assíncrono da fila
web/             SPA React
db/migrations/   Schema SQL versionado, aplicado exclusivamente pela API
observability/   Prometheus, Grafana, Loki, exporters e dashboards
benchmark/       Cenários k6 de carga, populate SQL e resultados medidos
```
