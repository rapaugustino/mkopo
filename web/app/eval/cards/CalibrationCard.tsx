"use client";

/**
 * Calibration eval card — renders the reliability diagram + ECE + Brier.
 *
 * Visualises the output of ``mkopo/services/calibration.py``. The
 * underlying metrics:
 *
 * - **Reliability diagram** (Guo et al. ICML 2017): for each
 *   confidence bin, plot mean predicted confidence (x) vs empirical
 *   accuracy (y). Perfect calibration is the y=x diagonal. Points
 *   below the diagonal = the model is overconfident in that bin;
 *   above = underconfident.
 *
 * - **Expected Calibration Error (ECE)**: weighted gap between mean
 *   confidence and empirical accuracy across bins. Lower is better,
 *   0 = perfect.
 *
 * - **Brier score**: mean squared error between predicted probability
 *   and the {0,1} outcome. Strictly proper scoring rule. Lower is
 *   better. Useful as a tie-breaker because it penalises confident-
 *   and-wrong harder than ECE.
 *
 * Why both metrics: ECE is criticized for bin-boundary sensitivity
 * (you can game it with binning). Reporting both is the
 * regulator-defensible default — no single number is forced into
 * canonicality.
 */

import { useQuery } from "@tanstack/react-query";
import { IconGauge } from "@tabler/icons-react";
import {
  api,
  type CalibrationDetails,
  type TaskDetail,
} from "@/lib/api";
import { Pill } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";
import { Skeleton } from "@/app/components/Skeleton";
import { Tooltip } from "@/app/components/Tooltip";

const PCT = (v: number, digits = 1) => `${(v * 100).toFixed(digits)}%`;

interface ReliabilityDiagramProps {
  bins: CalibrationDetails["bins"];
}

/**
 * SVG reliability diagram. x-axis = mean confidence per bin, y-axis =
 * empirical accuracy. The y=x line is the "perfect calibration"
 * reference. Each bin is a bar whose width matches the bin's
 * confidence range; empty bins are skipped.
 */
function ReliabilityDiagram({ bins }: ReliabilityDiagramProps) {
  const W = 300;
  const H = 220;
  const PAD_L = 32;
  const PAD_R = 8;
  const PAD_T = 8;
  const PAD_B = 24;
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;

  const xFor = (v: number) => PAD_L + innerW * v;
  const yFor = (v: number) => PAD_T + innerH * (1 - v);

  // Bars: confidence range -> width on x-axis. Empty bins still get
  // a slot but no fill (so the x-axis stays uniform).
  const totalN = bins.reduce((s, b) => s + b.n, 0);

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      xmlns="http://www.w3.org/2000/svg"
      style={{ width: "100%", height: "auto", display: "block" }}
      aria-label="Reliability diagram: mean confidence vs empirical accuracy per bin, with y=x reference line"
    >
      {/* Gridlines + y-axis labels at 0%, 25%, 50%, 75%, 100%. */}
      {[0, 0.25, 0.5, 0.75, 1].map((y) => (
        <g key={y}>
          <line
            x1={PAD_L}
            y1={yFor(y)}
            x2={W - PAD_R}
            y2={yFor(y)}
            stroke="#888780"
            strokeOpacity={0.18}
            strokeWidth={0.5}
          />
          <text
            x={PAD_L - 5}
            y={yFor(y) + 3}
            textAnchor="end"
            fontSize={9}
            fill="var(--color-text-secondary)"
          >
            {Math.round(y * 100)}%
          </text>
        </g>
      ))}

      {/* y=x perfect-calibration reference line. Dashed so it reads
          as "ideal" not "data". */}
      <line
        x1={xFor(0)}
        y1={yFor(0)}
        x2={xFor(1)}
        y2={yFor(1)}
        stroke="var(--color-text-tertiary)"
        strokeWidth={1}
        strokeDasharray="3 3"
        opacity={0.7}
      />

      {/* Per-bin bars. Width = bin's confidence range; height = the
          empirical accuracy. Colour-code: overconfident (below the
          diagonal) is warning red, underconfident is blue, near-
          diagonal is success green. Empty bins get a faint outline
          so the operator can see them. */}
      {bins.map((b, i) => {
        if (b.n === 0) {
          return (
            <rect
              key={i}
              x={xFor(b.lower)}
              y={yFor(0.02)}
              width={Math.max(0, xFor(b.upper) - xFor(b.lower) - 1)}
              height={2}
              fill="var(--color-background-secondary)"
              opacity={0.6}
            />
          );
        }
        const gap = b.empirical_accuracy - b.mean_confidence;
        // Tolerance band ±5pp around the diagonal counts as
        // "calibrated". Outside: overconfident (acc < conf) flagged
        // red; underconfident (acc > conf) flagged amber/info.
        const colour =
          Math.abs(gap) < 0.05
            ? "var(--color-text-success)"
            : gap < 0
              ? "var(--color-text-danger)"
              : "var(--color-text-warning)";
        const barX = xFor(b.lower);
        const barY = yFor(b.empirical_accuracy);
        const barW = Math.max(0, xFor(b.upper) - xFor(b.lower) - 1);
        const barH = Math.max(0, yFor(0) - barY);
        return (
          <g key={i}>
            <rect
              x={barX}
              y={barY}
              width={barW}
              height={barH}
              fill={colour}
              opacity={0.55}
            >
              <title>
                {`Bin [${(b.lower * 100).toFixed(0)}-${(b.upper * 100).toFixed(0)}%]
n=${b.n} · ${((b.n / totalN) * 100).toFixed(1)}% of data
mean conf=${(b.mean_confidence * 100).toFixed(1)}%
empirical acc=${(b.empirical_accuracy * 100).toFixed(1)}%
gap=${gap >= 0 ? "+" : ""}${(gap * 100).toFixed(1)}pp`}
              </title>
            </rect>
            {/* A small marker at (mean_confidence, empirical_accuracy)
                so the operator can see exactly where the bin lands
                relative to the diagonal — the bar's left edge isn't
                the right reference point. */}
            <circle
              cx={xFor(b.mean_confidence)}
              cy={yFor(b.empirical_accuracy)}
              r={2.5}
              fill="var(--color-text-primary)"
            />
          </g>
        );
      })}

      {/* x-axis labels: 0%, 50%, 100%. */}
      {[0, 0.5, 1].map((x) => (
        <text
          key={x}
          x={xFor(x)}
          y={H - 8}
          textAnchor="middle"
          fontSize={9}
          fill="var(--color-text-secondary)"
        >
          {Math.round(x * 100)}%
        </text>
      ))}
      {/* Axis labels */}
      <text
        x={PAD_L + innerW / 2}
        y={H - 1}
        textAnchor="middle"
        fontSize={9}
        fill="var(--color-text-tertiary)"
      >
        predicted confidence
      </text>
    </svg>
  );
}

export function CalibrationCard() {
  const query = useQuery<TaskDetail, Error>({
    queryKey: ["eval-task-detail", "calibration.extractor_confidence"],
    queryFn: () =>
      api.getTaskDetail("calibration.extractor_confidence"),
    refetchInterval: 60_000,
  });

  if (query.isLoading) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <Skeleton className="mb-2 h-3 w-32" />
        <Skeleton className="h-[200px]" />
      </div>
    );
  }
  if (!query.data?.found || !query.data.details) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <SectionLabel Icon={IconGauge}>
          Confidence calibration (ECE + Brier)
        </SectionLabel>
        <p className="mt-2 text-[12px] text-[var(--color-text-tertiary)]">
          No calibration data yet. The monitor runs at 3:30 AM UTC on
          accepted/overridden extractions. Need at least one resolved
          extraction with a non-null confidence to populate.
        </p>
      </div>
    );
  }

  const d = query.data.details as unknown as CalibrationDetails;
  const ranAt = query.data.ran_at
    ? new Date(query.data.ran_at).toLocaleString()
    : "—";

  // ECE quality rubric:
  //  - < 0.05  = well-calibrated (success)
  //  - < 0.10  = acceptable (info)
  //  - >= 0.10 = miscalibrated (warn / danger)
  const eceVariant =
    d.ece < 0.05 ? "success" : d.ece < 0.1 ? "info" : "warn";
  const brierVariant =
    d.brier < 0.1 ? "success" : d.brier < 0.2 ? "info" : "warn";

  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <SectionLabel Icon={IconGauge} dense>
          Confidence calibration
        </SectionLabel>
        <span className="text-[11px] text-[var(--color-text-tertiary)]">
          n={query.data.n} · window={d.window_days}d · {ranAt}
        </span>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2 text-[11.5px]">
        <Tooltip
          content="Expected Calibration Error — weighted gap between mean predicted confidence and empirical accuracy per bin (Guo et al. 2017, https://arxiv.org/abs/1706.04599). Lower is better. <5% well-calibrated; 5–10% acceptable; >10% miscalibrated."
          underline
        >
          <span className="text-[var(--color-text-secondary)]">ECE</span>
        </Tooltip>
        <Pill variant={eceVariant}>{PCT(d.ece, 2)}</Pill>
        <span className="text-[var(--color-text-tertiary)]">·</span>
        <Tooltip
          content="Brier score — mean squared error between predicted probability and the {0,1} outcome. Strictly proper scoring rule; in [0, 1] where 0 = perfect. Penalises confident-and-wrong harder than ECE, so use it as the tie-breaker."
          underline
        >
          <span className="text-[var(--color-text-secondary)]">Brier</span>
        </Tooltip>
        <Pill variant={brierVariant}>{d.brier.toFixed(3)}</Pill>
      </div>

      <div className="flex flex-col gap-3 lg:flex-row lg:items-start">
        <div className="lg:flex-1">
          <ReliabilityDiagram bins={d.bins} />
        </div>

        {/* Legend / interpretive guide. The diagram speaks for itself
            once the operator knows what to look for, but the first
            time they see it the colour code needs an explainer. */}
        <div className="flex flex-col gap-1.5 text-[11px] text-[var(--color-text-secondary)] lg:w-[180px]">
          <p className="text-[10px] font-medium uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
            How to read
          </p>
          <p>
            Each bar is a confidence bin; the dot marks (mean conf,
            empirical accuracy). The dashed diagonal is perfect
            calibration.
          </p>
          <div className="mt-1 flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-2.5 rounded-sm"
              style={{ background: "var(--color-text-success)", opacity: 0.55 }}
            />
            <span>within ±5pp (calibrated)</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-2.5 rounded-sm"
              style={{ background: "var(--color-text-danger)", opacity: 0.55 }}
            />
            <span>below diagonal (overconfident)</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-2.5 rounded-sm"
              style={{ background: "var(--color-text-warning)", opacity: 0.55 }}
            />
            <span>above diagonal (underconfident)</span>
          </div>
        </div>
      </div>
    </div>
  );
}
