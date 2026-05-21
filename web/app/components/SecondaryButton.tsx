import type { ButtonHTMLAttributes, ReactNode } from "react";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  Icon?: React.ComponentType<{ size?: number }>;
  size?: "sm" | "md";
  children: ReactNode;
}

/**
 * Outlined / ghost button — pairs with {@link PrimaryButton} for the
 * "Audit" / "Filter" / "Cancel" style. Hairline border, neutral hover,
 * primary text colour. Keeping both in one shape means a future theme
 * change (border weight, radius, hover treatment) lands in two files
 * not twenty.
 */
export function SecondaryButton({
  Icon,
  size = "md",
  children,
  className = "",
  ...rest
}: Props) {
  const padding = size === "sm" ? "px-2.5 py-1" : "px-3 py-1.5";
  return (
    <button
      {...rest}
      className={
        "flex items-center gap-1.5 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] " +
        "bg-[var(--color-background-primary)] text-xs text-[var(--color-text-primary)] " +
        "hover:bg-[var(--color-background-secondary)] disabled:opacity-50 " +
        `${padding} ${className}`
      }
    >
      {Icon && <Icon size={14} />}
      {children}
    </button>
  );
}
