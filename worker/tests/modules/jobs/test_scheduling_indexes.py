def _seed_scheduling_rows(db) -> None:
    db.execute("INSERT INTO companies (id, name, job_quota) VALUES (1, 'Acme', 10)")
    db.execute(
        """
        INSERT INTO jobs (company_id, kind, status, attempts, next_attempt_at)
        SELECT 1, 'report', 'queued', 0, now() + make_interval(secs => 3600 + value)
        FROM generate_series(1, 10000) AS value
        """
    )
    db.execute(
        """
        INSERT INTO jobs (company_id, kind, status, attempts, next_attempt_at)
        VALUES (1, 'report', 'queued', 0, now() - interval '1 second')
        """
    )
    db.execute("INSERT INTO jobs (company_id, kind, status, attempts) VALUES (1, 'report', 'queued', 0)")
    db.commit()
    db.execute("ANALYZE jobs")


def _explain(db, query: str) -> str:
    return "\n".join(row["QUERY PLAN"] for row in db.execute(f"EXPLAIN (COSTS OFF) {query}").fetchall())


def test_job_scheduling_indexes_are_partial_and_reversible_schema(db):
    indexes = {
        row["indexname"]: row["indexdef"]
        for row in db.execute(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'jobs'
              AND indexname IN ('ix_jobs_queued_ready', 'ix_jobs_queued_scheduled')
            """
        ).fetchall()
    }

    assert indexes["ix_jobs_queued_ready"] == (
        "CREATE INDEX ix_jobs_queued_ready ON public.jobs USING btree (id) WHERE ((status = 'queued'::text) AND (next_attempt_at IS NULL))"
    )
    assert indexes["ix_jobs_queued_scheduled"] == (
        "CREATE INDEX ix_jobs_queued_scheduled ON public.jobs USING btree (next_attempt_at, id) "
        "WHERE ((status = 'queued'::text) AND (next_attempt_at IS NOT NULL))"
    )


def test_scheduler_queries_use_scheduled_jobs_index(db):
    _seed_scheduling_rows(db)

    next_attempt_plan = _explain(
        db,
        """
        SELECT extract(epoch FROM min(next_attempt_at) - now()) AS s
        FROM jobs
        WHERE status = 'queued' AND next_attempt_at > now()
        """,
    )
    ready_claim_plan = _explain(
        db,
        """
        SELECT id
        FROM jobs
        WHERE status = 'queued' AND next_attempt_at IS NULL
        ORDER BY id
        LIMIT 1
        FOR UPDATE SKIP LOCKED
        """,
    )
    claim_plan = _explain(
        db,
        """
        SELECT id, company_id, kind, trace_id, attempts,
               extract(epoch FROM now() - updated_at) AS queue_wait_s
        FROM jobs
        WHERE status = 'queued'
          AND attempts < 3
          AND (next_attempt_at IS NULL OR next_attempt_at <= now())
        ORDER BY id
        LIMIT 1
        FOR UPDATE SKIP LOCKED
        """,
    )

    assert "ix_jobs_queued_scheduled" in next_attempt_plan
    assert "ix_jobs_queued_ready" in ready_claim_plan
    assert "ix_jobs_queued_scheduled" in claim_plan
