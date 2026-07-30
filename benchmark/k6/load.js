import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';

const BASE = __ENV.BASE_URL || 'http://localhost:8000';
const COMPANY = __ENV.COMPANY_ID || '900';
const USER = { 'X-Auth': `${COMPANY}:user` };
const ADMIN = { 'X-Auth': `${COMPANY}:admin` };
const JSON_HEADERS = { 'Content-Type': 'application/json' };

const created = new Counter('jobs_created');
const throttled = new Counter('jobs_throttled_429');
const e2e = new Trend('job_e2e_duration', true);
const e2eTimeout = new Counter('job_e2e_timeout');

const okOr404 = http.expectedStatuses(200, 404);
const okOrConflict = http.expectedStatuses(200, 409);
const conflictOnly = http.expectedStatuses(409);
const createdOr429 = http.expectedStatuses(201, 429);

function logUnexpected(route, res, ...expected) {
  if (!expected.includes(res.status)) {
    console.error(`UNEXPECTED ${route}: status=${res.status} body=${String(res.body).slice(0, 200)}`);
  }
}

const retriesWon = new Counter('chaos_retry_won');
const retriesConflict = new Counter('chaos_retry_conflict_409');
const cancelsMidFlight = new Counter('chaos_cancel_mid_flight');
const cancelsLostRace = new Counter('chaos_cancel_lost_to_finalize_409');

// Todo cenário pesado começa com um estágio de warmup (fase separada nas
// métricas): aquece pool de conexões, caches do Postgres e JIT do uvicorn
// antes de medir. Thresholds só valem para a fase main.
const S = {
  read: {
    warmup: { executor: 'constant-vus', exec: 'read', vus: 5, duration: '15s', tags: { phase: 'warmup' } },
    read: {
      executor: 'ramping-vus',
      exec: 'read',
      startTime: '15s',
      startVUs: 5,
      tags: { phase: 'main' },
      stages: [
        { duration: '10s', target: 20 },
        { duration: '40s', target: 20 },
        { duration: '10s', target: 50 },
        { duration: '30s', target: 50 },
      ],
    },
  },
  write: {
    warmup: { executor: 'constant-arrival-rate', exec: 'write', rate: 5, timeUnit: '1s', duration: '15s', preAllocatedVUs: 10, tags: { phase: 'warmup' } },
    write: {
      executor: 'constant-arrival-rate',
      exec: 'write',
      startTime: '15s',
      rate: Number(__ENV.WRITE_RPS || 30),
      timeUnit: '1s',
      duration: __ENV.DURATION || '60s',
      preAllocatedVUs: 60,
      tags: { phase: 'main' },
    },
  },
  lifecycle: {
    warmup: { executor: 'constant-arrival-rate', exec: 'lifecycle', rate: 1, timeUnit: '2s', duration: '15s', preAllocatedVUs: 20, tags: { phase: 'warmup' } },
    lifecycle: {
      executor: 'constant-arrival-rate',
      exec: 'lifecycle',
      startTime: '15s',
      rate: Number(__ENV.LIFECYCLE_RPS || 1),
      timeUnit: '1s',
      duration: __ENV.DURATION || '60s',
      preAllocatedVUs: 80,
      tags: { phase: 'main' },
    },
  },
  chaos: {
    warmup: { executor: 'constant-arrival-rate', exec: 'chaos', rate: 1, timeUnit: '2s', duration: '15s', preAllocatedVUs: 10, tags: { phase: 'warmup' } },
    chaos: {
      executor: 'constant-arrival-rate',
      exec: 'chaos',
      startTime: '15s',
      rate: Number(__ENV.CHAOS_RPS || 2),
      timeUnit: '1s',
      duration: __ENV.DURATION || '60s',
      preAllocatedVUs: 60,
      tags: { phase: 'main' },
    },
  },
  mixed: {
    warmup: { executor: 'constant-vus', exec: 'read', vus: 5, duration: '15s', tags: { phase: 'warmup' } },
    read: { executor: 'constant-vus', exec: 'read', startTime: '15s', vus: 30, duration: '60s', tags: { phase: 'main' } },
    write: { executor: 'constant-arrival-rate', exec: 'write', startTime: '15s', rate: 10, timeUnit: '1s', duration: '60s', preAllocatedVUs: 30, tags: { phase: 'main' } },
    lifecycle: { executor: 'constant-arrival-rate', exec: 'lifecycle', startTime: '15s', rate: 1, timeUnit: '1s', duration: '60s', preAllocatedVUs: 60, tags: { phase: 'main' } },
    chaos: { executor: 'constant-arrival-rate', exec: 'chaos', startTime: '15s', rate: 1, timeUnit: '1s', duration: '60s', preAllocatedVUs: 30, tags: { phase: 'main' } },
  },
};

const SCENARIO = __ENV.SCENARIO || 'read';

const thresholds = {
  'http_req_failed{phase:main}': ['rate<0.01'],
  'http_req_duration{route:list,phase:main}': ['p(95)<500'],
  'http_req_duration{route:detail,phase:main}': ['p(95)<300'],
  'http_req_duration{route:result,phase:main}': ['p(95)<300'],
  'http_req_duration{route:create,phase:main}': ['p(95)<300'],
  'http_req_duration{route:cancel,phase:main}': ['p(95)<300'],
  'http_req_duration{route:retry,phase:main}': ['p(95)<300'],
  'http_req_duration{route:admin_jobs,phase:main}': ['p(95)<500'],
  'http_req_duration{route:admin_dlq,phase:main}': ['p(95)<500'],
};
// e2e só é SLO no lifecycle: os cenários mixed/write saturam o worker de
// propósito (taxa de entrada > 1 job/s por réplica) para medir a degradação.
if (SCENARIO === 'lifecycle') thresholds.job_e2e_duration = ['p(95)<15000'];

export const options = {
  scenarios: S[SCENARIO],
  thresholds,
  summaryTrendStats: ['avg', 'p(50)', 'p(95)', 'p(99)', 'max'],
};

export function read() {
  const offset = Math.floor(Math.random() * 20) * 50;
  const filtered = Math.random() < 0.2 ? '&status=done&search=rep' : '';
  const res = http.get(`${BASE}/jobs?limit=50&offset=${offset}${filtered}`, {
    headers: USER,
    tags: { route: 'list' },
  });
  check(res, { 'list 200': (r) => r.status === 200 });

  const jobs = res.json();
  if (Array.isArray(jobs) && jobs.length > 0) {
    const id = jobs[Math.floor(Math.random() * jobs.length)].id;
    const detail = http.get(`${BASE}/jobs/${id}`, { headers: USER, tags: { route: 'detail' } });
    check(detail, { 'detail 200': (r) => r.status === 200 });

    if (Math.random() < 0.5) {
      const result = http.get(`${BASE}/jobs/${id}/result`, {
        headers: USER,
        tags: { route: 'result' },
        responseCallback: okOr404,
      });
      check(result, { 'result 200/404': (r) => r.status === 200 || r.status === 404 });
    }
  }

  if (Math.random() < 0.1) {
    const admin = http.get(`${BASE}/admin/jobs?limit=50`, { headers: ADMIN, tags: { route: 'admin_jobs' } });
    check(admin, { 'admin jobs 200': (r) => r.status === 200 });
    const dlq = http.get(`${BASE}/admin/dlq?limit=50`, { headers: ADMIN, tags: { route: 'admin_dlq' } });
    check(dlq, { 'admin dlq 200': (r) => r.status === 200 });
  }
}

export function write() {
  const key = `bench-${__VU}-${__ITER}-${Date.now()}`;
  const res = http.post(`${BASE}/jobs`, JSON.stringify({ kind: 'report' }), {
    headers: { ...USER, ...JSON_HEADERS, 'Idempotency-Key': key },
    tags: { route: 'create' },
  });
  if (res.status === 201) created.add(1);
  if (res.status === 429) throttled.add(1);
  check(res, { 'create 201': (r) => r.status === 201 });
}

// Mede o processamento ponta a ponta: create → fila → worker → done → result.
// 15% dos jobs são cancelados no meio e têm o retry negado com 409 (contrato).
export function lifecycle() {
  const key = `bench-lc-${__VU}-${__ITER}-${Date.now()}`;
  const res = http.post(`${BASE}/jobs`, JSON.stringify({ kind: 'report' }), {
    headers: { ...USER, ...JSON_HEADERS, 'Idempotency-Key': key },
    tags: { route: 'create' },
    responseCallback: createdOr429,
  });
  if (res.status !== 201) {
    throttled.add(1);
    return;
  }
  created.add(1);
  const id = res.json('id');
  const t0 = Date.now();

  if (Math.random() < 0.15) {
    const cancel = http.post(`${BASE}/jobs/${id}/cancel`, null, {
      headers: USER,
      tags: { route: 'cancel' },
      responseCallback: okOrConflict,
    });
    check(cancel, { 'cancel 200/409': (r) => r.status === 200 || r.status === 409 });
    logUnexpected('cancel', cancel, 200, 409);
    if (cancel.status === 200) {
      const retry = http.post(`${BASE}/jobs/${id}/retry`, null, {
        headers: USER,
        tags: { route: 'retry' },
        responseCallback: conflictOnly,
      });
      check(retry, { 'retry em cancelado 409': (r) => r.status === 409 });
      return;
    }
  }

  const deadline = t0 + 30000;
  let status = '';
  while (Date.now() < deadline) {
    const detail = http.get(`${BASE}/jobs/${id}`, { headers: USER, tags: { route: 'detail' } });
    logUnexpected('detail', detail, 200);
    status = detail.json('status');
    if (status === 'done' || status === 'failed' || status === 'cancelled') break;
    sleep(0.5);
  }

  if (status === 'done') {
    e2e.add(Date.now() - t0);
    const result = http.get(`${BASE}/jobs/${id}/result`, { headers: USER, tags: { route: 'result' } });
    check(result, { 'result 200': (r) => r.status === 200 });
    return;
  }
  if (Date.now() >= deadline) e2eTimeout.add(1);
}

// Caos deliberado: dispara as corridas que as garantias do sistema prometem
// arbitrar (idempotência, duplo retry, cancel × finalize) e verifica que só
// um lado vence cada disputa.
export function chaos() {
  const key = `bench-chaos-${__VU}-${__ITER}`;
  const createReq = {
    method: 'POST',
    url: `${BASE}/jobs`,
    body: JSON.stringify({ kind: 'report' }),
    params: {
      headers: { ...USER, ...JSON_HEADERS, 'Idempotency-Key': key },
      tags: { route: 'create' },
      responseCallback: createdOr429,
    },
  };
  const [a, b] = http.batch([createReq, createReq]);
  logUnexpected('create-batch', a, 201, 429);
  logUnexpected('create-batch', b, 201, 429);
  if (a.status === 201 && b.status === 201) {
    check(a, { 'replay concorrente devolve o mesmo job': () => a.json('id') === b.json('id') });
  }

  const list = http.get(`${BASE}/jobs?status=failed&limit=50`, { headers: USER, tags: { route: 'list' } });
  const failed = list.json();
  if (!Array.isArray(failed) || failed.length === 0) return;

  const target = failed[Math.floor(Math.random() * failed.length)].id;
  const retryReq = {
    method: 'POST',
    url: `${BASE}/jobs/${target}/retry`,
    params: { headers: USER, tags: { route: 'retry' }, responseCallback: okOrConflict },
  };
  const [r1, r2] = http.batch([retryReq, retryReq]);
  logUnexpected('retry-batch', r1, 200, 409);
  logUnexpected('retry-batch', r2, 200, 409);
  check(r1, { 'duplo retry: no máximo um vence': () => !(r1.status === 200 && r2.status === 200) });
  const won = r1.status === 200 || r2.status === 200;
  if (r1.status === 200) retriesWon.add(1);
  if (r2.status === 200) retriesWon.add(1);
  if (r1.status === 409) retriesConflict.add(1);
  if (r2.status === 409) retriesConflict.add(1);
  if (!won) return;

  // deixa o retry entrar (ou não) em processamento e cancela no meio:
  // quem escrever primeiro na linha vence — 200 (cancelou) ou 409 (finalize venceu)
  sleep(Math.random() * 1.5);
  if (Math.random() < 0.5) {
    const cancel = http.post(`${BASE}/jobs/${target}/cancel`, null, {
      headers: USER,
      tags: { route: 'cancel' },
      responseCallback: okOrConflict,
    });
    check(cancel, { 'cancel na corrida: 200 ou 409': (r) => r.status === 200 || r.status === 409 });
    if (cancel.status === 200) cancelsMidFlight.add(1);
    if (cancel.status === 409) cancelsLostRace.add(1);
    return;
  }

  // ou deixa o retry processar até o fim e confere o resultado
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    const detail = http.get(`${BASE}/jobs/${target}`, { headers: USER, tags: { route: 'detail' } });
    const status = detail.json('status');
    if (status === 'done' || status === 'failed' || status === 'cancelled') break;
    sleep(0.5);
  }
}
