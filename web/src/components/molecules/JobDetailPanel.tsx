import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { get } from "../../api";
import { useDelayedLoading } from "../../hooks/useDelayedLoading";
import { parseJobDetail, parseJobResult } from "../../types";
import type { JobDetail, JobResult } from "../../types";
import { Badge } from "../atoms/Badge";
import { Button } from "../atoms/Button";

const ROLE_LABELS: Record<string, string> = {
  user: "usuário",
  admin: "administrador",
  system: "sistema",
};

const EVENT_LABELS: Record<string, string> = {
  submitted: "Solicitado",
  retry_requested: "Retry solicitado",
  completed: "Concluído",
  failed: "Falha registrada",
};

function formatActor(actor: string) {
  const [company, role] = actor.split(":", 2);
  if (!company || !role) return actor;
  return `empresa ${company} - ${ROLE_LABELS[role] ?? role}`;
}

export function JobDetailPanel({ auth, jobId }: { auth: string; jobId: number }) {
  const [copied, setCopied] = useState(false);
  const detail = useQuery<JobDetail>({
    queryKey: ["job", auth, jobId],
    queryFn: ({ signal }) => get(`/jobs/${jobId}`, auth, parseJobDetail, signal),
  });
  const result = useQuery<JobResult>({
    queryKey: ["job-result", auth, jobId],
    queryFn: ({ signal }) => get(`/jobs/${jobId}/result`, auth, parseJobResult, signal),
    enabled: detail.data?.status === "done",
    retry: false,
  });
  const loading = useDelayedLoading(detail.isLoading || (detail.data?.status === "done" && result.isLoading));

  async function copyPayload() {
    if (!result.data) return;
    await navigator.clipboard.writeText(result.data.payload);
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  }

  function formatAuditDate(value: string) {
    return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
  }

  if (loading) {
    return (
      <div className="job-detail">
        <span className="skeleton skeleton-text" />
      </div>
    );
  }
  if (!detail.data) return <div className="job-detail">Não foi possível carregar o job.</div>;

  const job = detail.data;
  return (
    <div className="job-detail">
      <div className="job-detail-meta">
        <Badge status={job.status} />
        <span className="job-meta-item">
          {job.attempts} {job.attempts === 1 ? "tentativa" : "tentativas"}
        </span>
        <span className="job-meta-item">tipo: {job.kind}</span>
      </div>
      {job.last_error && (
        <div className="job-error-box">
          <span className="job-detail-label">Último erro</span>
          <p>{job.last_error}</p>
        </div>
      )}
      <div className="job-result-box">
        <div className="job-result-head">
          <span className="job-detail-label">Resultado</span>
          {result.data && (
            <Button onClick={copyPayload} aria-label="Copiar resultado">
              {copied ? "Copiado ✓" : "Copiar"}
            </Button>
          )}
        </div>
        {result.data ? (
          <pre className="job-payload">{result.data.payload}</pre>
        ) : job.cancellation ? (
          <p className="job-muted">
            Cancelamento solicitado por {formatActor(job.cancellation.cancelled_by)} em {formatAuditDate(job.cancellation.cancelled_at)}
          </p>
        ) : (
          <p className="job-muted">
            {job.status === "done" ? "resultado indisponível" : "o resultado aparece aqui quando o job concluir"}
          </p>
        )}
        {job.audit_events
          .filter((event) => event.event_type !== "cancelled" || !job.cancellation)
          .map((event) => (
          <p className="job-audit-meta" key={`${event.event_type}-${event.occurred_at}`}>
            {EVENT_LABELS[event.event_type] ?? event.event_type} por {formatActor(event.actor)} em {formatAuditDate(event.occurred_at)}
          </p>
          ))}
      </div>
    </div>
  );
}
