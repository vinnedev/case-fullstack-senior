import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "ghost" | "danger";

const CLASSES: Record<Variant, string> = {
  primary: "btn-primary",
  ghost: "btn-ghost",
  danger: "btn-danger-ghost",
};

type Props = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; children: ReactNode };

export function Button({ variant = "ghost", children, ...rest }: Props) {
  return (
    <button className={CLASSES[variant]} {...rest}>
      {children}
    </button>
  );
}
