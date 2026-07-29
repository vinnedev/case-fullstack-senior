export function SkeletonRow() {
  return (
    <div className="skeleton-row" aria-hidden="true">
      <span className="skeleton skeleton-id" />
      <span className="skeleton skeleton-text" />
      <span className="skeleton skeleton-badge" />
    </div>
  );
}

export function SkeletonList({ rows = 5 }: { rows?: number }) {
  return (
    <div role="status" aria-label="Carregando">
      {Array.from({ length: rows }, (_, i) => (
        <SkeletonRow key={i} />
      ))}
    </div>
  );
}
