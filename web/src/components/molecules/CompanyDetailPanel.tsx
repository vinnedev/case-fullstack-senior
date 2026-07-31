import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchPage } from "../../api";
import type { Page } from "../../api";
import { useDelayedLoading } from "../../hooks/useDelayedLoading";
import { parseAdminJobs } from "../../types";
import type { AdminCompany, AdminJob, JobStatus } from "../../types";
import { Badge } from "../atoms/Badge";
import { FilterBar } from "./FilterBar";
import { Pagination } from "./Pagination";

const JOBS_PAGE_SIZE = 5;

export function CompanyDetailPanel({ auth, company }: { auth: string; company: AdminCompany }) {
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(JOBS_PAGE_SIZE);
  const [status, setStatus] = useState<JobStatus | null>(null);

  const params = new URLSearchParams({
    company_id: String(company.id),
    limit: String(pageSize),
    offset: String(page * pageSize),
  });
  if (status) params.set("status", status);

  const { data, isLoading } = useQuery<Page<AdminJob>>({
    queryKey: ["admin-company-jobs", auth, company.id, page, pageSize, status],
    queryFn: ({ signal }) => fetchPage(`/admin/jobs?${params.toString()}`, auth, parseAdminJobs, signal),
  });
  const showSkeleton = useDelayedLoading(isLoading);
  const jobs = data?.items ?? [];
  const total = data?.total ?? 0;

  return (
    <div className="job-detail">
      <div className="job-detail-meta">
        <span className="job-meta-item">
          quota restante: <strong>{company.job_quota}</strong>
        </span>
        <span className="job-meta-item">
          limite concorrente: <strong>{company.max_concurrent_jobs}</strong>
        </span>
        <span className="job-meta-item">
          total de jobs: <strong>{company.total_jobs}</strong>
        </span>
      </div>
      <FilterBar
        status={status}
        onStatus={(s) => {
          setStatus(s);
          setPage(0);
        }}
      />
      {showSkeleton ? (
        <div className="job-detail-skeleton">
          <span className="skeleton skeleton-text" />
          <span className="skeleton skeleton-text" />
        </div>
      ) : jobs.length === 0 ? (
        <p className="empty">{status ? "Nenhum job com esse status." : "Nenhum job por aqui ainda."}</p>
      ) : (
        <ul className="job-list">
          {jobs.map((job) => (
            <li key={job.id} className="job-row">
              <span className="job-id">#{job.id}</span>
              <span className="job-kind">job #{job.id}</span>
              <Badge status={job.status} />
            </li>
          ))}
        </ul>
      )}
      {total > 0 && (
        <Pagination
          page={page}
          pageSize={pageSize}
          total={total}
          onPage={setPage}
          onPageSize={(size) => {
            setPageSize(size);
            setPage(0);
          }}
        />
      )}
    </div>
  );
}
