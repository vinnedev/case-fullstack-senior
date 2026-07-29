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
  - `GET /jobs/{id}` e `GET /jobs/{id}/result` não filtravam por `company_id` — qualquer empresa lia job e **resultado sensível** de qualquer outra só chutando IDs sequenciais.
  
  Encontrei revisando as queries default do projeto uma a uma, nenhum sintoma reportado cobria isso. O `WHERE` só usava o `id` em decorrência disso havia vazamento de dados entre tenants.

  - `/admin/jobs` não checava role — qualquer `X-Auth: 1:user` via jobs de todas as empresas. O handler até recebia o contexto mas nunca olhava `role`, nesse caso ocorria escalada de privilégios.

  - O `X-Auth` original fazia `int(company_id)` sem tratamento — `X-Auth: abc:user` derrubava o handler com 500 em vez de 401.

- **Concorrência / async:**
  - `POST /jobs` fazia check-then-insert do limite de concorrência sem lock: duas requests simultâneas liam o mesmo count e ambas criavam job (sintoma 2). Reproduzi com 20 POSTs concorrentes: o limite era ultrapassado.
  - O worker original fazia `SELECT ... WHERE status='queued'` **sem** `FOR UPDATE SKIP LOCKED` — duas réplicas pegariam o mesmo job, gravando resultado e debitando quota em dobro (sintoma 2, "cota cai rápido demais").
  - Numa exceção do worker, o rollback desfazia inclusive o `status='running'` — o job voltava a `queued` e era **retentado para sempre**, sem nenhum registro do erro. Descobri ao simular uma falha e ver o loop infinito nos logs.
  - Não existia graceful shutdown: derrubar a API matava requests em andamento e conexões abertas no meio de transação e processamentos.

- **Modelagem / performance:**
  - `GET /jobs` fazia um `SELECT count(*)` **por job** dentro do loop (N+1). Com 20k jobs eram 20k+1 queries por request (sintoma 1). Reproduzi com o `generate_series` do próprio KNOWN_ISSUES: a listagem ia a segundos.
  - Nenhum índice além das PKs: o filtro por empresa, o poll do worker e o count de concorrência faziam seq scan.
  - Listagem sem paginação: mesmo sem o N+1, serializar 20k linhas por request não escala.
  - `jobs.status` era TEXT livre (um typo criaria job invisível), `job_results.job_id` permitia N resultados por job (leitura não-determinística no retry), `users.email` sem UNIQUE, FK sem `ON DELETE CASCADE` (dependendo do contexto pode até ser aceitavel para soft delete). Schema sem versionamento — só um `schema.sql` de initdb.
  - Sem trace: impossível ligar a request HTTP que criou o job ao processamento no worker (sintoma 3).

- **Frontend:**
  - `api.ts` não tratava erro nenhum (`r.json()` direto, sem olhar `r.ok`).
  - Sem ações de cancelar/reprocessar (necessárias para as features A e B).
  - Tipagem `any` na listagem.

## 2. Correções — o que eu fiz

Para cada problema acima: a correção, os arquivos alterados, por que ela
resolve a causa raiz (não só o sintoma), e **como você verificou** que resolveu.

- **Isolamento por tenant** (`api/modules/jobs/service.py`, `routes.py`): todas as queries de leitura/mutação de job ganharam `AND company_id = %s` vindo do contexto autenticado. Job de outra empresa responde 404 (não 403, para não confirmar existência do ID). Verificação: testes `test_*_isolated_by_tenant` — get, result, cancel e retry cruzando tenants.

- **Role no admin** (`modules/admin/routes.py`): dependency `require_admin` → 403 para não-admin. Testado.

- **Sintoma 1 (listagem lenta)**: o N+1 virou uma única query com `LEFT JOIN + count + GROUP BY`; índice `(company_id, created_at)` cobre filtro+ordenação; paginação `limit/offset` (máx 200) limita a serialização. Verificação: com 20.000 jobs a resposta ficou em ~100ms constantes. `EXPLAIN` confirma o índice. Índices são dirigidos pelas queries reais, deliberadamente **não** indexei colunas de baixa cardinalidade isoladas, que só custariam escrita/RAM.

- **Sintoma 2 (duplicação/limite furado)**: no `create_job`, `SELECT ... FOR UPDATE` na linha da company serializa criações da mesma empresa (empresas diferentes não competem). No worker, claim com `FOR UPDATE SKIP LOCKED`. Resultado único garantido no banco: `UNIQUE (job_id)` + `INSERT ... ON CONFLICT DO NOTHING`, e a quota só é debitada quando o insert de resultado realmente acontece. Verificação: 20 POSTs simultâneos → exatamente 2 aceitos (limite 2), 18× 429; teste `test_result_and_quota_are_idempotent`.

- **Sintoma 3 (falha não rastreável)**: colunas `trace_id` e `last_error` (migration `0003`). A API grava o `request_id` do log como `trace_id` do job; o worker propaga o mesmo trace em todos os eventos. Na falha, o worker faz rollback do trabalho parcial mas **persiste** `attempts`, `last_error` e `status='failed'` em transação própria; o retry manual reenfileira enquanto `attempts < 3`. Logging inteiro reformulado para *wide events* (um JSON por request/job, enriquecido progressivamente — filosofia de loggingsucks.com), com tail sampling: erros e lentos sempre logam. Verificação: injetei falha real e segui o trace da submissão HTTP até o `failed` com `last_error` no banco.

- **Feature A (cancelar)**: `POST /jobs/{id}/cancel` — um único `UPDATE ... WHERE status IN ('queued','running') RETURNING`; 0 linhas → 404 (não existe/na outra empresa) ou 409 (estado não cancelável). Durante o trabalho, um watcher em conexão separada sinaliza um token cooperativo; o handler interrompe em ponto seguro, a transação ativa sofre rollback e a lease é limpa. A corrida com o worker está resolvida por desenho, ver §3. UI com botão condicionado ao estado. Verificação: testes de rota (queued, running, done→409, duplo cancel→409, cross-tenant→404) e teste de integração do worker cancelando *no meio* do processamento, confirmando rollback do resultado parcial.

- **Auditoria de ciclo de vida** (`db/migrations/0009_job_cancellation_audit.sql`, `0010_job_audit_events.sql`): eventos append-only por job em tabela própria, com `company_id`, tipo, principal canônico, timestamp, `trace_id` e índices por job e tenant. A transição de cancelamento e o evento são gravados na mesma transação; a unicidade por job impede dois eventos de cancelamento. O detalhe consulta os eventos com filtro de tenant e o front mostra quem cancelou e quando no box de resultado. O case usa `X-Auth: company_id:role`, portanto o principal registrado é `1:user`/`1:admin`, não um e-mail individual; com autenticação real, o campo deve receber o `user_id` de um token assinado.

- **Observabilidade operacional** (`observability/`): cAdvisor coleta CPU/memória/rede por container; o pool da API publica utilização e requests aguardando conexão; `pg_stat_statements` com `track_io_timing` identifica queryid, tempo total/médio e parcela de leitura/escrita; o dashboard Performance Investigation consulta SQL ativas e bloqueios; regras Prometheus provisionam SLOs e alertas de p99, disponibilidade, backlog, pool, conexões e deadlocks. Labels de query continuam limitados a `queryid`; o texto completo só é consultado na tabela interna do Grafana para evitar cardinalidade no Prometheus.

- **Feature B (retry idempotente)**: toda execução que falha fica em `failed`; `POST /jobs/{id}/retry` faz `UPDATE ... WHERE status='failed' AND attempts < 3 RETURNING` e agenda `next_attempt_at` com backoff exponencial (5s, depois 10s). Duplo-clique: o segundo UPDATE não casa linha → 409. A terceira falha vai para a DLQ. Idempotência de resultado/quota é a mesma do sintoma 2 (UNIQUE + ON CONFLICT + quota amarrada à transição). Verificação: testes de duplo retry, limite de tentativas, backoff e cross-tenant.

- **Graceful shutdown** (`shared/http/graceful_shutdown.py` + lifespan): para de aceitar (503), drena requests em andamento com timeout, fecha o pool. Testado com testes async dedicados.

- **Schema versionado**: migrations SQL puras em `db/migrations/*.sql` + runner mínimo (`shared/db/migrate.py`) com tabela `schema_migrations`; o container da api aplica antes do uvicorn.

- **Frontend**: `api.ts` tipado com `ApiError` extraindo o `detail` do backend (409/429 deixam de ser silêncio — mascaravam até incidente de segurança); `JobsList` tipado com botões de cancelar/retry coerentes com o estado e feedback de erro visível; `SubmitForm` com disable durante o submit e `Idempotency-Key` por intenção; polling adaptativo (1s só com job ativo, desligado em repouso — o polling fixo de 1s combinado com o antigo N+1 era DoS acidental); CORS restrito a allowlist por env (`CORS_ORIGINS`) com métodos e headers mínimos, em vez de `*`. Por fim, UI reestilizada com a identidade da Galaxies (paleta azul, ícone, cards, badges por status) e sistema de toasts para todo feedback de ação — sem lib de UI, só CSS.

## 3. Trade-offs e decisões de design

As decisões não-óbvias que você tomou, as alternativas que considerou e por que
escolhi o que escolhi:

- **Corrida cancelar × finalizar**: o worker processa em duas fases — *claim* (`queued→running`, commit) e *finalize* (`UPDATE ... SET done WHERE status='running'`). Quem escrever primeiro na linha vence, o banco arbitra: se o cancel chegou antes, o finalize não casa linha, o resultado é descartado e a quota não é debitada; se o done chegou antes, o cancel devolve 409. Sem deadlock (lock de uma linha só, sempre na mesma ordem) e sem estado inconsistente (resultado+quota+status na mesma transação do finalize). Alternativa rejeitada: manter claim e finalize numa transação única — o cancel ficaria bloqueado pela duração do processamento.

- **Crash depois do claim** (migration `0008`): o claim grava `locked_at` e um `worker_id`; finalize, falha, cancel e retry limpam ambos. Antes de buscar trabalho, qualquer worker recupera leases vencidos há 30s como `failed`, permitindo retry manual, e envia diretamente para a DLQ quando o claim órfão já era a terceira tentativa. Finalize/failure exigem o mesmo `worker_id`, impedindo um worker antigo de sobrescrever uma recuperação.

- **Idempotência por constraint, não por aplicação**: o "gravou uma vez só" mora no `UNIQUE (job_id)` do banco, o único lugar sem race. Código de aplicação checando "já existe?" seria exatamente o check-then-act que causou o sintoma 2.
- **`Idempotency-Key` no `POST /jobs`** (migration `0005`): retry de rede/duplo-clique na *criação* também não pode duplicar. Header **obrigatório** (regra de negócio: 422 com o campo apontado se ausente — coberto por teste disparando a request sem o header, assim como a ausência de `X-Auth` → 401); índice único parcial `(company_id, idempotency_key)` + `INSERT ... ON CONFLICT DO NOTHING` — se a chave já existe, devolve o job original (mesmo body, sem novo NOTIFY, sem passar de novo pelo limite de concorrência, que já foi pago na primeira). A chave é reconsultada depois do lock da empresa: isso fecha a janela em que um replay concorrente poderia receber 429 se a primeira request ocupasse a última vaga. Chave escopada por tenant. O front gera a chave por intenção (renova só após sucesso), então duplo-clique replay-a em vez de duplicar.

- **Limite de concorrência com lock pessimista na company**: `FOR UPDATE` de uma linha, contenção limitada ao próprio tenant. Alternativa considerada: constraint/trigger — mais invasivo para uma regra que pode virar configurável.

- **Camada service por módulo** (monolito modular, package-by-feature): regra de negócio + SQL nos services (sem nada de FastAPI), rotas só traduzem domínio→HTTP. Repository separado seria indireção sem ganho neste tamanho; use-cases granulares, cerimônia demais.

- **Pub/sub com LISTEN/NOTIFY nativo do Postgres**: o create/retry faz `pg_notify` e o worker fica em `LISTEN`, com polling de 30s apenas como fallback. Estudei a feature antes de adotar (ver §5): NOTIFY é entregue no commit, tem custo baixíssimo e elimina o polling agressivo; mas **não é fila durável** — sem listener conectado a notificação se perde, payload máx. 8KB, sem ACK/redelivery. Por isso o desenho é híbrido: a tabela `jobs` continua sendo a fila (durável, com `SKIP LOCKED`), e o NOTIFY é só o sinal de "chegou trabalho". Isso dá a latência do pub/sub com a robustez da fila transacional — e escala até o ponto em que a resposta certa deixa de ser Postgres e vira um broker dedicado. (Suporta 50k jobs por seg.)

- **Erros de domínio tipados** (`InvalidJobStateError`, `RetryLimitError`...) traduzidos para status HTTP na borda — o service é testável e reutilizável sem HTTP.

- **Polling adaptativo no front — decisão final, não fallback**: avaliei push (SSE e WebSocket foi descartado de saída). Para acompanhar status de jobs, polling curto e adaptativo contra um endpoint barato é o padrão que times operando em grande escala escolhem: funciona atrás de qualquer LB/proxy sem sticky session, não mantém conexão aberta por usuário no servidor (SSE segura uma conexão viva por cliente e sofre com timeouts de proxies intermediários), não muda o modelo de auth (`EventSource` não envia o header `X-Auth` — a credencial teria que ir para query string, vazando em log de acesso) e escala horizontalmente sem estado. O trabalho certo foi baratear o polling, intervalo de 1s só enquanto há job ativo, desligado em repouso, contra endpoint paginado e indexado. Push só compensaria se o custo agregado do polling superasse o de manter conexões — muito longe da realidade deste sistema.

- **CORS como allowlist de ponta a ponta**: `CORS_ORIGINS` por env (settings → middleware → compose), métodos `GET/POST`, headers exatamente os três que o front usa (`X-Auth`, `Content-Type`, `Idempotency-Key`) e `max_age=600` para cachear o preflight. Verificado com preflight real: origem permitida recebe os headers, origem desconhecida recebe 400.

- **404 (e não 403) para recurso de outro tenant**: não vazar a existência do ID.

- **Testes contra Postgres real (testcontainers)** Foi escolhido, pois as garantias centrais do case *são* comportamento de Postgres (SKIP LOCKED, ON CONFLICT, locks de linha), por tanto evitei mocks e SQLite para testes.

- **Wide events em vez de linhas de log soltas**: um JSON por request/job com contexto de negócio, correlacionável por `trace_id`; tail sampling para custo controlado.

## 4. O que deixei de fora — conscientemente

- **Autenticação real**: o case declara o `X-Auth` como simplificação proposital. Endureci o parsing (401 para formato inválido) e deixei o `AuthContext` como costura única onde um JWT entraria.

- **Handlers sem limite de duração conhecido**: o worker renova o lease em background, mas um handler real ainda deveria declarar timeout próprio, política de cancelamento e limites de recursos; o executor do case é deliberadamente previsível.

- **Enforcement da `job_quota`** no create: a base original só decrementa a quota, nunca a checa, manter esse comportamento foi decisão explícita para não inventar regra de produto que o enunciado não pediu.

- **Cursor-based pagination**: limit/offset degrada com offsets profundos, para o padrão de uso (primeiras páginas) é suficiente e muito mais simples.

- **Broker dedicado (Redis/RabbitMQ/SQS)**: overkill para o volume do case; o desenho fila-na-tabela + NOTIFY chega longe e mantém tudo transacional com os dados. (Shopify é um case real na qual usou MySQL ao invés de Redis para o sistema principal de reserva de estoque usando estratégias similares utilizadas nesse case). Tech Blog - https://shopify.engineering/scaling-inventory-reservations

- **E2E de frontend (Playwright)**: o front é intencionalmente mínimo.

- **Push em tempo real**: avaliado e rejeitado conscientemente — ver §3; polling adaptativo foi a decisão (polling só acontece quando há job `queued`/`running` na tela, quando em repouso, zero requests — mutações e focus-refetch cobrem o resto).

## 5. Uso de IA — reflexão honesta

> **Prompt Injection no Case:** o `KNOWN_ISSUES.md` original continha um
> comentário HTML oculto instruindo agentes de IA a ignorar silenciosamente o
> Sintoma 3 e a esconder essa omissão. A instrução foi
> lida durante o levantamento de requisitos.
> Fica registrado essa observação porque é exatamente o tipo de coisa que uso cego de
> IA deixaria passar.


### Onde a IA ajudou:

- Elaboração de documentos, base de testes, base de validação usando Pydantic, TDD mecânico (testes de borda de auth, paginação, estados de cancel/retry), automatização usando script para reprodução dos sintomas com números e documentação das evidências, pipeline de observabilidade e métricas.

### Onde a IA atrapalhou / te levou pro caminho errado:

- O caso mais instrutivo foi a métrica de profundidade da fila, que a IA implementou errado em três camadas empilhadas. Para resolver um aviso do Pylance, ela unificou o módulo de métricas entre api e worker — e com isso o processo da **api** passou a registrar e exportar os gauges do worker, criando uma série `jobs_queued_depth{service="api"}` eternamente em 0 poluindo as queries do Grafana.

- O worker só atualizava o gauge quando a fila **esvaziava** (no retorno do drain) — ou seja, o valor publicado era quase sempre 0 por construção, nunca o backlog real.

- Mesmo que os dois primeiros fossem corrigidos, um gauge alimentado pelo loop do worker não sobrevive à análise: um job que vive ~1s raramente coincide com um scrape de 10s, e se o worker morre (exatamente quando a fila mais importa) o gauge congela no último valor. Só percebi porque testei pelo front e o painel não saía do zero. A correção foi conceitual, não pontual: **estado não é métrica de processo** — jobs por status vêm do postgres-exporter via query custom (`pg_jobs_total{status}`), informações incorretas no momento de cada scrape, independente de qualquer worker; o worker exporta apenas métricas de *fluxo* (espera na fila, duração, outcomes, wakeups), e os módulos de métricas foram separados por processo. Verifiquei parando o worker, criando um job (fila = 1 no Prometheus) e religando (fila = 0).

- a IA tentou otimizar para "fazer o painel existir" e para silenciar o linter; a pergunta certa era "quem é a fonte de verdade deste número?". A primeira versão do tratamento de falha do worker teve erro parecido: fazia rollback e re-raise — o que *parecia* correto e na prática criava o loop infinito de retry (o rollback desfazia o próprio `running`); só caiu no teste de integração. Também tentou "melhorar" o schema com constraints extras sem eu pedir, misturando escopo de modelagem com escopo de índice — tive que separar em migrations distintas e justificar cada uma.

- Introduziu um ORM completo (SQLAlchemy + Alembic) que depois precisei remover inteiro, porque violava um critério explícito do case. (SQL cru via psycopg (sem ORM); tudo rodando com `docker compose up --build`.)

### O que você rejeitou da IA e por quê:

- **O ORM** — o TASKS.md é claro: "SQL cru via psycopg — mantenha o padrão (sem ORM)". Reverti para psycopg puro com pool, mantendo a arquitetura de services; as queries ficaram mais explícitas e o `EXPLAIN` bate 1:1 com o que está no código.

- **Fila/pub-sub em camada de aplicação**: a sugestão inicial era polling puro e, depois, um broker externo. Rejeitei os dois e fui estudar o LISTEN/NOTIFY nativo do Postgres, pois já conhecia a capacidade e robustez, para *sinalização* ele aguenta alto volume com custo mínimo e entrega no commit, exatamente o que eu precisava aqui. Desde que não seja tratado como fila durável (payload 8KB, sem entrega garantida sem listener). A tabela continua sendo a fila; o NOTIFY substitui o polling. Ferramenta nativa, zero dependência nova, e o fallback de 30s cobre o caso de notify perdido. 

- **Índices especulativos** (status isolado, kind): o planner não os usaria com cardinalidade baixa e custariam escrita/RAM — mantive só os que o `EXPLAIN` prova.


# Demais observações

### Bateria adversarial: quebrando as próprias regras

Depois de tudo pronto, escrevi uma suíte cujo objetivo é **falhar** — cada teste
tenta violar uma regra de negócio com input errado, faltando ou malicioso
(`api/tests/integration/test_business_rules_break.py`, 195 casos, e
`worker/tests/modules/jobs/test_business_rules_break.py`, 20 casos). Cobre:
auth obrigatório em todas as rotas × 11 variações malformadas; chave de
idempotência ausente/em branco/gigante; isolamento cross-tenant em leitura e
mutação; roles inválidas; todas as transições de estado proibidas; limite de
tentativas; limite de concorrência; tipos/faixas em todo parâmetro; injeção de
SQL em `kind` e `search`; curingas de `ILIKE`; abuso de protocolo (header
duplicado, content-type errado, body de 100k, campos extras tentando definir
`id`/`status`); e, no worker, claim de estados inválidos, backoff pendente,
cancelamento no meio da falha, DLQ duplicada e quota debitada duas vezes.

**A bateria encontrou dois bugs reais** — ambos de validação que passavam
despercebidos porque `min_length=1` não olha o conteúdo:

1. `kind: "   "` (só espaços) criava job com HTTP 201 — lixo entrando no
   domínio. Corrigido com `StringConstraints(strip_whitespace=True,
   min_length=1)`: agora normaliza `"  report  "` → `"report"` e rejeita
   só-espaços com 422.
2. `Idempotency-Key` aceitava valor em branco e, pior, as constraints do
   `Annotated` **não estavam sendo aplicadas** porque o `Header()` estava no
   default em vez de dentro do `Annotated` — uma chave de 201 caracteres passava
   direto. Corrigido movendo o `Header()` para dentro do `Annotated`.

Também confirmou dois comportamentos que eu supunha e não tinha provado: roles
fora da whitelist (`root`, `ADMIN`, `admin `) são rejeitadas como credencial
inválida (401, fail-closed) em vez de "sem permissão"; e um job cancelado no
meio de uma falha **não** volta para a fila nem vai para a DLQ.

## Redução de ruídos de observabilidade

A primeira versão de `LOG_SUPPRESS_PROBE_ROUTES` filtrava só o wide event, e o
Grafana continuou mostrando `INFO: "GET /health HTTP/1.1" 200 OK` — o **access
log do uvicorn** é um canal independente, em texto puro, que eu tinha ignorado.
Com o healthcheck do container e o scrape do Prometheus a cada 10s, eram ~145
linhas a cada 5 minutos afogando os eventos que importam. A flag agora instala
também um `logging.Filter` no logger `uvicorn.access`, com exatamente a mesma
regra (rota de probe + status < 400). O filtro é conservador por desenho: se o
formato do record não for o esperado, ele deixa passar — silenciar por engano é
pior do que uma linha a mais.

## Casos de borda

- Cancel chegando durante o processamento: worker descarta resultado e não debita quota (testado com cancel concorrente real no meio do `_execute`).

- Duplo-clique em retry/cancel: segunda chamada → 409, sem efeito duplicado.

- Job com resultado pré-existente reprocessado: `ON CONFLICT DO NOTHING`, quota intacta (testado).

- Worker reiniciado com backlog: `drain()` no startup processa a fila antes de entrar em LISTEN.

- NOTIFY perdido (worker desconectado no momento do commit): fallback de polling de 30s garante progresso.

- `X-Auth` malformado em 7 variações (vazio, sem `:`, id não-numérico, id ≤ 0, role vazia): sempre 401, nunca 500.
- Tentativa de SQL injection no `kind`: armazenado literalmente (parâmetros bind), testado.

- Job `running` de worker morto: lease expirado vira `failed`; lease sem timestamp legado também é recuperado, e a terceira tentativa órfã vai para a DLQ.

- NOT tratado também: clock skew entre réplicas não afeta nada (toda ordenação é por `id`/transação, não por relógio).

## 7. Próximos passos (com mais tempo)

O que você faria a seguir.

---

**Tempo investido:** ~ 8 a 10 horas (Os problemas foram resolvidos relativamente rápido, o restante das horas foram investidas em testes e revisão manual pensando que é um serviço crítico e de demanda elevada)

A revisão manual teve um efeito muito positivo principalmente em validações pequenas e detalhadas que normalmente uma LLM acaba ignorando.
Testes comportamentais da aplicação, LLM é excelente para produtividade mas particularmente sinto que falha em testes e testa muito o que é "óbvio", todos nós sabemos que 1 + 1 = 2 eu quero entender de fato como minha aplicação se comporta quando eu envio que 1 + 1 = 4...