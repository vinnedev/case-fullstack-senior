import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, fetchJobsPage, post } from "../../api";
import { useDebounced } from "../../hooks/useDebounced";
import { useDelayedLoading } from "../../hooks/useDelayedLoading";
import { useToast } from "../../toast";
import { parseJobCancelled, parseJobRetried } from "../../types";
import type { JobStatus, JobsPage } from "../../types";
import { Card } from "../atoms/Card";
import { SkeletonList } from "../atoms/Skeleton";
import { FilterBar } from "../molecules/FilterBar";
import { JobDetailPanel } from "../molecules/JobDetailPanel";
import { JobRow } from "../molecules/JobRow";
import { Pagination } from "../molecules/Pagination";
import { SubmitButton } from "./SubmitButton";

const ACTIVE = ["queued", "running"];

export function JobsPanel({ auth }: { auth: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(10);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [search, setSearch] = useState("");
  const [highlightId, setHighlightId] = useState<number | null>(null);
  const debouncedSearch = useDebounced(search.trim());

  const params = new URLSearchParams({ limit: String(pageSize), offset: String(page * pageSize) });
  if (status) params.set("status", status);
  if (debouncedSearch) params.set("search", debouncedSearch);

  const { data, isLoading } = useQuery<JobsPage>({
    queryKey: ["jobs", auth, page, pageSize, status, debouncedSearch],
    queryFn: ({ signal }) => fetchJobsPage(auth, params, signal),
    // polling apenas com itens pendentes (queued/running); em repouso não há polling —
    // mutações invalidam a query e o refetch-on-focus cobre mudanças externas
    refetchInterval: (query) => (query.state.data?.jobs.some((j) => ACTIVE.includes(j.status)) ? 1000 : false),
  });
  const showSkeleton = useDelayedLoading(isLoading);

  const resetToFirstPage = () => {
    setPage(0);
    setExpanded(null);
  };
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["jobs", auth] });
  const onError = (error: unknown, jobId: number) => {
    const detail = error instanceof ApiError ? error.message : "erro inesperado";
    toast("error", `Job #${jobId}`, detail);
  };
  const cancel = useMutation({
    mutationFn: (id: number) => post(`/jobs/${id}/cancel`, auth, parseJobCancelled),
    onSuccess: (_data, id) => toast("info", `Job #${id} cancelado`),
    onError: (error, id) => onError(error, id),
    onSettled: invalidate,
  });
  const retry = useMutation({
    mutationFn: (id: number) => post(`/jobs/${id}/retry`, auth, parseJobRetried),
    onSuccess: (_data, id) => toast("success", `Job #${id} reenfileirado`),
    onError: (error, id) => onError(error, id),
    onSettled: invalidate,
  });

  // UX de criação: volta pra página 1, remove filtro que esconderia o job novo
  // (ele nasce "queued") e destaca a linha quando ela aparecer na lista.
  const onCreated = (jobId: number) => {
    resetToFirstPage();
    if (status && status !== "queued") setStatus(null);
    setSearch("");
    setHighlightId(jobId);
    setTimeout(() => setHighlightId(null), 2500);
    invalidate();
  };

  const total = data?.total ?? 0;
  const jobs = data?.jobs ?? [];
  const filtering = Boolean(status || debouncedSearch);

  return (
    <Card title="Jobs" actions={<SubmitButton auth={auth} onCreated={onCreated} />}>
      <FilterBar
        status={status}
        search={search}
        onStatus={(s) => {
          setStatus(s);
          resetToFirstPage();
        }}
        onSearch={(s) => {
          setSearch(s);
          resetToFirstPage();
        }}
      />
      {showSkeleton ? (
        <SkeletonList rows={Math.min(pageSize, 6)} />
      ) : jobs.length === 0 ? (
        <p className="empty">
          {filtering ? "Nenhum job encontrado com esses filtros." : page === 0 ? "Nenhum job por aqui ainda." : "Fim da lista."}
        </p>
      ) : (
        <ul className="job-list">
          {jobs.map((j) => (
            <li key={j.id} className={highlightId === j.id ? "job-new" : undefined}>
              <JobRow
                job={j}
                expanded={expanded === j.id}
                onToggle={() => setExpanded(expanded === j.id ? null : j.id)}
                onCancel={() => cancel.mutate(j.id)}
                onRetry={() => retry.mutate(j.id)}
                busy={(cancel.isPending && cancel.variables === j.id) || (retry.isPending && retry.variables === j.id)}
              />
              {expanded === j.id && <JobDetailPanel auth={auth} jobId={j.id} />}
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
            resetToFirstPage();
          }}
        />
      )}
    </Card>
  );
}
