import type { JobStatus } from "../../types";

const LABELS: Record<JobStatus, string> = {
  queued: "na fila",
  running: "rodando",
  done: "concluído",
  failed: "falhou",
  cancelled: "cancelado",
};

export function Badge({ status }: { status: JobStatus }) {
  return <span className={`badge badge-${status}`}>{LABELS[status]}</span>;
}
