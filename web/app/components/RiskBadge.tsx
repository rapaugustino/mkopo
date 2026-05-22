import type { RiskBand } from "@/lib/api";
import { humanizeRisk } from "@/lib/humanize";

/**
 * Risk-band display: a colored dot beside a humanised label
 * ("Low" / "Med" / "High"). Used everywhere a single loan's risk
 * surfaces as a status — pipeline table, party-page subtext, anywhere
 * the band is the data, not the visualisation.
 *
 * We don't use the Pill primitive here because pills feel heavy in
 * dense tables: a 6px dot + colored label reads as a status indicator,
 * a pill reads as a label. The visual language across the app was
 * inconsistent before this primitive (three different treatments —
 * inline dot+label on the pipeline, raw text on the party page,
 * SVG strokes on the concentration graph) and the audit flagged it.
 *
 * The SVG-stroke variant on the concentration graph is intentional
 * and stays as-is — it's the data viz, not a status badge, so the
 * shapes already carry the meaning.
 */
export function RiskBadge({
  band,
  size = "sm",
}: {
  band: RiskBand | string | null | undefined;
  /** `xs` is used inside dense table cells; `sm` everywhere else. */
  size?: "xs" | "sm";
}) {
  if (!band) {
    return <span className="text-[var(--color-text-tertiary)]">—</span>;
  }
  const color = RISK_COLOR[band as RiskBand] ?? "var(--color-text-tertiary)";
  const labelSize = size === "xs" ? "text-[11px]" : "text-[12px]";
  return (
    <span
      className={`inline-flex items-center gap-1.5 font-medium ${labelSize}`}
      style={{ color }}
    >
      <span
        aria-hidden="true"
        className="inline-block h-[6px] w-[6px] rounded-full"
        style={{ background: color }}
      />
      {humanizeRisk(band)}
    </span>
  );
}

const RISK_COLOR: Record<RiskBand, string> = {
  low: "var(--color-text-success)",
  med: "var(--color-text-warning)",
  high: "var(--color-text-danger)",
};
