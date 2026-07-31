#!/usr/bin/env bash
# Mede o teto de processamento do worker: semeia N jobs queued e cronometra o
# drain. Com SIM=0 o trabalho simulado do case sai da conta e o número passa a
# ser o da maquinaria real da fila (claim + finalize + auditoria + quota).
# Uso: JOBS=20000 WORKERS=4 SIM=0 TENANTS=1 benchmark/worker_throughput.sh
# TENANTS > 1 espalha os jobs entre empresas 900..900+N-1 e expõe o efeito da
# serialização por tenant (linha de quota da company é o ponto quente).
set -euo pipefail
cd "$(dirname "$0")/.."

JOBS="${JOBS:-20000}"
WORKERS="${WORKERS:-4}"
SIM="${SIM:-0}"
TENANTS="${TENANTS:-1}"

for value in "$JOBS" "$WORKERS" "$TENANTS"; do
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || { echo "JOBS, WORKERS e TENANTS devem ser inteiros positivos" >&2; exit 2; }
done
[[ "$SIM" =~ ^[0-9]+([.][0-9]+)?$ ]] || { echo "SIM deve ser um número não negativo" >&2; exit 2; }

echo ">> subindo stack com $WORKERS worker(s), trabalho simulado = ${SIM}s"
JOB_SIMULATED_WORK_S="$SIM" docker compose up -d --build --scale worker="$WORKERS" db api worker > /dev/null 2>&1
until curl -sf http://localhost:8000/health > /dev/null; do sleep 1; done

docker compose exec -T db psql -q -U relay -d relay < benchmark/sql/setup.sql > /dev/null
docker compose exec -T db psql -q -U relay -d relay -c "
  INSERT INTO companies (id, name, max_concurrent_jobs, job_quota)
  SELECT 900 + g, 'Benchmark ' || g, 100000, 10000000
  FROM generate_series(0, $TENANTS - 1) AS g
  ON CONFLICT (id) DO UPDATE SET max_concurrent_jobs = EXCLUDED.max_concurrent_jobs, job_quota = EXCLUDED.job_quota"

echo ">> zerando fila residual e semeando $JOBS jobs queued em $TENANTS tenant(s)"
docker compose exec -T db psql -q -U relay -d relay \
  -c "UPDATE jobs SET status='cancelled', locked_at=NULL, worker_id=NULL WHERE company_id BETWEEN 900 AND $((900 + TENANTS - 1)) AND status IN ('queued','running')"
docker compose exec -T db psql -q -U relay -d relay \
  -c "INSERT INTO jobs (company_id, kind, status) SELECT 900 + (g % $TENANTS), 'report', 'queued' FROM generate_series(1, $JOBS) AS g"

# rodadas anteriores deixam dead tuples no índice parcial da fila e o claim
# paga o custo de pular entradas mortas até o autovacuum passar; o VACUUM
# explícito garante medição de estado limpo
docker compose exec -T db psql -q -U relay -d relay -c "VACUUM ANALYZE jobs" > /dev/null

START=$(date +%s)
# INSERT via SQL não emite NOTIFY; um pg_notify manual acorda os listeners
# sem esperar o timeout de 30s (o drain contínuo faz o resto).
docker compose exec -T db psql -q -U relay -d relay -c "SELECT pg_notify('jobs_queued', '0')" > /dev/null

PREV_DONE=0
PREV_T=$START
while :; do
  LEFT=$(docker compose exec -T db psql -tA -U relay -d relay \
    -c "SELECT count(*) FROM jobs WHERE company_id BETWEEN 900 AND $((900 + TENANTS - 1)) AND status IN ('queued','running')")
  NOW=$(date +%s)
  ELAPSED=$((NOW - START))
  DONE=$((JOBS - LEFT))
  DELTA_T=$((NOW - PREV_T))
  INST=0
  [ "$DELTA_T" -gt 0 ] && INST=$(((DONE - PREV_DONE) / DELTA_T))
  echo "   t=${ELAPSED}s restantes=$LEFT processados=$DONE (instantâneo ~${INST} jobs/s)"
  PREV_DONE=$DONE
  PREV_T=$NOW
  [ "$LEFT" -le 0 ] && break
  sleep 5
done

TOTAL=$(($(date +%s) - START))
[ "$TOTAL" -le 0 ] && TOTAL=1
echo ">> RESULTADO: $JOBS jobs drenados em ${TOTAL}s com $WORKERS réplica(s) e SIM=${SIM}s = $((JOBS / TOTAL)) jobs/s"
