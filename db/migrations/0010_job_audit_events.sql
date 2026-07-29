CREATE TABLE job_audit_events (
  id BIGSERIAL PRIMARY KEY,
  job_id INT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  company_id INT NOT NULL REFERENCES companies(id),
  event_type TEXT NOT NULL CHECK (event_type IN ('submitted', 'cancelled', 'retry_requested', 'completed', 'failed')),
  actor TEXT NOT NULL CHECK (char_length(actor) BETWEEN 3 AND 100 AND actor !~ '[[:cntrl:]]'),
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  trace_id TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

INSERT INTO job_audit_events (job_id, company_id, event_type, actor, occurred_at)
SELECT job_id, company_id, 'cancelled', cancelled_by, cancelled_at
FROM job_cancellation_audits;

INSERT INTO job_audit_events (job_id, company_id, event_type, actor, occurred_at, trace_id)
SELECT j.id, j.company_id, 'submitted', 'system:legacy', j.created_at, j.trace_id
FROM jobs AS j
WHERE NOT EXISTS (
  SELECT 1 FROM job_audit_events AS a
  WHERE a.job_id = j.id AND a.event_type = 'submitted'
);

CREATE UNIQUE INDEX uq_job_audit_submitted
  ON job_audit_events (job_id)
  WHERE event_type = 'submitted';

CREATE UNIQUE INDEX uq_job_audit_cancelled
  ON job_audit_events (job_id)
  WHERE event_type = 'cancelled';

CREATE INDEX ix_job_audit_events_job_time
  ON job_audit_events (job_id, occurred_at DESC, id DESC);

CREATE INDEX ix_job_audit_events_company_time
  ON job_audit_events (company_id, occurred_at DESC, id DESC);

DROP TABLE job_cancellation_audits;
