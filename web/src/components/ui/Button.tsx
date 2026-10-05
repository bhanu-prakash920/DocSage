import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Link, type LinkProps } from "react-router-dom";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

interface Common {
  variant?: Variant;
  size?: Size;
  icon?: ReactNode;
  iconOnly?: boolean;
  block?: boolean;
}

export function btnClass({ variant = "secondary", size = "md", iconOnly, block }: Common, extra = "") {
  return [
    "btn",
    `btn--${variant}`,
    size !== "md" && `btn--${size}`,
    iconOnly && "btn--icon",
    block && "btn--block",
    extra,
  ]
    .filter(Boolean)
    .join(" ");
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, Common {
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant, size, icon, iconOnly, block, loading, className, children, disabled, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={btnClass({ variant, size, iconOnly, block }, className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading && <span className="spinner" aria-hidden="true" />}
      {icon}
      {iconOnly ? <span className="sr-only">{children}</span> : children}
    </button>
  );
});

export function ButtonLink({ variant, size, icon, block, className, children, ...rest }: LinkProps & Common) {
  return (
    <Link className={btnClass({ variant, size, block }, className as string)} {...rest}>
      {icon}
      {children}
    </Link>
  );
}
