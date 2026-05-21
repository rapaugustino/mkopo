import type { ReactNode } from "react";

interface Props {
  label: ReactNode;
  value: ReactNode;
  /** Tiny trailing line, e.g. "+0.8 vs last week" or "2 missing docs". */
  trend?: ReactNode;
  /** Optional Tabler icon component, rendered next to the label. */
  Icon?: React.ComponentType<{ size?: number }>;
  /** Active state — subtle bg + brand bottom border. Used by pipeline stages. */
  active?: boolean;
  /** Override the trend's colour. Default is muted tertiary. */
  trendColor?: string;
}

/**
 * KPI / stage tile used in the pipeline view (5 across) and the
 * underwriting workspace (4 across). Mirrors pp-stage + ev-stat in the
 * mockups.
 *
 * One primitive, two callers — anywhere we want to tighten typography
 * (font sizes, value weight, label tracking), it changes once here.
 */
export function StatTile({
  label,
  value,
  trend,
  Icon,
  active = false,
  trendColor,
}: Props) {
  return (
    <div
      className="relative px-3 py-2.5"
      style={{
        background: active ? "var(--color-background-secondary)" : undefined,
      }}
    >
      <p className="flex items-center gap-1.5 text-[11px] uppercase tracking-[0.04em] text-[var(--color-text-secondary)]">
        {Icon && <Icon size={12} />}
        {label}
      </p>
      <p className="tabular-value mt-1.5 text-[20px] font-medium">{value}</p>
      {trend && (
        <p
          className="mt-1 text-[11px]"
          style={{ color: trendColor ?? "var(--color-text-tertiary)" }}
        >
          {trend}
        </p>
      )}
      {active && (
        <div
          className="absolute bottom-0 left-0 right-0 h-[2px]"
          style={{ background: "var(--color-brand)" }}
        />
      )}
    </div>
  );
}
