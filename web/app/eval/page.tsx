"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  IconActivity,
  IconAlertOctagon,
  IconArrowRight,
  IconChartLine,
  IconGauge,
  IconListSearch,
  IconRefresh,
  IconRobot,
} from "@tabler/icons-react";
import { toast } from "sonner";
import {
  api,
  type EvalAgentReliabilityRow,
  type EvalConfidenceBucket,
  type EvalDiagnostics,
  type EvalFailureRow,
  type EvalFieldRow,
  type EvalSummary,
  type EvalTrend,
} from "@/lib/api";
import { titleCase, humanizeField } from "@/lib/humanize";
import { AgentRunDrawer } from "@/app/observability/AgentRunDrawer";
import { LLMCallDrawer } from "@/app/observability/LLMCallDrawer";
import { BrandHeader } from "@/app/components/BrandHeader";
import { Pill } from "@/app/components/Pill";
import { PrimaryButton } from "@/app/components/PrimaryButton";
import { StatTile } from "@/app/components/StatTile";
import { Tooltip } from "@/app/components/Tooltip";
import { DecisionVerdictCard } from "./cards/DecisionVerdictCard";
import { AALFidelityCard } from "./cards/AALFidelityCard";
import { CalibrationCard } from "./cards/CalibrationCard";
import { AdversarialInjectionCard } from "./cards/AdversarialInjectionCard";

/** Tooltip definitions for the eval dashboard. Centralised so the
 *  wording stays consistent across cards + so a regulator-friendly
 *  language pass touches one file. Each definition names what's
 *  measured + how it's computed + where the number comes from. */
const EVAL_TOOLTIP: Record<string, string> = {
  "Production accuracy":
    "Unweighted mean accuracy across tracked extraction fields, computed from staff overrides in the review queue. A 'production' task_run is written by services/drift.py:run_drift_monitor each time the drift sweep runs (manually via Refresh, or on the scheduled sweep). Compare to Golden baseline to spot drift.",
  "Golden baseline":
    "Unweighted mean accuracy on the labelled YAML golden sets in api/evals/golden_sets/. A 'golden' task_run is written by the CLI eval runner (cd api && uv run python -m evals.runner) and by the periodic golden sweep. This is the reference the model is supposed to hit.",
  "LLM p95 latency":
    "95th percentile of LLM call duration in the last 24h. p95 (not p50) because tail latency is what users feel — a fast median with a slow p95 is still a bad experience.",
  "LLM error rate":
    "Fraction of llm_calls rows with status != 'ok' in the last 24h (schema validation failures, provider errors, timeouts). >5% trips the trend pill to red.",
  Open:
    "Review-queue items in 'open' status. Created when an extraction's confidence falls below its per-field threshold (see services/extractor.threshold_for).",
  "Resolved 7d":
    "Review-queue items closed (accepted or overridden) in the last 7 days. Healthy systems show steady throughput; a stalled count means human review is the bottleneck.",
  "Median age":
    "Median time-to-resolve for items closed in the last 7 days. The SLO band on the trend pill is set by team policy.",
};

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

  // Open-drawer state for the "Recent failures" deep-links. The eval
  // page reuses the observability drawers so an operator can drill
  // from "X failed" all the way to the prompt hash + structured
  // payload without leaving the eval workflow.
  const [openCallId, setOpenCallId] = useState<string | null>(null);
  const [openRunId, setOpenRunId] = useState<string | null>(null);

  // Top-level metrics refetch every 60s so the dashboard reflects
  // both manual Refresh-button clicks AND the scheduled jobs
  // (drift_monitor at 3am UTC + golden_eval_sweep at 4am UTC, see
  // workers/tasks.py). Previously these only refreshed on mount /
  // window focus — a staff user keeping the tab open wouldn't see
  // numbers move. 60s is the sweet spot: fresh enough to feel live,
  // slow enough that paginated bots don't notice us.
  const summaryQuery = useQuery<EvalSummary, Error>({
    queryKey: ["eval-summary"],
    queryFn: () => api.getEvalSummary(),
    refetchInterval: 60_000,
  });

  const fieldsQuery = useQuery<EvalFieldRow[], Error>({
    queryKey: ["eval-fields"],
    queryFn: () => api.getEvalFields(),
    refetchInterval: 60_000,
  });

  const trendQuery = useQuery<EvalTrend, Error>({
    queryKey: ["eval-trend", 30],
    queryFn: () => api.getEvalTrend(30),
    refetchInterval: 60_000,
  });

  const diagnosticsQuery = useQuery<EvalDiagnostics, Error>({
    queryKey: ["eval-diagnostics"],
    queryFn: () => api.getEvalDiagnostics(),
    // Cheaper than summary (no aggregation across task_runs); poll a
    // little faster so the "recent failures" list feels live during
    // an agent run that's actively producing them.
    refetchInterval: 20_000,
  });

  const refresh = useMutation({
    mutationFn: () => api.refreshDrift(),
    onSuccess: async (result) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["eval-summary"] }),
        queryClient.invalidateQueries({ queryKey: ["eval-fields"] }),
        queryClient.invalidateQueries({ queryKey: ["eval-trend", 30] }),
        queryClient.invalidateQueries({ queryKey: ["eval-diagnostics"] }),
      ]);
      if (result.fields_written === 0) {
        // Most common failure mode in a fresh demo: the user clicks
        // Refresh but there are <5 resolved extractions, so the drift
        // monitor skips every field. Be explicit about why.
        toast.info("Drift monitor ran — no fields written", {
          description:
            "Need at least 5 accepted/overridden extractions per field. Run intake on a loan, then accept or override a few in the review queue.",
          duration: 8_000,
        });
      } else {
        toast.success("Drift monitor ran", {
          description: `Wrote ${result.fields_written} production field${result.fields_written === 1 ? "" : "s"}.`,
        });
      }
    },
    onError: (e) =>
      toast.error("Drift monitor failed", {
        description: e instanceof Error ? e.message : String(e),
      }),
  });

  const summary = summaryQuery.data;
  const fields = fieldsQuery.data ?? [];
  const trend = trendQuery.data ?? { days: 30, points: [] };
  const diagnostics = diagnosticsQuery.data;
  // Width of the LLM "trend" line on the tiles depends on which
  // window the backend actually carried the stats from. "Last 24h" is
  // the happy path; "Last 7 days" / "All-time" means demo install with
  // no recent traffic — we want the operator to see that distinction
  // at a glance rather than puzzling at the "—".
  const windowLabel = summary?.llm_window_label ?? "24h";
  const windowReadable =
    windowLabel === "24h"
      ? "last 24h"
      : windowLabel === "7d"
        ? "last 7d"
        : "all-time";

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

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          <StatTile
            label={
              <Tooltip content={EVAL_TOOLTIP["Production accuracy"]} underline>
                Production accuracy
              </Tooltip>
            }
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
            label={
              <Tooltip content={EVAL_TOOLTIP["Golden baseline"]} underline>
                Golden baseline
              </Tooltip>
            }
            value={PCT(summary.overall_golden_accuracy)}
            trend={`${summary.fields_tracked} field${summary.fields_tracked === 1 ? "" : "s"} tracked`}
          />
        </div>
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          <StatTile
            label={
              <Tooltip content={EVAL_TOOLTIP["LLM p95 latency"]} underline>
                LLM p95 latency
              </Tooltip>
            }
            value={
              summary.llm_p95_latency_seconds == null
                ? "—"
                : `${summary.llm_p95_latency_seconds.toFixed(2)}s`
            }
            trend={`${summary.llm_calls_24h} calls · ${windowReadable}`}
          />
        </div>
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          <StatTile
            label={
              <Tooltip content={EVAL_TOOLTIP["LLM error rate"]} underline>
                LLM error rate
              </Tooltip>
            }
            value={PCT(summary.llm_error_rate_24h)}
            trend={windowReadable}
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

      {/* Phase 2 — task-specific eval cards. Each one renders the
          ``details`` JSONB written by an AggregatingEvalTask in
          ``evals/runner.py`` (decision verdict, AAL fidelity,
          adversarial injection) or by the calibration monitor in
          ``services/calibration.py``. Same shape on the wire (GET
          /eval/task-detail/{task_name}); each card narrows the
          payload to its own typed view.

          Why a separate section: the top metrics + trend + per-field
          rows above answer "are extractions still accurate?". These
          four answer the harder regulator questions — "in what
          direction are decisions wrong?" (confusion matrix), "is
          the AAL drafter ECOA-compliant?" (per-criterion fidelity),
          "is confidence well-calibrated?" (ECE + Brier), "do we
          catch known injection attacks?" (per-pattern coverage).
          Each of those needs a different visual; one row of bars
          can't carry the load. */}
      <div className="grid grid-cols-1 gap-2 xl:grid-cols-2">
        <DecisionVerdictCard />
        <AALFidelityCard />
        <CalibrationCard />
        <AdversarialInjectionCard />
      </div>

      {/* Diagnostics row — confidence calibration + review queue +
          agent reliability + recent failures. The eval page is about
          "is the AI doing the job well", and these four cards each
          answer a different facet of that question without
          duplicating the observability page (which answers "is the
          system healthy"). */}
      <div className="grid grid-cols-2 gap-2">
        <ConfidenceCard diagnostics={diagnostics} />
        <ReviewQueueCard diagnostics={diagnostics} />
      </div>

      <AgentReliabilityCard diagnostics={diagnostics} />

      <RecentFailuresCard
        diagnostics={diagnostics}
        onOpenCall={setOpenCallId}
        onOpenRun={setOpenRunId}
      />

      {/* Reuse the observability drawers — drilling into a failure
          from here lands the operator in the same prompt-hash / step
          payload view they'd get from /observability. */}
      <AgentRunDrawer
        runId={openRunId}
        onClose={() => setOpenRunId(null)}
        onOpenLLMCall={(id) => {
          setOpenRunId(null);
          setOpenCallId(id);
        }}
      />
      <LLMCallDrawer callId={openCallId} onClose={() => setOpenCallId(null)} />
    </div>
  );
}


// ---- Diagnostics cards ---------------------------------------------------


/**
 * Confidence calibration — accept rate per confidence band.
 *
 * The extractor reports a confidence per field; the auto-accept
 * threshold treats anything ≥0.85 as "good enough" to skip the
 * review queue. The story this card tells is whether that threshold
 * holds: in a calibrated extractor, the ≥0.95 band should approach
 * 100% accept-rate and the lower bands should slope down. If the
 * top band is well below 100% the model is over-confident and the
 * threshold needs raising.
 */
function ConfidenceCard({ diagnostics }: { diagnostics: EvalDiagnostics | undefined }) {
  const buckets = diagnostics?.confidence_buckets ?? [];
  const total = buckets.reduce((s, b) => s + b.n, 0);
  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
      <p className="mb-1 flex items-center gap-1.5 text-[13px] font-medium">
        <IconGauge size={14} />
        Confidence calibration
      </p>
      <p className="mb-3 text-[11.5px] text-[var(--color-text-secondary)]">
        Accept rate per confidence band over {total.toLocaleString()} resolved
        extractions. Well-calibrated = ≥0.95 band near 100%.
      </p>
      {total === 0 ? (
        <p className="text-[12px] text-[var(--color-text-secondary)]">
          No resolved extractions yet. Calibration becomes meaningful once
          intake has run on at least one loan + a few extractions have been
          accepted or overridden.
        </p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {buckets.map((b) => (
            <ConfidenceRow key={b.label} bucket={b} />
          ))}
        </div>
      )}
    </div>
  );
}

function ConfidenceRow({ bucket }: { bucket: EvalConfidenceBucket }) {
  const rate = bucket.n === 0 ? null : bucket.accepted / bucket.n;
  const bar = rate == null
    ? "var(--color-background-secondary)"
    : rate >= 0.92
      ? "var(--color-text-success)"
      : rate >= 0.75
        ? "var(--color-text-warning)"
        : "var(--color-text-danger)";
  const width = rate == null ? 0 : Math.max(2, Math.round(rate * 100));
  return (
    <div className="flex items-center gap-2 text-[11.5px]">
      <span className="w-[78px] text-[var(--color-text-secondary)] tabular-nums">
        {bucket.label}
      </span>
      <div className="h-1.5 flex-1 overflow-hidden rounded bg-[var(--color-background-secondary)]">
        <div
          className="h-full rounded"
          style={{ width: `${width}%`, background: bar }}
        />
      </div>
      <span className="w-[46px] text-right font-medium tabular-nums">
        {PCT(rate, 0)}
      </span>
      <span className="w-[52px] text-right text-[11px] text-[var(--color-text-tertiary)] tabular-nums">
        n={bucket.n}
      </span>
    </div>
  );
}


/**
 * Review queue burn-down. Three numbers: open count, last-7-day
 * resolution count, median age of open items. The relationship
 * between them tells the operator whether reviewers are keeping
 * pace with intake.
 */
function ReviewQueueCard({ diagnostics }: { diagnostics: EvalDiagnostics | undefined }) {
  const stats = diagnostics?.review_queue;
  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
      <p className="mb-1 flex items-center gap-1.5 text-[13px] font-medium">
        <IconListSearch size={14} />
        Review queue throughput
      </p>
      <p className="mb-3 text-[11.5px] text-[var(--color-text-secondary)]">
        Low-confidence extractions queued for human review.
      </p>
      <div className="grid grid-cols-3 gap-2">
        <Counter
          label="Open"
          value={stats?.open}
          tone={
            (stats?.open ?? 0) > 25 ? "warning" : "default"
          }
        />
        <Counter
          label="Resolved 7d"
          value={stats?.resolved_7d}
        />
        <Counter
          label="Median age"
          value={
            stats?.median_open_age_hours == null
              ? null
              : `${stats.median_open_age_hours.toFixed(1)}h`
          }
        />
      </div>
      {stats && stats.open > 0 && (
        <Link
          href="/review-queue"
          className="mt-3 inline-flex items-center gap-1 text-[11.5px] text-[var(--color-text-info)] hover:underline"
        >
          Open review queue
          <IconArrowRight size={11} />
        </Link>
      )}
    </div>
  );
}

function Counter({
  label,
  value,
  tone,
}: {
  label: string;
  value: number | string | null | undefined;
  tone?: "default" | "warning";
}) {
  const colour = tone === "warning" ? "var(--color-text-warning)" : "var(--color-text-primary)";
  const display = value == null ? "—" : typeof value === "number" ? value.toLocaleString() : value;
  return (
    <div className="rounded-md bg-[var(--color-background-secondary)] px-2.5 py-2">
      <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-secondary)]">
        {label}
      </p>
      <p
        className="font-editorial mt-0.5 text-[18px] leading-none tabular-nums"
        style={{ color: colour }}
      >
        {display}
      </p>
    </div>
  );
}


/**
 * Agent reliability — per-agent run counts and outcomes over the
 * last 7 days. The "worst step wins" status per run means a single
 * failed step downgrades the whole run.
 */
function AgentReliabilityCard({ diagnostics }: { diagnostics: EvalDiagnostics | undefined }) {
  const rows = diagnostics?.agent_reliability ?? [];
  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
      <p className="mb-1 flex items-center gap-1.5 text-[13px] font-medium">
        <IconRobot size={14} />
        Agent reliability (last 7 days)
      </p>
      <p className="mb-3 text-[11.5px] text-[var(--color-text-secondary)]">
        Per-agent run outcomes. <em>Interrupted</em> counts HITL pauses;
        a real failure shows up as <em>failed</em>.
      </p>
      {rows.length === 0 ? (
        <p className="text-[12px] text-[var(--color-text-secondary)]">
          No agent runs in the last 7 days.
        </p>
      ) : (
        <div className="flex flex-col">
          {rows.map((r) => (
            <AgentReliabilityBar key={r.agent_name} row={r} />
          ))}
        </div>
      )}
    </div>
  );
}

function AgentReliabilityBar({ row }: { row: EvalAgentReliabilityRow }) {
  const okPct = row.runs === 0 ? 0 : (row.ok / row.runs) * 100;
  const intPct = row.runs === 0 ? 0 : (row.interrupted / row.runs) * 100;
  const failPct = row.runs === 0 ? 0 : (row.failed / row.runs) * 100;
  return (
    <div className="flex items-center gap-3 border-t-[0.5px] border-[var(--color-border-tertiary)] py-2 text-[12px] first:border-t-0">
      <span className="w-[140px] truncate font-medium">
        {titleCase(row.agent_name)}
      </span>
      <div className="flex h-2 flex-1 overflow-hidden rounded bg-[var(--color-background-secondary)]">
        {okPct > 0 && (
          <div
            style={{ width: `${okPct}%`, background: "var(--color-text-success)" }}
            title={`${row.ok} ok`}
          />
        )}
        {intPct > 0 && (
          <div
            style={{ width: `${intPct}%`, background: "var(--color-text-warning)" }}
            title={`${row.interrupted} interrupted (HITL pause)`}
          />
        )}
        {failPct > 0 && (
          <div
            style={{ width: `${failPct}%`, background: "var(--color-text-danger)" }}
            title={`${row.failed} failed`}
          />
        )}
      </div>
      <span className="w-[60px] text-right tabular-nums text-[11.5px]">
        {row.runs} run{row.runs === 1 ? "" : "s"}
      </span>
      <span className="w-[150px] text-right text-[11px] text-[var(--color-text-secondary)] tabular-nums">
        {row.ok}/{row.interrupted}/{row.failed}
        <span className="ml-1 text-[10px] uppercase tracking-wider opacity-70">
          ok/int/fail
        </span>
      </span>
    </div>
  );
}


/**
 * Recent failures — the actionable list. Each row links into the
 * existing observability drawer for that LLM call or agent run, so
 * the operator can see the prompt hash + structured payload that
 * caused the failure.
 */
function RecentFailuresCard({
  diagnostics,
  onOpenCall,
  onOpenRun,
}: {
  diagnostics: EvalDiagnostics | undefined;
  onOpenCall: (id: string) => void;
  onOpenRun: (id: string) => void;
}) {
  const rows = diagnostics?.recent_failures ?? [];
  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
      <p className="mb-1 flex items-center gap-1.5 text-[13px] font-medium">
        <IconAlertOctagon size={14} />
        Recent failures
        {rows.length > 0 && (
          <Pill variant="danger">{rows.length}</Pill>
        )}
      </p>
      <p className="mb-3 text-[11.5px] text-[var(--color-text-secondary)]">
        Most recent LLM and agent-step failures, freshest first. Click
        a row to drill into the prompt and step payload.
      </p>
      {rows.length === 0 ? (
        <p className="text-[12px] text-[var(--color-text-success)]">
          No recent failures. <IconActivity size={11} className="inline" />
        </p>
      ) : (
        <div className="flex flex-col">
          {rows.map((r) => (
            <FailureRow
              key={`${r.kind}-${r.id}-${r.at}`}
              row={r}
              onClick={() =>
                r.kind === "llm" ? onOpenCall(r.id) : onOpenRun(r.id)
              }
            />
          ))}
        </div>
      )}
    </div>
  );
}

function FailureRow({
  row,
  onClick,
}: {
  row: EvalFailureRow;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex flex-col items-start gap-0.5 border-t-[0.5px] border-[var(--color-border-tertiary)] py-2 text-left text-[12px] hover:bg-[var(--color-background-secondary)] first:border-t-0"
    >
      <div className="flex w-full items-center gap-2">
        <Pill variant={row.kind === "llm" ? "danger" : "warn"}>
          {row.kind === "llm" ? "LLM" : "agent"}
        </Pill>
        <span className="flex-1 truncate font-medium">{row.summary}</span>
        <span className="text-[11px] text-[var(--color-text-tertiary)] tabular-nums">
          {relativeTime(row.at)}
        </span>
      </div>
      {row.detail && (
        <span className="line-clamp-1 text-[11px] text-[var(--color-text-secondary)]">
          {row.detail}
        </span>
      )}
    </button>
  );
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}
