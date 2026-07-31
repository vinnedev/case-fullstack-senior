# Benchmark — carga, caos e escala com k6

Cenários de carga contra a stack real (`docker compose`), com três objetivos:
medir o quanto o sistema escala, localizar gargalos com evidência e provar as
garantias de concorrência sob corrida real. Os dados rodam numa empresa
dedicada (id 900), isolada das empresas demo, com limites altos para o gargalo
medido ser o sistema e não a regra de negócio.

## Como rodar

Não precisa de k6 instalado: o runner usa a imagem `grafana/k6` na rede do
Compose. **Todo cenário começa com um estágio de warmup** (fase separada nas
métricas, fora dos thresholds) que aquece pool, caches e processo antes de
medir.

```bash
./benchmark/run.sh read       # todas as rotas de leitura + admin, ramp 20 → 50 VUs
./benchmark/run.sh write      # 30 creates/s constantes (WRITE_RPS ajusta)
./benchmark/run.sh lifecycle  # ciclo completo: create → fila → worker → done → result
./benchmark/run.sh chaos      # corridas deliberadas: replay, duplo retry, cancel × finalize
./benchmark/run.sh mixed      # tudo ao mesmo tempo (satura o worker de propósito)

# réplicas de worker (escala horizontal via SKIP LOCKED)
WORKERS=4 LIFECYCLE_RPS=3 ./benchmark/run.sh lifecycle

# limpar os dados de benchmark ao final
docker compose exec -T db psql -U relay -d relay < benchmark/sql/teardown.sql
```

O runner sobe a stack, cria a empresa 900 (`sql/setup.sql`), completa a carga
(`sql/populate.sql`, top-ups idempotentes: 100k `done`, 1,5k `failed`
retryáveis, 300 `failed` esgotados, 1k `cancelled`), zera fila residual e
executa o cenário de `k6/load.js`. Summaries em `results/` (ignorado pelo git;
os números consolidados vivem aqui).

## Cobertura

| Rota | Cenários |
|---|---|
| `GET /jobs` (paginação, filtro, busca) | read, mixed, chaos |
| `GET /jobs/{id}` | read, lifecycle, chaos, mixed |
| `GET /jobs/{id}/result` | read, lifecycle, mixed |
| `POST /jobs` (Idempotency-Key) | write, lifecycle, chaos, mixed |
| `POST /jobs/{id}/cancel` | lifecycle, chaos, mixed |
| `POST /jobs/{id}/retry` | lifecycle (409 em cancelado), chaos (em failed real) |
| `GET /admin/jobs`, `GET /admin/dlq` | read, mixed |
| Processamento ponta a ponta (fila + worker) | lifecycle (`job_e2e_duration`), mixed |

Thresholds (fase main): `http_req_failed < 1%`, listagem/admin p95 < 500ms,
demais rotas p95 < 300ms, e2e p95 < 15s (só no lifecycle — mixed satura o
worker de propósito).

## Resultados medidos

Ambiente: Docker (OrbStack) em Apple Silicon, stack completa local, 100k+ jobs
na empresa de benchmark. Summaries de 29/07/2026.

### Leitura — antes e depois da otimização da listagem

| Métrica | Antes | Depois |
|---|---|---|
| Throughput | 76 req/s | **328 req/s** (4,3×) |
| `GET /jobs` p50 / p95 / p99 | 464ms / 1,15s / 1,43s | **97ms / 225ms / 288ms** |
| `GET /jobs/{id}` p95 | 835ms | **186ms** |
| `GET /jobs/{id}/result` p95 | — | 174ms |
| `GET /admin/jobs` / `GET /admin/dlq` p95 | — | 204ms / 193ms |
| Thresholds | 2 violados | todos passando, 0% erro |

**Causa raiz.** A query de listagem usava `LEFT JOIN job_results + GROUP BY`
para o `result_count`, e o plano agregava **todos os 100k jobs da empresa**
antes do `LIMIT 50` (64ms por request no `EXPLAIN ANALYZE`, 227ms de média sob
50 VUs). A correção pagina primeiro pelo índice `(company_id, created_at)` e
conta resultados só para as linhas da página: **0,26ms** no mesmo `EXPLAIN`
(246×).

### Escrita e ciclo completo

| Cenário | Resultado |
|---|---|
| write (30 creates/s, 60s) | 100% HTTP 201, p50 5,4ms / p95 7,6ms / p99 9,5ms, zero 429 |
| lifecycle (1 job/s, dentro da capacidade) | e2e p50 **1,51s** / p95 1,52s / p99 1,73s, zero timeouts |
| mixed (~11 jobs/s de entrada, 1 worker) | API segue saudável (238 req/s, 0% erro), mas e2e degrada para p50 15s — worker saturado por construção |

### Escala horizontal do worker (`SKIP LOCKED`)

3 jobs/s de entrada, mesmo cenário, só mudando `WORKERS`:

| Réplicas | e2e p50 | e2e p95 | Timeouts (30s) |
|---|---|---|---|
| 1 worker (capacidade ~1 job/s) | 13,5s | 27,5s | 102 |
| **4 workers** | **1,52s** | **1,53s** | **0** |

A fila drena sem nenhum job processado duas vezes — o claim com
`FOR UPDATE SKIP LOCKED` arbitra as réplicas no banco.

### Caos: corridas deliberadas (garantias sob disputa)

O cenário `chaos` dispara as corridas que o sistema promete arbitrar e
verifica que só um lado vence cada disputa: replay concorrente da mesma
`Idempotency-Key` (batch de 2 requests simultâneas), duplo retry no mesmo job
`failed` e cancel no meio do processamento do retry (corrida com o finalize).

Resultado final: **1005 requests, 0% falhas, 100% das 281 checks** — 41
retries vencedores, 217 conflitos respondidos com 409, 23 cancels no meio do
voo, nenhum resultado duplicado.

**O caos encontrou dois bugs reais antes de passar:**

1. **Commit depois da resposta (404 read-your-writes).** `GET /jobs/{id}` e
   `cancel` milissegundos após o `201` respondiam 404: o commit acontecia no
   teardown da dependency do FastAPI, que roda *depois* da resposta ser
   enviada. Um cliente rápido lia o banco antes do commit do próprio create.
   Curl manual nunca reproduz (lento demais); o k6 pegou em 35 de ~200 polls.
   Correção: commit explícito ao fim da unidade de trabalho no service, antes
   da resposta — que também libera o lock `FOR UPDATE` da company mais cedo.
2. **Contrato do replay quebrado (500).** `JobCreated.status` era o literal
   `'queued'`, mas o replay de uma `Idempotency-Key` devolve o job original —
   que pode já estar `done`. A validação de resposta explodia com 500.
   Correção: o schema do create aceita o domínio completo de status, com o
   replay documentado no OpenAPI.

## Onde estão os gargalos agora

1. **`count(*)` exato do `X-Total-Count`**: 97% do tempo de banco do cenário de
   leitura (24ms de média por página, contra 0,7ms da listagem). Custo
   proporcional ao volume do tenant, pago em toda página. Alavancas em
   [DECISIONS §7](../docs/case/DECISIONS.md#7-próximos-passos-com-mais-tempo).
2. **Worker: ~1 job/s por réplica** (o handler do case simula 1s de trabalho).
   Teto do caminho de escrita ponta a ponta; resolvido com réplicas, como a
   tabela acima demonstra.
3. **Pool de conexões da API** (lição do
   [caso da Shopify](https://shopify.engineering/scaling-inventory-reservations)):
   sob carga, parte da latência é fila por conexão, não query. As métricas
   `api_db_pool_requests_waiting` e `api_db_pool_utilization_ratio` existem
   exatamente para diagnosticar isso antes de culpar o banco.

## EXPLAIN ANALYZE por query (100k+ linhas)

| Query | Plano | Tempo |
|---|---|---|
| Listagem paginada (final) | Index Scan Backward `(company_id, created_at)` + subquery no índice único | 0,26ms |
| `count(*)` da empresa | Seq scan (empresa = 82% da tabela; com mais tenants o btree assume) | 8ms |
| Claim do worker | Index Scan `ix_jobs_queued` | 0,03ms |
| Recuperação de lease | Index `ix_jobs_running_lease` | 0,2ms |
| Auditoria do detalhe | Index por job | 0,006ms |
| Limite de concorrência | Index-only `ix_jobs_company_active` | 0,2ms |
| Admin (listagem/DLQ) | Index scans | < 0,1ms |
