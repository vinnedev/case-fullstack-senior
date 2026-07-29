import type { Job } from "../../types";
import { Badge } from "../atoms/Badge";
import { Button } from "../atoms/Button";

type Props = {
  job: Job;
  expanded: boolean;
  onToggle: () => void;
  onCancel: () => void;
  onRetry: () => void;
  busy: boolean;
};

const ACTIVE: Job["status"][] = ["queued", "running"];

export function JobRow({ job, expanded, onToggle, onCancel, onRetry, busy }: Props) {
  return (
    <div className="job-row">
      <span className="job-id">#{job.id}</span>
      <button className="job-kind job-link" title="Ver detalhes e resultado" aria-expanded={expanded} onClick={onToggle}>
        {job.kind}
        {job.result_count > 0 && <span className="job-results"> · {job.result_count} resultado</span>}
      </button>
      <Badge status={job.status} />
      {ACTIVE.includes(job.status) && (
        <Button variant="danger" onClick={onCancel} disabled={busy}>
          Cancelar
        </Button>
      )}
      {job.status === "failed" && (
        <Button onClick={onRetry} disabled={busy}>
          Tentar de novo
        </Button>
      )}
    </div>
  );
}
