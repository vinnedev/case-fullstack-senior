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
  search: string;
  onStatus: (status: JobStatus | null) => void;
  onSearch: (search: string) => void;
};

export function FilterBar({ status, search, onStatus, onSearch }: Props) {
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
      <div className="search-wrap">
        <svg className="search-icon" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
          <circle cx="6" cy="6" r="4.4" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="m9.5 9.5 3 3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
        <input
          type="search"
          placeholder="Buscar por tipo…"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          aria-label="Buscar jobs por tipo"
        />
      </div>
    </div>
  );
}
