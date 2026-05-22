import type { ButtonHTMLAttributes } from "react";

interface Props extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  /** Accessible label — required, since this button has no visible text. */
  label: string;
  /** Tabler icon component. */
  Icon: React.ComponentType<{ size?: number }>;
  /** Visual size of the icon (button hit area scales accordingly). */
  size?: "xs" | "sm" | "md";
  /** Visual emphasis. ``ghost`` is the default (transparent, hover bg).
   *  ``solid`` adds a hairline border for inline toolbars where the
   *  button would otherwise vanish into a flat row. */
  variant?: "ghost" | "solid";
  /** Active/pressed state for toggles — applies the brand background so
   *  segmented controls (Observability's 1h/6h/24h/7d picker) get a
   *  clear "selected" state without each call site re-styling. */
  active?: boolean;
}

/**
 * Compact icon-only button. Use for prev/next/close inside drawers,
 * window pickers, and toolbar rows where text would be redundant.
 *
 * The variants and active state cover the three real-world uses we
 * found in the audit:
 * - ``ghost``                → DocSourceViewer's prev/next/close (no chrome
 *                              when the row is already bordered).
 * - ``solid``                → standalone icon button outside a toolbar.
 * - ``ghost`` + ``active``   → Observability's window-picker segmented
 *                              control's selected button.
 *
 * Always pass ``label`` — it lands on ``aria-label`` *and* ``title`` so
 * the screen reader and the hover tooltip carry the same text. The
 * old hand-rolled icon buttons forgot the aria-label half the time.
 */
export function IconButton({
  label,
  Icon,
  size = "sm",
  variant = "ghost",
  active = false,
  className = "",
  ...rest
}: Props) {
  const iconSize = size === "xs" ? 12 : size === "sm" ? 14 : 16;
  const pad = size === "xs" ? "p-1" : size === "sm" ? "p-1.5" : "p-2";
  const chrome =
    variant === "solid"
      ? "border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]"
      : "";
  return (
    <button
      {...rest}
      aria-label={label}
      title={label}
      className={
        "inline-flex items-center justify-center rounded-md transition-colors " +
        "disabled:cursor-not-allowed disabled:opacity-50 " +
        chrome +
        " " +
        pad +
        " " +
        (active
          ? "bg-[var(--color-background-success)] text-[var(--color-brand)] "
          : "text-[var(--color-text-secondary)] hover:bg-[var(--color-background-secondary)] hover:text-[var(--color-text-primary)] ") +
        className
      }
    >
      <Icon size={iconSize} />
    </button>
  );
}
