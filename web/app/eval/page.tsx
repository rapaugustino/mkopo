"use client";

import { useMemo } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { IconChartLine, IconListSearch, IconRefresh } from "@tabler/icons-react";
import { toast } from "sonner";
import {
  api,
  type EvalFieldRow,
  type EvalSummary,
  type EvalTrend,
} from "@/lib/api";
import { humanizeField } from "@/lib/humanize";
import { BrandHeader } from "@/app/components/BrandHeader";
import { Pill } from "@/app/components/Pill";
import { PrimaryButton } from "@/app/components/PrimaryButton";
import { StatTile } from "@/app/components/StatTile";

const PCT = (v: number | null | undefined, digits = 1) =>
  v == null ? "—" : `${(v * 100).toFixed(digits)}%`;

/** The eval task name is ``extraction.<field>``; ``humanizeField``
 *  strips the prefix and maps known fields to canonical capitalisation. */
const labelFor = humanizeField;

function colourForAccuracy(accuracy: number | null): string {
  if (accuracy == null) return "var(--color-text-secondary)";
  if (accuracy >= 0.92) return "var(--color-text-success)";
  if (accuracy >= 0.85) return "var(--color-text-warning)";
  return "var(--color-text-danger)";
}

/**
 * SVG line chart for the weekly trend. Aggregates points client-side
 * into one polyline per (task_name, source). We render production solid
 * and golden dashed so the eye can read drift instantly.
 *
 * Coordinates are sized to a fixed viewBox (600x150) matching the mockup;
 * the parent makes it fluid via width:100%.
 */
function TrendChart({ trend }: { trend: EvalTrend }) {
  const W = 600;
  const H = 150;
  const PAD_L = 36;
  const PAD_R = 12;
  const PAD_T = 12;
  const PAD_B = 26;
  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;

  // y-domain: 0.75..1.00 covers the relevant range without losing detail.
  // Anything below 75% gets clamped — drift at that depth is "alert
  // loudly" territory, not "interpolate a curve" territory.
  const Y_MIN = 0.75;
  const Y_MAX = 1.0;
  const yFor = (acc: number) =>
    PAD_T + innerH * (1 - (Math.max(Y_MIN, Math.min(Y_MAX, acc)) - Y_MIN) / (Y_MAX - Y_MIN));

  if (trend.points.length === 0) {
    return (
      <p className="px-2 py-8 text-center text-xs text-[var(--color-text-secondary)]">
        No eval runs yet in the last {trend.days} days. Trigger the drift
        monitor below to populate.
      </p>
    );
  }

  // x-domain: oldest..newest, parsed timestamps
  const ts = trend.points.map((p) => new Date(p.created_at).getTime());
  const tMin = Math.min(...ts);
  const tMax = Math.max(...ts);
  const xFor = (t: number) =>
    tMax === tMin ? PAD_L + innerW / 2 : PAD_L + innerW * ((t - tMin) / (tMax - tMin));

  // group: task_name -> source -> sorted [{t, accuracy}]
  type Pt = { t: number; accuracy: number };
  const grouped = new Map<string, Map<string, Pt[]>>();
  for (const p of trend.points) {
    const series = grouped.get(p.task_name) ?? new Map<string, Pt[]>();
    const pts = series.get(p.source) ?? [];
    pts.push({ t: new Date(p.created_at).getTime(), accuracy: p.accuracy });
    series.set(p.source, pts);
    grouped.set(p.task_name, series);
  }
  for (const series of grouped.values()) {
    for (const arr of series.values()) {
      arr.sort((a, b) => a.t - b.t);
    }
  }

  // Stable palette across re-renders: hash task_name → hue.
  const colourOf = (taskName: string): string => {
    let h = 0;
    for (let i = 0; i < taskName.length; i++) h = (h * 31 + taskName.charCodeAt(i)) >>> 0;
    return `hsl(${h % 360} 55% 42%)`;
  };

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      xmlns="http://www.w3.org/2000/svg"
      style={{ width: "100%", height: "auto", display: "block" }}
      aria-label="Weekly extraction accuracy trend per field, with production and golden lines"
    >
      {/* Y-axis gridlines + labels at 75% / 88% / 100% */}
      {[1.0, 0.875, 0.75].map((y) => (
        <g key={y}>
          <line
            x1={PAD_L}
            y1={yFor(y)}
            x2={W - PAD_R}
            y2={yFor(y)}
            stroke="#888780"
            strokeOpacity={0.25}
            strokeWidth={0.5}
          />
          <text
            x={PAD_L - 6}
            y={yFor(y) + 3}
            textAnchor="end"
            fontSize={10}
            fill="var(--color-text-secondary)"
          >
            {Math.round(y * 100)}%
          </text>
        </g>
      ))}

      {/* One polyline per (task_name, source). Production solid; golden dashed. */}
      {[...grouped.entries()].flatMap(([taskName, series]) =>
        [...series.entries()].map(([source, pts]) => {
          const colour = colourOf(taskName);
          const path = pts.map((p) => `${xFor(p.t).toFixed(1)},${yFor(p.accuracy).toFixed(1)}`).join(" ");
          return (
            <g key={`${taskName}-${source}`}>
              <polyline
                points={path}
                fill="none"
                stroke={colour}
                strokeWidth={1.5}
                strokeDasharray={source === "golden" ? "4 3" : undefined}
                opacity={0.85}
              />
              {pts.map((p, i) => (
                <circle
                  key={i}
                  cx={xFor(p.t)}
                  cy={yFor(p.accuracy)}
                  r={2.5}
                  fill={colour}
                >
                  <title>
                    {labelFor(taskName)} · {source} · {PCT(p.accuracy, 1)} ·{" "}
                    {new Date(p.t).toLocaleDateString()}
                  </title>
                </circle>
              ))}
            </g>
          );
        }),
      )}

      {/* x-axis labels: oldest / midpoint / newest */}
      {[tMin, (tMin + tMax) / 2, tMax].map((t, i) => (
        <text
          key={i}
          x={xFor(t)}
          y={H - 6}
          textAnchor="middle"
          fontSize={10}
          fill="var(--color-text-secondary)"
        >
          {new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
        </text>
      ))}
    </svg>
  );
}

function FieldBar({ row }: { row: EvalFieldRow }) {
  const acc = row.production_accuracy;
  const bar = colourForAccuracy(acc);
  const width = acc == null ? 0 : Math.max(2, Math.round(acc * 100));
  // The eval task_name is shaped ``extraction.<field>`` so the review
  // queue can filter by the bare field name. Strip the prefix.
  const bareField = row.field_name.replace(/^extraction\./, "");
  // Drifting = production materially below golden. We surface a
  // direct "Investigate" link to the filtered review queue when
  // that's the case, so the operator can see the actual extractions
  // that contributed to the drop instead of staring at a number.
  const isDrifting = row.delta != null && row.delta <= -0.03;
  return (
    <div className="flex items-center gap-3 text-xs">
      <span
        className="w-[140px] truncate text-[var(--color-text-secondary)]"
        title={labelFor(row.field_name)}
      >
        {labelFor(row.field_name)}
      </span>
      <div className="h-2 flex-1 overflow-hidden rounded bg-[var(--color-background-secondary)]">
        <div
          className="h-full rounded"
          style={{ width: `${width}%`, background: bar }}
        />
      </div>
      <span className="w-[44px] text-right font-medium">{PCT(acc)}</span>
      {/* Sample size — flagged in the audit as missing from the per-
          field row. Now visible inline so an operator can tell
          "92% on 4 items" (noise) from "92% on 800 items" (signal). */}
      <span
        className="w-[56px] text-right text-[11px] tabular-nums text-[var(--color-text-tertiary)]"
        title="Production sample size (last accepted/overridden extractions)"
      >
        {row.production_n != null ? `n=${row.production_n}` : "—"}
      </span>
      {row.delta != null && (
        <span
          className="w-[80px] text-right text-[11px]"
          style={{
            color:
              row.delta <= -0.03
                ? "var(--color-text-danger)"
                : row.delta < 0
                  ? "var(--color-text-warning)"
                  : "var(--color-text-success)",
          }}
        >
          {row.delta >= 0 ? "+" : ""}
          {(row.delta * 100).toFixed(1)}pp vs golden
        </span>
      )}
      {/* "Investigate" deep-link. Only renders on drifting rows so it
          doesn't add visual noise to the healthy ones. Lands the
          operator in the review queue with the field filter
          pre-applied — closes the loop between "field is drifting"
          and "here are the specific extractions to look at". */}
      <span className="w-[110px] text-right">
        {isDrifting ? (
          <Link
            href={`/review-queue?field=${encodeURIComponent(bareField)}`}
            className="inline-flex items-center gap-1 text-[11px] text-[var(--color-text-info)] hover:underline"
          >
            <IconListSearch size={11} />
            Investigate
          </Link>
        ) : null}
      </span>
    </div>
  );
}

export default function EvalDashboardPage() {
  const queryClient = useQueryClient();

  const summaryQuery = useQuery<EvalSummary, Error>({
    queryKey: ["eval-summary"],
    queryFn: () => api.getEvalSummary(),
  });

  const fieldsQuery = useQuery<EvalFieldRow[], Error>({
    queryKey: ["eval-fields"],
    queryFn: () => api.getEvalFields(),
  });

  const trendQuery = useQuery<EvalTrend, Error>({
    queryKey: ["eval-trend", 30],
    queryFn: () => api.getEvalTrend(30),
  });

  const refresh = useMutation({
    mutationFn: () => api.refreshDrift(),
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["eval-summary"] }),
        queryClient.invalidateQueries({ queryKey: ["eval-fields"] }),
        queryClient.invalidateQueries({ queryKey: ["eval-trend", 30] }),
      ]);
      toast.success("Drift monitor ran", {
        description: `Wrote ${result.fields_written} production field${result.fields_written === 1 ? "" : "s"}.`,
      });
    },
    onError: (e) =>
      toast.error("Drift monitor failed", {
        description: e instanceof Error ? e.message : String(e),
      }),
  });

  const summary = summaryQuery.data;
  const fields = fieldsQuery.data ?? [];
  const trend = trendQuery.data ?? { days: 30, points: [] };

  const driftBadge = useMemo(() => {
    if (!summary || summary.fields_drifting === 0) return null;
    return (
      <Pill variant="danger">
        {summary.fields_drifting} field{summary.fields_drifting === 1 ? "" : "s"} drifting
      </Pill>
    );
  }, [summary]);

  if (summaryQuery.isPending) {
    return <p className="text-sm text-[var(--color-text-secondary)]">Loading eval data…</p>;
  }
  if (summaryQuery.error) {
    return (
      <p className="text-sm text-[var(--color-text-danger)]">
        Couldn&apos;t load eval data: {summaryQuery.error.message}
      </p>
    );
  }
  if (!summary) return null;

  const accColour = colourForAccuracy(summary.overall_production_accuracy);
  const deltaColour =
    summary.overall_delta == null
      ? undefined
      : summary.overall_delta <= -0.03
        ? "var(--color-text-danger)"
        : summary.overall_delta < 0
          ? "var(--color-text-warning)"
          : "var(--color-text-success)";

  return (
    <div className="flex flex-col gap-3">
      <BrandHeader
        title="Evaluation"
        sub="Extraction accuracy vs golden baseline · LLM call health · drift alert"
        badge={driftBadge}
        actions={
          <PrimaryButton
            Icon={IconRefresh}
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending}
          >
            {refresh.isPending ? "Refreshing…" : "Refresh drift"}
          </PrimaryButton>
        }
      />

      {/* Success / failure feedback for the manual refresh goes through
          the toast system now — see Providers. The inline banners that
          used to live here duplicated that signal. */}

      <div className="grid grid-cols-4 gap-2">
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          <StatTile
            label="Production accuracy"
            value={PCT(summary.overall_production_accuracy)}
            trend={
              summary.overall_delta != null
                ? `${summary.overall_delta >= 0 ? "+" : ""}${(summary.overall_delta * 100).toFixed(1)}pp vs golden`
                : "no baseline"
            }
            trendColor={deltaColour}
          />
          <div
            className="h-[2px]"
            style={{ background: accColour, opacity: 0.6 }}
          />
        </div>
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          <StatTile
            label="Golden baseline"
            value={PCT(summary.overall_golden_accuracy)}
            trend={`${summary.fields_tracked} field${summary.fields_tracked === 1 ? "" : "s"} tracked`}
          />
        </div>
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          <StatTile
            label="LLM p95 latency"
            value={
              summary.llm_p95_latency_seconds == null
                ? "—"
                : `${summary.llm_p95_latency_seconds.toFixed(2)}s`
            }
            trend={`${summary.llm_calls_24h} calls / 24h`}
          />
        </div>
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          <StatTile
            label="LLM error rate"
            value={PCT(summary.llm_error_rate_24h)}
            trend="last 24h"
            trendColor={
              (summary.llm_error_rate_24h ?? 0) > 0.05
                ? "var(--color-text-danger)"
                : undefined
            }
          />
        </div>
      </div>

      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <div className="mb-3 flex items-center justify-between gap-3">
          <p className="flex items-center gap-1.5 text-[13px] font-medium">
            <IconChartLine size={14} />
            Accuracy trend (last {trend.days} days)
          </p>
          <span className="flex items-center gap-3 text-[11px] text-[var(--color-text-secondary)]">
            <span className="flex items-center gap-1">
              <span
                className="inline-block h-[2px] w-3"
                style={{ background: "var(--color-text-primary)" }}
              />
              production
            </span>
            <span className="flex items-center gap-1">
              <span
                className="inline-block h-[2px] w-3 border-dashed"
                style={{
                  background:
                    "repeating-linear-gradient(to right, var(--color-text-primary) 0 3px, transparent 3px 5px)",
                }}
              />
              golden
            </span>
          </span>
        </div>
        <TrendChart trend={trend} />
      </div>

      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <p className="mb-3 text-[13px] font-medium">Per-field accuracy (most recent run)</p>
        {fields.length === 0 ? (
          <p className="text-xs text-[var(--color-text-secondary)]">
            No fields evaluated yet. Run intake on the seed loans, accept
            or override a few extractions in the review queue, then click
            <em> Refresh drift</em>.
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            {fields.map((row) => (
              <FieldBar key={row.field_name} row={row} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
