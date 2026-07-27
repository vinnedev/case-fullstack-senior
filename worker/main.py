import time, psycopg

from shared.config.env import load_env, require_env
from shared.logging.wide_event import WideEvent, start_event

load_env()

SERVICE = "worker"

def process_once(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id, company_id, kind FROM jobs WHERE status='queued' ORDER BY id LIMIT 1")
        row = cur.fetchone()
        if not row: return False
        job_id, company_id, kind = row
        event = start_event(SERVICE, "job_processed", job_id=job_id, company_id=company_id, job_kind=kind)
        try:
            cur.execute("UPDATE jobs SET status='running', attempts=attempts+1, updated_at=now() WHERE id=%s", (job_id,))
            conn.commit()
            time.sleep(1)  # simula trabalho
            cur.execute("INSERT INTO job_results (job_id, payload) VALUES (%s,%s)", (job_id, f"resultado sensível da empresa {company_id}"))
            cur.execute("UPDATE companies SET job_quota = job_quota - 1 WHERE id=%s", (company_id,))
            cur.execute("UPDATE jobs SET status='done', updated_at=now() WHERE id=%s", (job_id,))
            conn.commit()
            event.add(outcome="done")
            return True
        except Exception as exc:
            conn.rollback()
            event.error(exc, outcome="failed")
            raise
        finally:
            event.emit()

def main():
    WideEvent(SERVICE, "startup").emit()
    conn = psycopg.connect(require_env("DATABASE_URL"))
    while True:
        if not process_once(conn): time.sleep(2)

if __name__ == "__main__":
    main()
