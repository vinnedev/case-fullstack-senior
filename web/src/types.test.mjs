import assert from "node:assert/strict";
import test from "node:test";

import {
  getJobMutationInvalidationKeys,
  invalidateJobMutationCaches,
  jobQueryKeys,
  shouldPollActiveJobs,
} from "./jobQueries.ts";

import {
  parseAdminCompanies,
  parseAdminJobs,
  parseJob,
  parseJobCancelled,
  parseJobCreated,
  parseJobDetail,
  parseJobRetried,
  parseJobs,
} from "./types.ts";

const validJob = {
  id: 1,
  kind: "report",
  status: "queued",
  created_at: "2026-07-28T12:00:00Z",
  result_count: 0,
};

test("valida uma listagem de jobs correta", () => {
  assert.deepEqual(parseJobs([validJob]), [validJob]);
});

test("rejeita shape, status e identificadores inválidos", () => {
  assert.throws(() => parseJobs({}));
  assert.throws(() => parseJob({ ...validJob, status: "exploded" }));
  assert.throws(() => parseJob({ ...validJob, id: 0 }));
  assert.throws(() => parseJob({ ...validJob, result_count: -1 }));
  assert.throws(() => parseJob({ ...validJob, created_at: "ontem" }));
});

test("rejeita tentativas fora do domínio", () => {
  const detail = {
    id: 1,
    company_id: 1,
    kind: "report",
    status: "failed",
    attempts: 4,
    last_error: "boom",
  };
  assert.throws(() => parseJobDetail(detail));
});

test("valida auditoria de submissão e cancelamento no detalhe", () => {
  const detail = parseJobDetail({
    id: 1,
    company_id: 1,
    kind: "report",
    status: "cancelled",
    attempts: 0,
    last_error: null,
    cancellation: { cancelled_by: "1:user", cancelled_at: "2026-07-28T12:01:00Z" },
    audit_events: [
      { event_type: "submitted", actor: "1:user", occurred_at: "2026-07-28T12:00:00Z", trace_id: "trace-1" },
      { event_type: "cancelled", actor: "1:user", occurred_at: "2026-07-28T12:01:00Z", trace_id: null },
    ],
  });
  assert.equal(detail.cancellation.cancelled_by, "1:user");
  assert.equal(detail.audit_events.length, 2);
});

test("rejeita resposta de mutação inconsistente", () => {
  assert.throws(() => parseJobCreated({ id: -1, status: "queued" }));
  assert.throws(() => parseJobCreated({ id: 1, status: "done" }));
  assert.throws(() => parseJobCancelled({ id: 1, status: "queued" }));
  assert.throws(() => parseJobRetried({ id: 1, status: "queued" }));
  assert.deepEqual(parseJobRetried({ id: 1, status: "queued", attempts: 2 }), {
    id: 1,
    status: "queued",
    attempts: 2,
  });
});

const validAdminJob = {
  id: 7,
  company_id: 2,
  status: "done",
};

const validCompany = {
  id: 1,
  name: "Acme",
  max_concurrent_jobs: 2,
  job_quota: 20,
  total_jobs: 3,
  queued: 1,
  running: 0,
  done: 1,
  failed: 1,
  cancelled: 0,
};

test("valida a listagem administrativa de jobs", () => {
  assert.deepEqual(parseAdminJobs([validAdminJob]), [validAdminJob]);
  assert.throws(() => parseAdminJobs([{ ...validAdminJob, status: "exploded" }]));
  assert.throws(() => parseAdminJobs([{ ...validAdminJob, id: 0 }]));
});

test("valida a listagem administrativa de empresas", () => {
  assert.deepEqual(parseAdminCompanies([validCompany]), [validCompany]);
  assert.throws(() => parseAdminCompanies([{ ...validCompany, name: "" }]));
  assert.throws(() => parseAdminCompanies([{ ...validCompany, queued: -1 }]));
  assert.throws(() => parseAdminCompanies([{ ...validCompany, total_jobs: 1.5 }]));
  assert.throws(() => parseAdminCompanies({}));
});

test("centraliza as chaves de cache dos jobs", () => {
  assert.deepEqual(jobQueryKeys.list("token"), ["jobs", "token"]);
  assert.deepEqual(jobQueryKeys.page("token", 2, 25, "running"), ["jobs", "token", 2, 25, "running"]);
  assert.deepEqual(jobQueryKeys.detail("token", 17), ["job", "token", 17]);
  assert.deepEqual(jobQueryKeys.result("token", 17), ["job-result", "token", 17]);
});

test("invalida lista, detalhe e resultado do job mutado", async () => {
  const invalidated = [];
  const variables = { auth: "token", jobId: 17 };
  await invalidateJobMutationCaches((queryKey) => invalidated.push(queryKey), variables);

  assert.deepEqual(invalidated, getJobMutationInvalidationKeys(variables));
});

test("mantém o tenant da mutação pendente ao invalidar após uma troca de autenticação", async () => {
  const pendingMutation = { auth: "tenant-original", jobId: 17 };
  const authAfterSwitch = "tenant-atual";
  const invalidated = [];

  await invalidateJobMutationCaches((queryKey) => invalidated.push(queryKey), pendingMutation);

  assert.deepEqual(invalidated, getJobMutationInvalidationKeys(pendingMutation));
  assert.deepEqual(invalidated[0], jobQueryKeys.list("tenant-original"));
  assert.notDeepEqual(invalidated[0], jobQueryKeys.list(authAfterSwitch));
});

test("faz polling do detalhe somente enquanto o job estiver ativo", () => {
  assert.equal(shouldPollActiveJobs("queued"), true);
  assert.equal(shouldPollActiveJobs("running"), true);
  assert.equal(shouldPollActiveJobs("done"), false);
  assert.equal(shouldPollActiveJobs("failed"), false);
  assert.equal(shouldPollActiveJobs("cancelled"), false);
  assert.equal(shouldPollActiveJobs(undefined), false);
});
