# Relay Worker

Processo Python responsável por reivindicar e processar jobs da fila persistida
no PostgreSQL. O worker não expõe API HTTP; ele usa `LISTEN/NOTIFY` para ser
acordado e mantém polling de 30 segundos como fallback.

## Executar

```bash
cp .env.example .env
uv run main.py
```

`DATABASE_URL` é obrigatória. O worker lê apenas `worker/.env`; migrations são
responsabilidade exclusiva da API.

## Testar

```bash
uv run pytest
```

Os testes usam um PostgreSQL efêmero via testcontainers e cobrem claim
concorrente, cancelamento durante o processamento, falhas, retry manual,
leases e DLQ. O fluxo detalhado está em [`docs/worker.md`](../docs/worker.md).
