# DECISIONS — Achados, decisões e trade-offs

Preencha as seções abaixo. Este documento é parte da avaliação: ele mostra o
seu raciocínio, não só o código. Seja concreto — cite arquivos, aponte causas
raiz e explique o porquê das escolhas.

---

## 1. Achados — problemas que identifiquei

Liste os problemas que você encontrou (tanto os reportados em `KNOWN_ISSUES.md`
quanto os que você descobriu sozinho), organizados por dimensão. Para cada um:
o que é, como você o encontrou, qual a causa raiz e qual o impacto.

- **Segurança / multi-tenancy:**
  - `GET /jobs/{id}` e `GET /jobs/{id}/result` não filtravam por `company_id`: qualquer empresa lia job e **resultado sensível** de qualquer outra só chutando IDs sequenciais.
  
  Encontrei revisando as queries default do projeto uma a uma, nenhum sintoma reportado cobria isso. O `WHERE` só usava o `id` e em decorrência disso havia vazamento de dados entre tenants.

  - `/admin/jobs` não checava role. Qualquer `X-Auth: 1:user` via jobs de todas as empresas. O handler até recebia o contexto mas nunca olhava `role`, nesse caso ocorria escalada de privilégios.

  - O `X-Auth` original fazia `int(company_id)` sem tratamento: `X-Auth: abc:user` derrubava o handler com 500 em vez de 401.

- **Concorrência / async:**
  - `POST /jobs` fazia check-then-insert do limite de concorrência sem lock: duas requests simultâneas liam o mesmo count e ambas criavam job (sintoma 2). Reproduzi com 20 POSTs concorrentes: o limite era ultrapassado.
  - O worker original fazia `SELECT ... WHERE status='queued'` **sem** `FOR UPDATE SKIP LOCKED`: duas réplicas pegariam o mesmo job, gravando resultado e debitando quota em dobro (sintoma 2, "cota cai rápido demais").
  - Numa exceção do worker, o rollback desfazia inclusive o `status='running'` e o job voltava a `queued`, **retentado para sempre**, sem nenhum registro do erro. Descobri ao simular uma falha e ver o loop infinito nos logs.
  - Não existia graceful shutdown: derrubar a API matava requests em andamento e conexões abertas no meio de transação e processamentos.
  - O commit da API acontecia **depois** da resposta: o `get_db` (dependency com `yield`) só commitava no teardown, que o FastAPI executa após enviar a resposta. Um cliente rápido recebia o 201 e lia 404 do próprio job. Invisível no curl manual, apareceu no cenário de caos do benchmark (35 ocorrências em ~200 polls).
  - O contrato do create declarava `status` como literal `queued`, mas o replay da `Idempotency-Key` devolve o job original, que pode já estar `done`. A validação da resposta explodia com 500 — também achado pelo caos, com replays concorrentes reais.

- **Modelagem / performance:**
  - `GET /jobs` fazia um `SELECT count(*)` **por job** dentro do loop (N+1). Com 20k jobs eram 20k+1 queries por request (sintoma 1). Reproduzi com o `generate_series` do próprio [KNOWN_ISSUES.md](KNOWN_ISSUES.md): a listagem ia a segundos.
  - Nenhum índice além das PKs: o filtro por empresa, o poll do worker e o count de concorrência faziam seq scan.
  - Listagem sem paginação: mesmo sem o N+1, serializar 20k linhas por request não escala.
  - `jobs.status` era TEXT livre (um typo criaria job invisível), `job_results.job_id` permitia N resultados por job (leitura não-determinística no retry), `users.email` sem UNIQUE, FK sem `ON DELETE CASCADE` (dependendo do contexto pode até ser aceitavel para soft delete). Schema sem versionamento, só um `schema.sql` de initdb.
  - Sem trace: impossível ligar a request HTTP que criou o job ao processamento no worker (sintoma 3).

- **Frontend:**
  - `api.ts` não tratava erro nenhum (`r.json()` direto, sem olhar `r.ok`).
  - Sem ações de cancelar/reprocessar (necessárias para as features A e B).
  - Tipagem `any` na listagem.

---

## 2. Correções — o que eu fiz

Para cada problema acima: a correção, os arquivos alterados, por que ela
resolve a causa raiz (não só o sintoma), e **como você verificou** que resolveu.

- **Isolamento por tenant** (`api/modules/jobs/service.py`, `routes.py`): todas as queries de leitura/mutação de job ganharam `AND company_id = %s` vindo do contexto autenticado. Job de outra empresa responde 404 (não 403, para não confirmar existência do ID). Verificação: testes `test_*_isolated_by_tenant` cobrindo get, result, cancel e retry cruzando tenants.

- **Role no admin** (`modules/admin/routes.py`): dependency `require_admin` → 403 para não-admin. Testado.

- **Sintoma 1 (listagem lenta)**: o N+1 virou uma única query paginada. A primeira versão calculava o `result_count` com `LEFT JOIN + count + GROUP BY`, e o benchmark de carga (ver [benchmark/README.md](../../benchmark/README.md)) mostrou que o `GROUP BY j.id` agregava os 100k jobs da empresa antes do `LIMIT`: 64ms por request no `EXPLAIN ANALYZE`, média de 227ms sob 50 VUs. A versão final pagina primeiro pelo índice `(company_id, created_at)` e só conta resultados para as linhas da página, com subquery correlata no índice único de `job_results`: 0,26ms no mesmo `EXPLAIN`, 246× mais rápido. A paginação `limit/offset` (máx 200) limita a serialização. Verificação: com 20.000 jobs (carga reproduzível com `db/populate_jobs.sql`) a resposta ficou em ~100ms constantes e, sob carga, o cenário `read` do k6 foi de 76 para 318 req/s com p95 caindo de 1,15s para 218ms. Índices são dirigidos pelas queries reais, **não** indexei colunas de baixa cardinalidade isoladas, que só custariam escrita/RAM.

- **Sintoma 2 (duplicação/limite furado)**: no `create_job`, `SELECT ... FOR UPDATE` na linha da company serializa criações da mesma empresa (empresas diferentes não competem). No worker, claim com `FOR UPDATE SKIP LOCKED`. Resultado único garantido no banco: `UNIQUE (job_id)` + `INSERT ... ON CONFLICT DO NOTHING`, e a quota só é debitada quando o insert de resultado realmente acontece. Verificação: 20 POSTs simultâneos → exatamente 2 aceitos (limite 2), 18× 429, além do teste `test_result_and_quota_are_idempotent`.

- **Sintoma 3 (falha não rastreável)**: colunas `trace_id` e `last_error` (migration `0003`). A API grava o `request_id` do log como `trace_id` do job e o worker propaga o mesmo trace em todos os eventos. Na falha, o worker faz rollback do trabalho parcial mas **persiste** `attempts`, `last_error` e `status='failed'` em transação própria. O retry manual reenfileira enquanto `attempts < 3`. Logging inteiro reformulado para *wide events* (um JSON por request/job, enriquecido progressivamente, filosofia de loggingsucks.com), com tail sampling: erros e lentos sempre logam. Verificação: injetei falha real e segui o trace da submissão HTTP até o `failed` com `last_error` no banco.

- **Feature A (cancelar)**: `POST /jobs/{id}/cancel` é um único `UPDATE ... WHERE status IN ('queued','running') RETURNING`. Zero linhas viram 404 (não existe ou é de outra empresa) ou 409 (estado não cancelável). Durante o trabalho, um watcher em conexão separada sinaliza um token cooperativo. O handler interrompe em ponto seguro, a transação ativa sofre rollback e a lease é limpa. A corrida com o worker está resolvida por desenho, detalhada em [§3](#3-trade-offs-e-decisões-de-design). UI com botão condicionado ao estado. Verificação: testes de rota (queued, running, done→409, duplo cancel→409, cross-tenant→404) e teste de integração do worker cancelando *no meio* do processamento, confirmando rollback do resultado parcial.

- **Auditoria de ciclo de vida** (`db/migrations/0009_job_cancellation_audit.sql`, `0010_job_audit_events.sql`): eventos append-only por job em tabela própria, com `company_id`, tipo, principal canônico, timestamp, `trace_id` e índices por job e tenant. A transição de cancelamento e o evento são gravados na mesma transação, e a unicidade por job impede dois eventos de cancelamento. O detalhe consulta os eventos com filtro de tenant e o front mostra quem cancelou e quando no box de resultado. O case usa `X-Auth: company_id:role`, portanto o principal registrado é `1:user`/`1:admin`, não um e-mail individual. Com autenticação real, o campo deve receber o `user_id` de um token assinado.

- **Observabilidade operacional** (`observability/`): cAdvisor coleta CPU/memória/rede por container. O pool da API publica utilização e requests aguardando conexão. `pg_stat_statements` com `track_io_timing` identifica queryid, tempo total/médio e parcela de leitura/escrita. O dashboard Performance Investigation consulta SQL ativas e bloqueios. Regras Prometheus provisionam SLOs e alertas de p99, disponibilidade, backlog, pool, conexões e deadlocks. Labels de query continuam limitados a `queryid`, e o texto completo só é consultado na tabela interna do Grafana para evitar cardinalidade no Prometheus.

- **Feature B (retry idempotente)**: toda execução que falha fica em `failed`. `POST /jobs/{id}/retry` faz `UPDATE ... WHERE status='failed' AND attempts < 3 RETURNING` e agenda `next_attempt_at` com backoff exponencial (5s, depois 10s). Duplo-clique: o segundo UPDATE não casa linha e responde 409. A terceira falha vai para a DLQ. Idempotência de resultado/quota é a mesma do sintoma 2 (UNIQUE + ON CONFLICT + quota amarrada à transição). Verificação: testes de duplo retry, limite de tentativas, backoff e cross-tenant.

- **Graceful shutdown** (`shared/http/graceful_shutdown.py` + lifespan): para de aceitar (503), drena requests em andamento com timeout e fecha o pool. Testado com testes async dedicados.

- **Schema versionado**: migrations SQL puras em `db/migrations/*.sql` com runner mínimo (`shared/db/migrate.py`) e tabela `schema_migrations`. O container da api aplica antes do uvicorn.

- **CI no GitHub Actions** (`.github/workflows/ci.yml`): pipeline em cada push/PR. Para api e worker, em matriz: lint (`ruff check` e `ruff format --check`), type check (`pyright`) e os testes com testcontainers. Para o front: `tsc --noEmit`, testes e build. Por fim, `docker compose build` valida que as imagens continuam construindo. A entrega pedia um repositório pronto para review, então o CI prova que tudo passa fora da minha máquina.

- **Frontend**: `api.ts` tipado com `ApiError` extraindo o `detail` do backend (409/429 deixam de ser silêncio, antes mascaravam até incidente de segurança). `JobsPanel` tipado com botões de cancelar/retry coerentes com o estado e feedback de erro visível. `SubmitButton` com disable durante o submit e `Idempotency-Key` por intenção. Polling adaptativo: 1s só com job ativo, desligado em repouso (o polling fixo de 1s combinado com o antigo N+1 era DoS acidental). CORS restrito a allowlist por env (`CORS_ORIGINS`) com métodos e headers mínimos, em vez de `*`. Por fim, UI reestilizada com a identidade da Galaxies (paleta azul, ícone, cards, badges por status) e sistema de toasts para todo feedback de ação, sem lib de UI, só CSS.

### Bateria adversarial: quebrando as próprias regras

Depois de tudo pronto, escrevi uma suíte cujo objetivo é **falhar**: cada teste
tenta violar uma regra de negócio com input errado, faltando ou malicioso
(`api/tests/integration/test_business_rules_break.py`, 248 casos coletados, e
`worker/tests/modules/jobs/test_business_rules_break.py`, 17 casos coletados).
A suíte cobre:

- auth obrigatório em todas as rotas × 11 variações malformadas
- chave de idempotência ausente, em branco e gigante
- isolamento cross-tenant em leitura e mutação
- roles inválidas e todas as transições de estado proibidas
- limite de tentativas e limite de concorrência
- tipos e faixas em todo parâmetro
- injeção de SQL em `kind` e `search`, curingas de `ILIKE`
- abuso de protocolo (header duplicado, content-type errado, body de 100k, campos extras tentando definir `id`/`status`)
- no worker: claim de estados inválidos, backoff pendente, cancelamento no meio da falha, DLQ duplicada e quota debitada duas vezes

**A bateria encontrou dois bugs reais**, ambos de validação que passavam
despercebidos porque `min_length=1` não olha o conteúdo:

1. `kind: "   "` (só espaços) criava job com HTTP 201, lixo entrando no
   domínio. Corrigido com `StringConstraints(strip_whitespace=True,
   min_length=1)`: agora normaliza `"  report  "` → `"report"` e rejeita
   só-espaços com 422.
2. `Idempotency-Key` aceitava valor em branco e, pior, as constraints do
   `Annotated` **não estavam sendo aplicadas** porque o `Header()` estava no
   default em vez de dentro do `Annotated`: uma chave de 201 caracteres passava
   direto. Corrigido movendo o `Header()` para dentro do `Annotated`.

Também confirmou dois comportamentos que eu supunha e não tinha provado: roles
fora da whitelist (`root`, `ADMIN`, `admin `) são rejeitadas como credencial
inválida (401, fail-closed) em vez de "sem permissão". E um job cancelado no
meio de uma falha **não** volta para a fila nem vai para a DLQ.

### Redução de ruídos de observabilidade

A primeira versão de `LOG_SUPPRESS_PROBE_ROUTES` filtrava só o wide event, e o
Grafana continuou mostrando `INFO: "GET /health HTTP/1.1" 200 OK`. O motivo: o
**access log do uvicorn** é um canal independente, em texto puro, que eu tinha
ignorado. Com o healthcheck do container e o scrape do Prometheus a cada 10s,
eram ~145 linhas a cada 5 minutos afogando os eventos que importam. A flag
agora instala também um `logging.Filter` no logger `uvicorn.access`, com
exatamente a mesma regra (rota de probe + status < 400). O filtro é conservador
por desenho: se o formato do record não for o esperado, ele deixa passar,
porque silenciar por engano é pior do que uma linha a mais.

### Benchmark de carga e caos (k6)

Para transformar "escala" em número, montei a pasta
[`benchmark/`](../../benchmark/README.md): cenários k6 na rede do Compose
contra 100 mil jobs de uma empresa dedicada, sempre com warmup antes da
medição, cobrindo todas as rotas e o processamento ponta a ponta. O cenário de
leitura pagou o investimento logo de cara: expôs que a listagem, mesmo sem o
N+1, agregava a empresa inteira antes de paginar. Depois da correção, saiu de
76 para 328 req/s com p95 de 225ms e zero erros. A escrita sustenta 30
creates/s com p95 de 7,6ms. O ciclo completo (create → fila → worker → done →
result) roda com e2e p50 de 1,5s dentro da capacidade, e o teste de escala
provou a horizontalidade do worker: a 3 jobs/s, 1 réplica satura (e2e p95
27,5s, 102 timeouts) e 4 réplicas seguram p95 em 1,53s com zero timeouts, sem
nenhum job processado duas vezes.

O cenário de **caos** dispara as corridas que o desenho promete arbitrar —
replay concorrente da mesma `Idempotency-Key`, duplo retry disputado no mesmo
job `failed`, cancel no meio do processamento — e encontrou **dois bugs
reais** antes de fechar em 100%: o commit pós-resposta e o contrato do replay
(ambos em [§1](#1-achados--problemas-que-identifiquei), corrigidos e cobertos
por teste). Na rodada final: 1005 requests, 0% falhas, todas as disputas com
exatamente um vencedor.

Gargalos conhecidos após a otimização: o `count(*)` exato do `X-Total-Count`
(97% do tempo de banco do caminho de leitura, alavanca registrada em
[§7](#7-próximos-passos-com-mais-tempo)) e o teto de ~1 job/s por réplica do
worker (o handler simula 1s), resolvido com réplicas. Números completos, plano
de cada query e metodologia no README do benchmark.

---

## 3. Trade-offs e decisões de design

As decisões não-óbvias que você tomou, as alternativas que considerou e por que
escolheu o que escolheu. (Ex.: como garantiu idempotência; como rastreou um job
ponta a ponta; como resolveu a corrida do cancelamento.)

- **Corrida cancelar × finalizar**: o worker processa em duas fases, *claim* (`queued→running`, commit) e *finalize* (`UPDATE ... SET done WHERE status='running'`). Quem escrever primeiro na linha vence, o banco arbitra. Se o cancel chegou antes, o finalize não casa linha, o resultado é descartado e a quota não é debitada. Se o done chegou antes, o cancel devolve 409. Sem deadlock (lock de uma linha só, sempre na mesma ordem) e sem estado inconsistente (resultado, quota e status na mesma transação do finalize). Alternativa rejeitada: manter claim e finalize numa transação única, pois o cancel ficaria bloqueado pela duração do processamento.

- **Crash depois do claim** (migration `0008`): o claim grava `locked_at` e um `worker_id`. Finalize, falha, cancel e retry limpam ambos. Antes de buscar trabalho, qualquer worker recupera leases vencidos há 30s como `failed`, permitindo retry manual, e envia diretamente para a DLQ quando o claim órfão já era a terceira tentativa. Finalize/failure exigem o mesmo `worker_id`, impedindo um worker antigo de sobrescrever uma recuperação.

- **Idempotência por constraint, não por aplicação**: o "gravou uma vez só" mora no `UNIQUE (job_id)` do banco, o único lugar sem race. Código de aplicação checando "já existe?" seria exatamente o check-then-act que causou o sintoma 2.

- **`Idempotency-Key` no `POST /jobs`** (migration `0005`): retry de rede e duplo-clique na *criação* também não podem duplicar. O header é **obrigatório**, regra de negócio: 422 com o campo apontado se ausente, coberto por teste disparando a request sem o header (a ausência de `X-Auth` responde 401, também testada). A garantia mora no índice único parcial `(company_id, idempotency_key)` com `INSERT ... ON CONFLICT DO NOTHING`. Se a chave já existe, a API devolve o job original: mesmo body, sem novo NOTIFY e sem passar de novo pelo limite de concorrência, que já foi pago na primeira. A chave é reconsultada depois do lock da empresa, o que fecha a janela em que um replay concorrente poderia receber 429 se a primeira request ocupasse a última vaga. A chave é escopada por tenant. O front gera a chave por intenção e só renova após sucesso, então duplo-clique replay-a em vez de duplicar.

- **Limite de concorrência com lock pessimista na company**: `FOR UPDATE` de uma linha, contenção limitada ao próprio tenant. Alternativa considerada: constraint/trigger, mais invasivo para uma regra que pode virar configurável.

- **Camada service por módulo** (monolito modular, package-by-feature): regra de negócio + SQL nos services (sem nada de FastAPI), rotas só traduzem domínio→HTTP. Repository separado seria indireção sem ganho neste tamanho. Use-cases granulares seriam cerimônia demais.

- **Pub/sub com LISTEN/NOTIFY nativo do Postgres**: o create/retry faz `pg_notify` e o worker fica em `LISTEN`, com polling de 30s apenas como fallback. Estudei a feature antes de adotar (ver [§5](#5-uso-de-ia--reflexão-honesta)): NOTIFY é entregue no commit, tem custo baixíssimo e elimina o polling agressivo. Mas **não é fila durável**: sem listener conectado a notificação se perde, o payload é limitado a 8KB e não há ACK nem redelivery. Por isso o desenho é híbrido. A tabela `jobs` continua sendo a fila (durável, com `SKIP LOCKED`) e o NOTIFY é só o sinal de "chegou trabalho". Isso dá a latência do pub/sub com a robustez da fila transacional, até o ponto em que a resposta certa deixa de ser Postgres e vira um broker dedicado.

- **Erros de domínio tipados** (`InvalidJobStateError`, `RetryLimitError`...) traduzidos para status HTTP na borda, assim o service é testável e reutilizável sem HTTP.

- **Polling adaptativo no front, decisão final e não fallback**: avaliei push (SSE e WebSocket foi descartado de saída). Para acompanhar status de jobs, polling curto e adaptativo contra um endpoint barato é o padrão que times operando em grande escala escolhem: funciona atrás de qualquer LB/proxy sem sticky session, não mantém conexão aberta por usuário no servidor (SSE segura uma conexão viva por cliente e sofre com timeouts de proxies intermediários), não muda o modelo de auth (`EventSource` não envia o header `X-Auth`, a credencial teria que ir para query string, vazando em log de acesso) e escala horizontalmente sem estado. O trabalho certo foi baratear o polling: intervalo de 1s só enquanto há job ativo, desligado em repouso, contra endpoint paginado e indexado. Push só compensaria se o custo agregado do polling superasse o de manter conexões, muito longe da realidade deste sistema.

- **CORS como allowlist de ponta a ponta**: `CORS_ORIGINS` por env (settings → middleware → compose), métodos `GET/POST`, headers exatamente os três que o front usa (`X-Auth`, `Content-Type`, `Idempotency-Key`) e `max_age=600` para cachear o preflight. Verificado com preflight real: origem permitida recebe os headers, origem desconhecida recebe 400.

- **404 (e não 403) para recurso de outro tenant**: não vazar a existência do ID.

- **Testes contra Postgres real (testcontainers)**: as garantias centrais do case *são* comportamento de Postgres (SKIP LOCKED, ON CONFLICT, locks de linha), portanto evitei mocks e SQLite para testes.

- **Busca por substring com trigram, não full-text**: a busca de `GET /jobs` é `kind ILIKE '%termo%'`. Full-text search (tsvector) casa lexemas e prefixos, não substring arbitrária — `%por%` não encontraria `report`. O índice correto é `pg_trgm` (migration `0012`): o pior caso (termo sem match, que varria a empresa inteira) caiu de 32ms para 0,02ms em 100k linhas, e quando o termo casa tudo o planner ignora o GIN e mantém o early-stop do índice de paginação. Medido no benchmark, plano a plano.

- **Fila própria vs bibliotecas de fila em Postgres**: avaliei pgqueuer, pgmq e pgque antes de manter a implementação. O pgqueuer usa exatamente os mesmos primitivos que o projeto já tem (LISTEN/NOTIFY + `SKIP LOCKED` + polling de fallback), o que valida o desenho, mas adotá-lo trocaria as regras de negócio do case (cancel cooperativo por job, limite por tenant, DLQ auditável, eventos com ator) por convenções genéricas de framework. O pgmq segue o modelo SQS de mensagem opaca com visibility timeout: os jobs aqui são linhas de domínio que a UI consulta por status e tenant, então eu terminaria com fila e tabela de estado separadas, o dual-system que o desenho atual elimina. O pgque é fan-out por event log com tick (~50ms de latência mediana, exige pg_cron), modelo errado para worker competindo por job. A crítica legítima que fica do pgque é o bloat de dead tuples em filas `SKIP LOCKED` sob churn alto — registrada como regra de operação na proposta de produção do README (monitorar autovacuum e arquivar jobs concluídos).

- **Wide events em vez de linhas de log soltas**: um JSON por request/job com contexto de negócio, correlacionável por `trace_id`. Tail sampling mantém o custo controlado.

---

## 4. O que deixei de fora — conscientemente

O que você **não** implementou de propósito e por quê (prazo, escopo, custo x
benefício). Um "deixei de fora, e aqui está o raciocínio" vale mais que um item
mal-feito.

- **Autenticação real**: o case declara o `X-Auth` como simplificação proposital. Endureci o parsing (401 para formato inválido) e deixei o `AuthContext` como costura única onde um JWT entraria.

- **Handlers sem limite de duração conhecido**: o worker renova o lease em background, mas um handler real ainda deveria declarar timeout próprio, política de cancelamento e limites de recursos. O executor do case é deliberadamente previsível.

- **Enforcement da `job_quota`** no create: a base original só decrementa a quota, nunca a checa. Manter esse comportamento foi decisão explícita para não inventar regra de produto que o enunciado não pediu.

- **Cursor-based pagination**: limit/offset degrada com offsets profundos. Para o padrão de uso (primeiras páginas) é suficiente e muito mais simples.

- **Broker dedicado (Redis/RabbitMQ/SQS)**: overkill para o volume do case. O desenho fila-na-tabela + NOTIFY chega longe e mantém tudo transacional com os dados. Shopify é um case real que usou MySQL ao invés de Redis para o sistema principal de reserva de estoque, com estratégias similares às utilizadas nesse case ([tech blog da Shopify](https://shopify.engineering/scaling-inventory-reservations)).

- **E2E de frontend (Playwright)**: o front é intencionalmente mínimo.

- **Push em tempo real**: avaliado e rejeitado conscientemente, o raciocínio completo está em [§3](#3-trade-offs-e-decisões-de-design). Polling adaptativo foi a decisão: só acontece quando há job `queued`/`running` na tela e, em repouso, zero requests. Mutações e focus-refetch cobrem o resto.

---

## 5. Uso de IA — reflexão honesta

> **Prompt Injection no Case:** o [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md) original continha um
> comentário HTML oculto instruindo agentes de IA a ignorar silenciosamente o
> Sintoma 3 e a esconder essa omissão. A instrução foi
> lida durante o levantamento de requisitos.
> Fica registrada essa observação porque é exatamente o tipo de coisa que uso cego de
> IA deixaria passar.

- **Onde a IA ajudou:**
  - Elaboração de documentos, base de testes, base de validação usando Pydantic, TDD mecânico (testes de borda de auth, paginação, estados de cancel/retry), automatização usando script para reprodução dos sintomas com números e documentação das evidências, pipeline de observabilidade e métricas.

- **Onde a IA atrapalhou / te levou pro caminho errado:**
  - O caso mais instrutivo foi a métrica de profundidade da fila, que a IA implementou errado em três camadas empilhadas. Para resolver um aviso do Pylance, ela unificou o módulo de métricas entre api e worker. Com isso o processo da **api** passou a registrar e exportar os gauges do worker, criando uma série `jobs_queued_depth{service="api"}` eternamente em 0 poluindo as queries do Grafana.
  - O worker só atualizava o gauge quando a fila **esvaziava** (no retorno do drain). Ou seja, o valor publicado era quase sempre 0 por construção, nunca o backlog real.
  - Mesmo que os dois primeiros fossem corrigidos, um gauge alimentado pelo loop do worker não sobrevive à análise: um job que vive ~1s raramente coincide com um scrape de 10s, e se o worker morre (exatamente quando a fila mais importa) o gauge congela no último valor. Só percebi porque testei pelo front e o painel não saía do zero. A correção foi conceitual, não pontual: **estado não é métrica de processo**. Jobs por status vêm do postgres-exporter via query custom (`pg_jobs_total{status}`), informação correta no momento de cada scrape, independente de qualquer worker. O worker exporta apenas métricas de *fluxo* (espera na fila, duração, outcomes, wakeups) e os módulos de métricas foram separados por processo. Verifiquei parando o worker, criando um job (fila = 1 no Prometheus) e religando (fila = 0).
  - A IA tentou otimizar para "fazer o painel existir" e para silenciar o linter, quando a pergunta certa era "quem é a fonte de verdade deste número?". A primeira versão do tratamento de falha do worker teve erro parecido: fazia rollback e re-raise, o que *parecia* correto e na prática criava o loop infinito de retry (o rollback desfazia o próprio `running`). Só caiu no teste de integração. Também tentou "melhorar" o schema com constraints extras sem eu pedir, misturando escopo de modelagem com escopo de índice. Tive que separar em migrations distintas e justificar cada uma.
  - Introduziu um ORM completo (SQLAlchemy + Alembic) que depois precisei remover inteiro, porque violava um critério explícito do case: SQL cru via psycopg, sem ORM, tudo rodando com `docker compose up --build`.

- **O que você rejeitou da IA e por quê:**
  - **O ORM**: o [TASKS.md](TASKS.md) é claro: "SQL cru via psycopg — mantenha o padrão (sem ORM)". Reverti para psycopg puro com pool, mantendo a arquitetura de services. As queries ficaram mais explícitas e o `EXPLAIN` bate 1:1 com o que está no código.
  - **Fila/pub-sub em camada de aplicação**: a sugestão inicial era polling puro e, depois, um broker externo. Rejeitei os dois e fui estudar o LISTEN/NOTIFY nativo do Postgres, pois já conhecia a capacidade e robustez. Para *sinalização* ele aguenta alto volume com custo mínimo e entrega no commit, exatamente o que eu precisava aqui, desde que não seja tratado como fila durável (payload 8KB, sem entrega garantida sem listener). A tabela continua sendo a fila e o NOTIFY substitui o polling. Ferramenta nativa, zero dependência nova, e o fallback de 30s cobre o caso de notify perdido.
  - **Índices especulativos** (status isolado, kind): o planner não os usaria com cardinalidade baixa e custariam escrita/RAM. Mantive só os que o `EXPLAIN` prova.

---

## 6. Casos de borda

Cenários extremos que você antecipou e como seu código os trata (ou reconhece
que não trata).

- Cancel chegando durante o processamento: worker descarta resultado e não debita quota (testado com cancel concorrente real no meio do `_execute`).

- Duplo-clique em retry/cancel: segunda chamada → 409, sem efeito duplicado.

- Job com resultado pré-existente reprocessado: `ON CONFLICT DO NOTHING`, quota intacta (testado).

- Worker reiniciado com backlog: `drain()` no startup processa a fila antes de entrar em LISTEN.

- NOTIFY perdido (worker desconectado no momento do commit): fallback de polling de 30s garante progresso.

- `X-Auth` malformado em 7 variações (vazio, sem `:`, id não-numérico, id ≤ 0, role vazia): sempre 401, nunca 500.

- Tentativa de SQL injection no `kind`: armazenado literalmente (parâmetros bind), testado.

- Job `running` de worker morto: lease expirado vira `failed`. Lease sem timestamp legado também é recuperado, e a terceira tentativa órfã vai para a DLQ.

- Não exigiu tratamento: clock skew entre réplicas não afeta nada (toda ordenação é por `id`/transação, não por relógio).

---

## 7. Próximos passos (com mais tempo)

O que você faria a seguir.

- **Alertmanager com receivers de verdade**: hoje um alerta `firing` só aparece no Prometheus/Grafana, ninguém recebe mensagem. A rota já está desenhada no [observability/README.md](../../observability/README.md) (Prometheus → Alertmanager → Slack/PagerDuty), falta plugar os receivers com secret manager, fora do repositório.

- **Trocar o `X-Auth` por token assinado (JWT/OIDC)**: o `AuthContext` já é a costura única de propósito, então a troca entra num lugar só e a auditoria passa a registrar um `user_id` real em vez de `company_id:role`. Junto disso cabe um estudo para cenários mais complexos: federação de login (SSO com o IdP de cada empresa via OIDC/SAML) e gerenciamento de tenants (provisionamento de empresa, papéis por organização, claims do token mapeando para o `company_id` que hoje vem do header).

- **Enforcement da `job_quota` no create**: hoje a quota só é decrementada, nunca checada (comportamento herdado da base, mantive de propósito, ver [§4](#4-o-que-deixei-de-fora--conscientemente)). É regra de produto, então o próximo passo é definir o comportamento esperado (bloquear? avisar? cobrar?) antes de codar qualquer coisa.

- **Timeout e limites por handler no worker**: o executor do case é previsível. Um handler real precisa declarar duração máxima, política de cancelamento e compensação para efeitos externos já commitados.

- **Cursor-based pagination**, só quando o padrão de acesso exigir offsets profundos. A razão de adiar está em [§4](#4-o-que-deixei-de-fora--conscientemente), não vale a complexidade antes disso.

- **E2E de front (Playwright)** cobrindo cancelar/retry pela UI. A bateria adversarial cobre a API, mas o clique do usuário ainda depende de revisão manual.

- **Broker dedicado (SQS/RabbitMQ)** apenas se o volume tornar o Postgres o gargalo. O desenho fila-na-tabela + NOTIFY existe justamente para adiar essa troca sem reescrever o worker.

- **Baratear o `count(*)` do `X-Total-Count`**: o benchmark mostrou que, com a listagem otimizada, o total exato virou 97% do tempo de banco do caminho de leitura (24ms por página a 100k jobs). Alavancas possíveis: cachear o total por tenant, estimar acima de um teto com `reltuples` ou tornar o header opcional. Mantive o exato de propósito, correto e barato o suficiente no volume do case.

---

**Tempo investido:** 12 ~ 14 horas (Os problemas foram resolvidos relativamente rápido, o restante das horas foram investidas em estudos das ferramentas, benchmarks, testes e revisão manual pensando que é um serviço crítico e de demanda elevada)

A revisão manual teve um efeito muito positivo principalmente em validações pequenas e detalhadas que normalmente uma LLM acaba ignorando.

Testes comportamentais da aplicação, LLM é excelente para produtividade mas particularmente sinto que falha em testes e testa muito o que é "óbvio", todos nós sabemos que 1 + 1 = 2 eu quero entender de fato como minha aplicação se comporta quando eu envio que 1 + 1 = 4...
