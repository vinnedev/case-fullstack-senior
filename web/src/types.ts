export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export type Job = {
  id: number;
  kind: string;
  status: JobStatus;
  created_at: string;
  result_count: number;
};

export type JobDetail = {
  id: number;
  company_id: number;
  kind: string;
  status: JobStatus;
  attempts: number;
  last_error: string | null;
  cancellation: CancellationAudit | null;
  audit_events: AuditEvent[];
};

export type CancellationAudit = {
  cancelled_by: string;
  cancelled_at: string;
};

export type AuditEvent = {
  event_type: "submitted" | "cancelled" | "retry_requested" | "completed" | "failed";
  actor: string;
  occurred_at: string;
  trace_id: string | null;
};

export type JobResult = { payload: string };
export type JobCreated = { id: number; status: "queued" };
export type JobCancelled = { id: number; status: "cancelled" };
export type JobRetried = { id: number; status: "queued"; attempts: number };
export type AdminJob = { id: number; company_id: number; status: JobStatus };
export type JobsPage = { jobs: Job[]; total: number };

const JOB_STATUSES = new Set<JobStatus>(["queued", "running", "done", "failed", "cancelled"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requireRecord(value: unknown, context: string): Record<string, unknown> {
  if (!isRecord(value)) throw new TypeError(`Resposta inválida em ${context}`);
  return value;
}

function requireNumber(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new TypeError(`Campo inválido: ${field}`);
  return value;
}

function requireInteger(value: unknown, field: string): number {
  const parsed = requireNumber(value, field);
  if (!Number.isSafeInteger(parsed)) throw new TypeError(`Campo inválido: ${field}`);
  return parsed;
}

function requirePositiveInteger(value: unknown, field: string): number {
  const parsed = requireInteger(value, field);
  if (parsed <= 0) throw new TypeError(`Campo inválido: ${field}`);
  return parsed;
}

function requireNonNegativeInteger(value: unknown, field: string, maximum?: number): number {
  const parsed = requireInteger(value, field);
  if (parsed < 0 || (maximum !== undefined && parsed > maximum)) {
    throw new TypeError(`Campo inválido: ${field}`);
  }
  return parsed;
}

function requireString(value: unknown, field: string): string {
  if (typeof value !== "string") throw new TypeError(`Campo inválido: ${field}`);
  return value;
}

function requireNonEmptyString(value: unknown, field: string): string {
  const parsed = requireString(value, field);
  if (parsed.trim().length === 0) throw new TypeError(`Campo inválido: ${field}`);
  return parsed;
}

function requireDateTime(value: unknown, field: string): string {
  const parsed = requireNonEmptyString(value, field);
  if (Number.isNaN(Date.parse(parsed))) throw new TypeError(`Campo inválido: ${field}`);
  return parsed;
}

function requireStatus(value: unknown, field = "status"): JobStatus {
  if (typeof value !== "string" || !JOB_STATUSES.has(value as JobStatus)) {
    throw new TypeError(`Campo inválido: ${field}`);
  }
  return value as JobStatus;
}

function parseAuditEvents(value: unknown): AuditEvent[] {
  if (!Array.isArray(value)) throw new TypeError("Campo inválido: audit_events");
  const eventTypes = new Set<AuditEvent["event_type"]>(["submitted", "cancelled", "retry_requested", "completed", "failed"]);
  return value.map((item) => {
    const row = requireRecord(item, "evento de auditoria");
    if (typeof row.event_type !== "string" || !eventTypes.has(row.event_type as AuditEvent["event_type"])) {
      throw new TypeError("Campo inválido: audit_events.event_type");
    }
    if (row.trace_id !== null && typeof row.trace_id !== "string") throw new TypeError("Campo inválido: audit_events.trace_id");
    return {
      event_type: row.event_type as AuditEvent["event_type"],
      actor: requireNonEmptyString(row.actor, "audit_events.actor"),
      occurred_at: requireDateTime(row.occurred_at, "audit_events.occurred_at"),
      trace_id: row.trace_id,
    };
  });
}

export function parseJob(value: unknown): Job {
  const row = requireRecord(value, "job");
  return {
    id: requirePositiveInteger(row.id, "id"),
    kind: requireNonEmptyString(row.kind, "kind"),
    status: requireStatus(row.status),
    created_at: requireDateTime(row.created_at, "created_at"),
    result_count: requireNonNegativeInteger(row.result_count, "result_count"),
  };
}

export function parseJobs(value: unknown): Job[] {
  if (!Array.isArray(value)) throw new TypeError("Resposta inválida na listagem de jobs");
  return value.map(parseJob);
}

export function parseJobDetail(value: unknown): JobDetail {
  const row = requireRecord(value, "detalhe do job");
  if (row.last_error !== null && typeof row.last_error !== "string") {
    throw new TypeError("Campo inválido: last_error");
  }
  const cancellation = row.cancellation;
  if (cancellation !== null && !isRecord(cancellation)) throw new TypeError("Campo inválido: cancellation");
  const parsedCancellation =
    cancellation === null
      ? null
      : {
          cancelled_by: requireNonEmptyString(cancellation.cancelled_by, "cancellation.cancelled_by"),
          cancelled_at: requireDateTime(cancellation.cancelled_at, "cancellation.cancelled_at"),
        };
  return {
    id: requirePositiveInteger(row.id, "id"),
    company_id: requirePositiveInteger(row.company_id, "company_id"),
    kind: requireNonEmptyString(row.kind, "kind"),
    status: requireStatus(row.status),
    attempts: requireNonNegativeInteger(row.attempts, "attempts", 3),
    last_error: row.last_error,
    cancellation: parsedCancellation,
    audit_events: parseAuditEvents(row.audit_events),
  };
}

export function parseJobResult(value: unknown): JobResult {
  const row = requireRecord(value, "resultado do job");
  return { payload: requireNonEmptyString(row.payload, "payload") };
}

function requireExpectedStatus<T extends JobStatus>(value: unknown, expected: T): T {
  const status = requireStatus(value);
  if (status !== expected) throw new TypeError(`Status inválido: esperado ${expected}`);
  return status as T;
}

export function parseJobCreated(value: unknown): JobCreated {
  const row = requireRecord(value, "mutação do job");
  return { id: requirePositiveInteger(row.id, "id"), status: requireExpectedStatus(row.status, "queued") };
}

export function parseJobCancelled(value: unknown): JobCancelled {
  const row = requireRecord(value, "cancelamento do job");
  return { id: requirePositiveInteger(row.id, "id"), status: requireExpectedStatus(row.status, "cancelled") };
}

export function parseJobRetried(value: unknown): JobRetried {
  const row = requireRecord(value, "retry do job");
  return {
    id: requirePositiveInteger(row.id, "id"),
    status: requireExpectedStatus(row.status, "queued"),
    attempts: requireNonNegativeInteger(row.attempts, "attempts", 3),
  };
}

export function parseAdminJobs(value: unknown): AdminJob[] {
  if (!Array.isArray(value)) throw new TypeError("Resposta inválida na listagem administrativa");
  return value.map((item) => {
    const row = requireRecord(item, "job administrativo");
    return {
      id: requirePositiveInteger(row.id, "id"),
      company_id: requirePositiveInteger(row.company_id, "company_id"),
      status: requireStatus(row.status),
    };
  });
}
