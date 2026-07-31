import { isPaginationNeeded } from "../../jobQueries";
import { Button } from "../atoms/Button";
import { Select } from "../atoms/Select";

export const PAGE_SIZES = [5, 10, 25, 50] as const;

type Props = {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
  onPageSize: (size: number) => void;
  itemNoun?: [singular: string, plural: string];
};

export function Pagination({ page, pageSize, total, onPage, onPageSize, itemNoun = ["job", "jobs"] }: Props) {
  if (!isPaginationNeeded(page, pageSize, total)) return null;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <nav className="pagination" aria-label="Paginação">
      <span className="page-indicator">
        {total} {total === 1 ? itemNoun[0] : itemNoun[1]} · página {page + 1} de {totalPages}
      </span>
      <div className="pagination-controls">
        <Select
          aria-label="Itens por página"
          value={pageSize}
          onChange={(e) => onPageSize(Number(e.target.value))}
        >
          {PAGE_SIZES.map((s) => (
            <option key={s} value={s}>
              {s} / página
            </option>
          ))}
        </Select>
        <Button onClick={() => onPage(page - 1)} disabled={page === 0}>
          ‹ Anterior
        </Button>
        <Button onClick={() => onPage(page + 1)} disabled={page + 1 >= totalPages}>
          Próxima ›
        </Button>
      </div>
    </nav>
  );
}
