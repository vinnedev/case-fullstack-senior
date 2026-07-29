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

## Testar

```bash
uv run pytest
```

Os testes usam um PostgreSQL efêmero via testcontainers e cobrem rotas,
services, integração (corridas de concorrência e bateria adversarial de regras
de negócio), auth, shutdown e contrato OpenAPI. Endpoints, validação e camadas
estão documentados em [`docs/api.md`](../docs/api.md).
