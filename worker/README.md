# Relay Worker

Processo Python responsável por reivindicar e processar jobs da fila persistida
no PostgreSQL. O worker não expõe API HTTP; ele usa `LISTEN/NOTIFY` para ser
acordado e mantém polling de 30 segundos como fallback.

## Executar

```bash
uv run main.py
```

## Testar

```bash
uv run pytest
```

Os testes usam um PostgreSQL efêmero via testcontainers e cobrem claim
concorrente, cancelamento durante o processamento, falhas, retry manual,
leases e DLQ. O fluxo detalhado está em [`docs/worker.md`](../docs/worker.md).
