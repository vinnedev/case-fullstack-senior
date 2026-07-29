import type { ReactNode, SelectHTMLAttributes } from "react";

type Props = SelectHTMLAttributes<HTMLSelectElement> & { label?: string; children: ReactNode };

export function Select({ label, children, ...rest }: Props) {
  const control = (
    <span className="select-wrap">
      <select {...rest}>{children}</select>
      <svg className="select-chevron" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
        <path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  );
  if (!label) return control;
  return (
    <label className="select-label">
      <span>{label}</span>
      {control}
    </label>
  );
}
