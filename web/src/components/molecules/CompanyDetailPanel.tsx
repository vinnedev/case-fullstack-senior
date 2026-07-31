import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchPage } from "../../api";
import type { Page } from "../../api";
import { useDelayedLoading } from "../../hooks/useDelayedLoading";
import { clampPage, getQueryErrorMessage, hasCompanyJobsScopeChanged } from "../../jobQueries";
import type { CompanyJobsScope } from "../../jobQueries";
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
  const currentScope: CompanyJobsScope = { auth, companyId: company.id, status };
  const [appliedScope, setAppliedScope] = useState(currentScope);
  const scopeChanged = hasCompanyJobsScopeChanged(appliedScope, currentScope);
  const visiblePage = scopeChanged ? 0 : page;

  const params = new URLSearchParams({
    company_id: String(company.id),
    limit: String(pageSize),
    offset: String(visiblePage * pageSize),
  });
  if (status) params.set("status", status);

  const { data, isLoading, isError, error } = useQuery<Page<AdminJob>>({
    queryKey: ["admin-company-jobs", auth, company.id, visiblePage, pageSize, status],
    queryFn: ({ signal }) => fetchPage(`/admin/jobs?${params.toString()}`, auth, parseAdminJobs, signal),
  });
  const showSkeleton = useDelayedLoading(isLoading);
  const hasReplacementError = isError && !data;
  const hasBackgroundError = isError && Boolean(data);
  const jobs = data?.items ?? [];
  const total = data?.total ?? 0;
  const displayPage = data && !scopeChanged ? clampPage(visiblePage, pageSize, data.total) : visiblePage;

  useEffect(() => {
    if (!scopeChanged) return;
    setAppliedScope({ auth, companyId: company.id, status });
    setPage(0);
  }, [auth, company.id, scopeChanged, status]);
  useEffect(() => {
    if (!data || scopeChanged) return;
    const nextPage = clampPage(page, pageSize, data.total);
    if (nextPage === page) return;
    setPage(nextPage);
  }, [data, page, pageSize, scopeChanged]);

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
      {hasBackgroundError && (
        <p className="empty job-error" role="alert">
          {getQueryErrorMessage(error, "Não foi possível atualizar os jobs da empresa. Tente novamente.")}
        </p>
      )}
      {showSkeleton ? (
        <div className="job-detail-skeleton">
          <span className="skeleton skeleton-text" />
          <span className="skeleton skeleton-text" />
        </div>
      ) : hasReplacementError ? (
        <p className="empty job-error" role="alert">
          {getQueryErrorMessage(error, "Não foi possível carregar os jobs da empresa. Tente novamente.")}
        </p>
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
          page={displayPage}
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
