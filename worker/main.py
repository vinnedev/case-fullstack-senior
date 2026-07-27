import time

from sqlalchemy import select

from modules.companies.models import Company
from modules.jobs.models import Job, JobResult
from shared.db.engine import session_scope
from shared.logging.wide_event import WideEvent, start_event

SERVICE = "worker"

def process_once() -> bool:
    with session_scope() as session:
        job = session.execute(
            select(Job).where(Job.status == "queued").order_by(Job.id).limit(1).with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if not job:
            return False
        event = start_event(SERVICE, "job_processed", job_id=job.id, company_id=job.company_id, job_kind=job.kind)
        try:
            job.status = "running"
            job.attempts += 1
            session.flush()
            time.sleep(1)  # simula trabalho
            session.add(JobResult(job_id=job.id, payload=f"resultado sensível da empresa {job.company_id}"))
            company = session.get(Company, job.company_id)
            if company is None:
                raise RuntimeError(f"company {job.company_id} não encontrada para o job {job.id}")
            company.job_quota -= 1
            job.status = "done"
            event.add(outcome="done", attempts=job.attempts)
            return True
        except Exception as exc:
            event.error(exc, outcome="failed")
            raise
        finally:
            event.emit()

def main():
    WideEvent(SERVICE, "startup").emit()
    while True:
        if not process_once():
            time.sleep(2)

if __name__ == "__main__":
    main()
