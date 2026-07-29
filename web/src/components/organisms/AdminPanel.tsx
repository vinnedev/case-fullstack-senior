import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { get } from "../../api";
import { useDelayedLoading } from "../../hooks/useDelayedLoading";
import { parseAdminJobs } from "../../types";
import type { AdminJob } from "../../types";
import { Badge } from "../atoms/Badge";
import { Button } from "../atoms/Button";
import { Card } from "../atoms/Card";
import { SkeletonList } from "../atoms/Skeleton";

const PAGE_SIZE = 50;

export function AdminPanel({ auth }: { auth: string }) {
  const [show, setShow] = useState(false);
  const [page, setPage] = useState(0);
  const { data, isLoading } = useQuery<AdminJob[]>({
    queryKey: ["admin-jobs", auth, page],
    queryFn: ({ signal }) => get(`/admin/jobs?limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`, auth, parseAdminJobs, signal),
    enabled: show,
  });
  const showSkeleton = useDelayedLoading(show && isLoading);
  const hasNext = (data?.length ?? 0) === PAGE_SIZE;

  return (
    <Card title="Visão administrativa" actions={!show && <Button onClick={() => setShow(true)}>Ver todos</Button>}>
      {!show && <p className="empty">Todos os jobs, de todas as empresas.</p>}
      {show && showSkeleton && <SkeletonList rows={4} />}
      {show && !showSkeleton && (
        <>
          <ul className="job-list">
            {data?.map((j) => (
              <li key={j.id} className="job-row">
                <span className="job-id">#{j.id}</span>
                <span className="job-kind">empresa {j.company_id}</span>
                <Badge status={j.status} />
              </li>
            ))}
          </ul>
          {(page > 0 || hasNext) && (
            <div className="pagination">
              <Button disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                Anterior
              </Button>
              <span className="page-indicator">página {page + 1}</span>
              <Button disabled={!hasNext} onClick={() => setPage((p) => p + 1)}>
                Próxima
              </Button>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
