import type { AdminCompany, JobStatus } from "../../types";

type Props = {
  company: AdminCompany;
  selected: boolean;
  onSelect: () => void;
};

const COUNTER_STATUSES: JobStatus[] = ["queued", "running", "done", "failed", "cancelled"];

export function CompanyCard({ company, selected, onSelect }: Props) {
  return (
    <button
      className={`company-card${selected ? " company-card-active" : ""}`}
      aria-pressed={selected}
      title="Ver o processamento da empresa"
      onClick={onSelect}
    >
      <span className="company-card-head">
        <span className="job-kind">{company.name}</span>
        <span className="job-id">#{company.id}</span>
      </span>
      <span className="company-card-meta">
        {company.total_jobs} {company.total_jobs === 1 ? "job" : "jobs"} · quota {company.job_quota} · limite{" "}
        {company.max_concurrent_jobs}
      </span>
      <span className="company-counters" aria-label="Jobs por status">
        {COUNTER_STATUSES.filter((status) => company[status] > 0).map((status) => (
          <span key={status} className={`badge badge-${status}`} title={`${company[status]} ${status}`}>
            {company[status]}
          </span>
        ))}
        {company.total_jobs === 0 && <span className="job-muted">sem jobs</span>}
      </span>
    </button>
  );
}
