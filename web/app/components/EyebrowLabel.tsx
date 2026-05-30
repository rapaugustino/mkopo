import type { ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Density. ``compact`` uses 10px; ``regular`` uses 11px. */
  size?: "compact" | "regular";
  /** Optional className for one-off layout tweaks (margin, alignment). */
  className?: string;
}

/**
 * Uppercase eyebrow label — the small tracked all-caps text used as a
 * micro-heading above stat groupings, KPI rows, and "Action details"
 * panels.
 *
 * The audit found eyebrow labels in the wild split across three font
 * sizes (10px / 10.5px / 11px) and two letter-spacings
 * (``tracking-wider`` / ``tracking-wide``). Centralising fixes the
 * drift; new code should always go through this primitive instead of
 * hand-rolling another ``<p className="text-[11px] uppercase ...">``.
 *
 * Distinct from ``SectionLabel`` (mixed-case, larger, used inside
 * cards) — eyebrows are the smaller, tighter micro-headings used as
 * column or sub-group titles.
 *
 * Usage:
 *     <EyebrowLabel>Action details</EyebrowLabel>
 *     <EyebrowLabel size="compact">Window</EyebrowLabel>
 */
export function EyebrowLabel({
  children,
  size = "regular",
  className = "",
}: Props) {
  const textSize = size === "compact" ? "text-[10px]" : "text-[11px]";
  return (
    <p
      className={`${textSize} font-medium uppercase tracking-wider text-[var(--color-text-secondary)] ${className}`}
    >
      {children}
    </p>
  );
}
