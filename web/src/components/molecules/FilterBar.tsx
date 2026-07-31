import type { JobStatus } from "../../types";

const STATUSES: { value: JobStatus | null; label: string }[] = [
  { value: null, label: "Todos" },
  { value: "queued", label: "Na fila" },
  { value: "running", label: "Rodando" },
  { value: "done", label: "Concluídos" },
  { value: "failed", label: "Falharam" },
  { value: "cancelled", label: "Cancelados" },
];

type Props = {
  status: JobStatus | null;
  onStatus: (status: JobStatus | null) => void;
};

export function FilterBar({ status, onStatus }: Props) {
  return (
    <div className="filter-bar">
      <div className="filter-chips" role="tablist" aria-label="Filtrar por status">
        {STATUSES.map((s) => (
          <button
            key={s.label}
            role="tab"
            aria-selected={status === s.value}
            className={`chip${status === s.value ? " chip-active" : ""}`}
            onClick={() => onStatus(s.value)}
          >
            {s.label}
          </button>
        ))}
      </div>
    </div>
  );
}
