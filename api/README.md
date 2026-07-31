# Relay API

Serviço HTTP FastAPI responsável por submissão, consulta, cancelamento e retry
de jobs, com isolamento por tenant via header `X-Auth`. A API também é a única
responsável por aplicar as migrations SQL de `db/migrations/`.

## Executar localmente

```bash
cp .env.example .env
uv run python -m shared.db.migrate
uv run uvicorn main:app --reload
```

`DATABASE_URL` é obrigatória. A API lê apenas `api/.env`; o Swagger fica em
`http://localhost:8000/docs`.

## Migrations (up e down)

Cada migration `NNNN_nome.sql` tem o reverso `NNNN_nome.down.sql` ao lado. O
ledger fica em `schema_migrations` e cada passo roda na própria transação, sob
advisory lock (seguro com múltiplas réplicas).

```bash
uv run python -m shared.db.migrate            # up: aplica as pendentes (default)
uv run python -m shared.db.migrate down       # reverte a última aplicada
uv run python -m shared.db.migrate down --steps 3
uv run python -m shared.db.migrate down --all # reverte tudo

# dentro do Compose
docker compose exec api uv run python -m shared.db.migrate down --steps 1
```

Downs restauram o **schema** anterior; transformações de dados do up são
one-way (ex.: saneamento da `0007`) e cada caso está comentado no próprio
`.down.sql` — incluindo mapeamentos como `cancelled → failed` na `0004`.

## Testar

```bash
uv run pytest
```

Os testes usam um PostgreSQL efêmero via testcontainers e cobrem rotas,
services, integração (corridas de concorrência e bateria adversarial de regras
de negócio), auth, shutdown e contrato OpenAPI. Endpoints, validação e camadas
estão documentados em [`docs/api.md`](../docs/api.md).
