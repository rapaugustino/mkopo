import type { ReactNode } from "react";

interface Props {
  /** Main title — large, e.g. "LN-2026-0847 · Atlas Holdings LLC". */
  title: ReactNode;
  /** Secondary line — small, muted, e.g. "Bridge loan · $2.4M · J. Davis (owner)". */
  sub?: ReactNode;
  /** Slot to the right of the title, typically a StagePill or status chip. */
  badge?: ReactNode;
  /** Right-side action buttons (Filter / New loan, Audit / Approve, etc.). */
  actions?: ReactNode;
  /** Optional leading element (e.g. the ML logo on the pipeline page). */
  leading?: ReactNode;
}

/**
 * Card-shaped page header used across the mockups. Mirrors `pp-brand`,
 * `cf-header`, and `dc-header`. Keep this lean — title + sub + badge +
 * actions. Anything more specific (KPI strip, tab nav) lives in the page
 * below it.
 */
export function BrandHeader({ title, sub, badge, actions, leading }: Props) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
      <div className="flex min-w-0 items-center gap-3">
        {leading}
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-[15px] font-medium tracking-tight">
              {title}
            </p>
            {badge}
          </div>
          {sub && (
            <p className="mt-0.5 truncate text-[12px] text-[var(--color-text-secondary)]">
              {sub}
            </p>
          )}
        </div>
      </div>
      {actions && (
        <div className="flex shrink-0 items-center gap-1.5">{actions}</div>
      )}
    </div>
  );
}
