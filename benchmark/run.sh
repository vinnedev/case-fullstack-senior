#!/usr/bin/env bash
# Uso: benchmark/run.sh [read|write|lifecycle|chaos|mixed] (default: read)
set -euo pipefail
cd "$(dirname "$0")/.."

SCENARIO="${1:-read}"
RESULTS_DIR="benchmark/results"
mkdir -p "$RESULTS_DIR"

WORKERS="${WORKERS:-1}"
echo ">> subindo db, api, worker (x$WORKERS) e exporters"
docker compose --profile obs up -d --build --scale worker="$WORKERS" db api worker postgres-exporter prometheus

echo ">> aguardando api saudável"
until curl -sf http://localhost:8000/health > /dev/null; do sleep 1; done

echo ">> setup + populate (empresa 900: 100k done + mix de failed/cancelled)"
docker compose exec -T db psql -q -U relay -d relay < benchmark/sql/setup.sql
docker compose exec -T db psql -q -U relay -d relay < benchmark/sql/populate.sql

echo ">> zerando fila residual da empresa 900 (cenário começa limpo)"
docker compose exec -T db psql -q -U relay -d relay \
  -c "UPDATE jobs SET status='cancelled', locked_at=NULL, worker_id=NULL WHERE company_id=900 AND status='queued'" > /dev/null

NETWORK=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}' \
  "$(docker compose ps -q api)")

echo ">> k6 cenário '$SCENARIO' (rede $NETWORK)"
docker run --rm --network "$NETWORK" \
  -e SCENARIO="$SCENARIO" -e BASE_URL=http://api:8000 \
  -e WRITE_RPS="${WRITE_RPS:-}" -e LIFECYCLE_RPS="${LIFECYCLE_RPS:-}" \
  -e CHAOS_RPS="${CHAOS_RPS:-}" -e DURATION="${DURATION:-}" \
  -v "$PWD/benchmark/k6:/scripts:ro" \
  -v "$PWD/$RESULTS_DIR:/results" \
  grafana/k6 run /scripts/load.js \
  --summary-export "/results/${SCENARIO}-summary.json"

echo ">> resultado salvo em $RESULTS_DIR/${SCENARIO}-summary.json"
echo ">> para limpar os dados de benchmark: docker compose exec -T db psql -U relay -d relay < benchmark/sql/teardown.sql"
