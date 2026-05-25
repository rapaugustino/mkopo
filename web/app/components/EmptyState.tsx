"use client";

import type { ReactNode } from "react";
import {
  IconActivity,
  IconChartLine,
  IconFile,
  IconInbox,
  IconListSearch,
  IconMessage2,
  IconSparkles,
} from "@tabler/icons-react";

/**
 * Consistent empty-state component used across the app.
 *
 * Replaces a year's worth of ad-hoc "No X yet." paragraphs scattered
 * inside tables, panels, and tabs with one centered card pattern:
 *
 *     ┌───────────────────────────┐
 *     │       [ small icon ]      │
 *     │       Headline copy       │
 *     │     Sub-copy explaining   │
 *     │           [ CTA ]         │
 *     └───────────────────────────┘
 *
 * The visual treatment is intentionally restrained — a 28px iconic
 * tile, two lines of copy, optional action. Banks underwriters spend
 * eight hours a day in this app; cute illustrations would feel
 * cheap by the third loan.
 *
 * The "icon" set is keyed by a small ``variant`` prop so call sites
 * don't have to import an icon library on every empty-state usage.
 * If you need an icon not in the variant list, pass the component
 * directly via ``Icon``.
 */

const VARIANT_ICONS = {
  documents: IconFile,
  inbox: IconInbox,
  review: IconListSearch,
  activity: IconActivity,
  chart: IconChartLine,
  message: IconMessage2,
  spark: IconSparkles,
} as const;

type Variant = keyof typeof VARIANT_ICONS;

interface EmptyStateProps {
  /** Optional pre-named icon. Falls back to spark if neither
   *  ``variant`` nor ``Icon`` is supplied. */
  variant?: Variant;
  /** Override the icon component directly. Use when you need
   *  something outside the variant set. Wins over ``variant``. */
  Icon?: React.ComponentType<{ size?: number }>;
  /** Headline. Keep short — "No documents yet", "No risk flags". */
  title: string;
  /** One-line supporting copy. Describe *what to do* or
   *  *what would appear here*. */
  description?: ReactNode;
  /** Optional CTA — a button or link. */
  action?: ReactNode;
  /** Visual density. ``compact`` fits inside narrow panels (the
   *  default is comfortable). */
  size?: "compact" | "comfortable";
}

export function EmptyState({
  variant,
  Icon,
  title,
  description,
  action,
  size = "comfortable",
}: EmptyStateProps) {
  const Resolved = Icon ?? VARIANT_ICONS[variant ?? "spark"];
  const pad = size === "compact" ? "px-4 py-5" : "px-6 py-8";

  return (
    <div
      className={`flex flex-col items-center justify-center gap-2.5 text-center ${pad}`}
    >
      <span
        className="inline-flex h-9 w-9 items-center justify-center rounded-full"
        style={{
          background: "var(--color-background-secondary)",
          color: "var(--color-text-secondary)",
        }}
      >
        <Resolved size={16} />
      </span>
      <p className="text-[13px] font-medium text-[var(--color-text-primary)]">
        {title}
      </p>
      {description && (
        <p className="max-w-[42ch] text-[12px] leading-relaxed text-[var(--color-text-tertiary)]">
          {description}
        </p>
      )}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}
