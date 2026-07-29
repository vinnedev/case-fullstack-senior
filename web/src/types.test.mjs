import assert from "node:assert/strict";
import test from "node:test";

import {
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
