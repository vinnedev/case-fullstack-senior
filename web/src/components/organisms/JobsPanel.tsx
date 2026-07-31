import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, fetchJobsPage, post } from "../../api";
import { useDelayedLoading } from "../../hooks/useDelayedLoading";
import {
  invalidateJobMutationCaches,
  jobQueryKeys,
  shouldPollActiveJobs,
} from "../../jobQueries";
import type { JobMutationVariables } from "../../jobQueries";
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

export function JobsPanel({ auth }: { auth: string }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(10);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [status, setStatus] = useState<JobStatus | null>(null);
  const [highlightId, setHighlightId] = useState<number | null>(null);

  const params = new URLSearchParams({ limit: String(pageSize), offset: String(page * pageSize) });
  if (status) params.set("status", status);

  const { data, isLoading } = useQuery<JobsPage>({
    queryKey: jobQueryKeys.page(auth, page, pageSize, status),
    queryFn: ({ signal }) => fetchJobsPage(auth, params, signal),
    // polling apenas com itens pendentes (queued/running); em repouso não há polling —
    // mutações invalidam a query e o refetch-on-focus cobre mudanças externas
    refetchInterval: (query) => (query.state.data?.jobs.some((j) => shouldPollActiveJobs(j.status)) ? 1000 : false),
  });
  const showSkeleton = useDelayedLoading(isLoading);

  const resetToFirstPage = () => {
    setPage(0);
    setExpanded(null);
  };
  const invalidateList = () => queryClient.invalidateQueries({ queryKey: jobQueryKeys.list(auth) });
  const invalidateMutation = (variables: JobMutationVariables) =>
    invalidateJobMutationCaches(
      (queryKey) => queryClient.invalidateQueries({ queryKey }),
      variables,
    );
  const onError = (error: unknown, jobId: number) => {
    const detail = error instanceof ApiError ? error.message : "erro inesperado";
    toast("error", `Job #${jobId}`, detail);
  };
  const cancel = useMutation({
    mutationFn: ({ jobId, auth: mutationAuth }: JobMutationVariables) =>
      post(`/jobs/${jobId}/cancel`, mutationAuth, parseJobCancelled),
    onSuccess: (_data, { jobId }) => toast("info", `Job #${jobId} cancelado`),
    onError: (error, { jobId }) => onError(error, jobId),
    onSettled: (_data, _error, variables) => invalidateMutation(variables),
  });
  const retry = useMutation({
    mutationFn: ({ jobId, auth: mutationAuth }: JobMutationVariables) =>
      post(`/jobs/${jobId}/retry`, mutationAuth, parseJobRetried),
    onSuccess: (_data, { jobId }) => toast("success", `Job #${jobId} reenfileirado`),
    onError: (error, { jobId }) => onError(error, jobId),
    onSettled: (_data, _error, variables) => invalidateMutation(variables),
  });

  // UX de criação: volta pra página 1, remove filtro que esconderia o job novo
  // (ele nasce "queued") e destaca a linha quando ela aparecer na lista.
  const onCreated = (jobId: number) => {
    resetToFirstPage();
    if (status && status !== "queued") setStatus(null);
    setHighlightId(jobId);
    setTimeout(() => setHighlightId(null), 2500);
    invalidateList();
  };

  const total = data?.total ?? 0;
  const jobs = data?.jobs ?? [];
  const filtering = Boolean(status);

  return (
    <Card title="Jobs" actions={<SubmitButton auth={auth} onCreated={onCreated} />}>
      <FilterBar
        status={status}
        onStatus={(s) => {
          setStatus(s);
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
                onCancel={() => cancel.mutate({ auth, jobId: j.id })}
                onRetry={() => retry.mutate({ auth, jobId: j.id })}
                busy={(cancel.isPending && cancel.variables?.jobId === j.id) || (retry.isPending && retry.variables?.jobId === j.id)}
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
