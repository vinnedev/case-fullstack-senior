import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchPage } from "../../api";
import type { Page } from "../../api";
import { useDelayedLoading } from "../../hooks/useDelayedLoading";
import { getQueryErrorMessage } from "../../jobQueries";
import { parseAdminCompanies } from "../../types";
import type { AdminCompany } from "../../types";
import { Card } from "../atoms/Card";
import { CompanyCard } from "../molecules/CompanyCard";
import { CompanyDetailPanel } from "../molecules/CompanyDetailPanel";
import { Pagination } from "../molecules/Pagination";

export function AdminPanel({ auth }: { auth: string }) {
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(10);
  const [selected, setSelected] = useState<number | null>(null);

  const { data, isLoading, isError, error } = useQuery<Page<AdminCompany>>({
    queryKey: ["admin-companies", auth, page, pageSize],
    queryFn: ({ signal }) =>
      fetchPage(`/admin/companies?limit=${pageSize}&offset=${page * pageSize}`, auth, parseAdminCompanies, signal),
  });
  const showSkeleton = useDelayedLoading(isLoading);
  const hasReplacementError = isError && !data;
  const hasBackgroundError = isError && Boolean(data);
  const companies = data?.items ?? [];
  const total = data?.total ?? 0;
  const selectedCompany = companies.find((company) => company.id === selected) ?? null;

  return (
    <Card title="Visão administrativa">
      <p className="empty">Empresas lado a lado com o resumo de processamento. Clique numa empresa para ver os jobs.</p>
      {hasBackgroundError && (
        <p className="empty job-error" role="alert">
          {getQueryErrorMessage(error, "Não foi possível atualizar as empresas. Tente novamente.")}
        </p>
      )}
      {showSkeleton ? (
        <div className="company-grid" role="status" aria-label="Carregando">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="company-card" aria-hidden="true">
              <span className="skeleton skeleton-text" />
              <span className="skeleton skeleton-badge" />
            </div>
          ))}
        </div>
      ) : hasReplacementError ? (
        <p className="empty job-error" role="alert">
          {getQueryErrorMessage(error, "Não foi possível carregar as empresas. Tente novamente.")}
        </p>
      ) : companies.length === 0 ? (
        <p className="empty">{page === 0 ? "Nenhuma empresa cadastrada." : "Fim da lista."}</p>
      ) : (
        <div className="company-grid">
          {companies.map((company) => (
            <CompanyCard
              key={company.id}
              company={company}
              selected={selected === company.id}
              onSelect={() => setSelected(selected === company.id ? null : company.id)}
            />
          ))}
        </div>
      )}
      {selectedCompany && <CompanyDetailPanel auth={auth} company={selectedCompany} />}
      {total > 0 && (
        <Pagination
          page={page}
          pageSize={pageSize}
          total={total}
          onPage={(p) => {
            setPage(p);
            setSelected(null);
          }}
          onPageSize={(size) => {
            setPageSize(size);
            setPage(0);
            setSelected(null);
          }}
          itemNoun={["empresa", "empresas"]}
        />
      )}
    </Card>
  );
}
