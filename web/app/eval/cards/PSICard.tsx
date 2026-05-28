"use client";

/**
 * Population Stability Index card — input-feature drift, leading
 * indicator of model degradation.
 *
 * Backs ``mkopo/services/psi.py``. Renders one row per monitored
 * feature (loan_amount / loan_class / loan_type), each banded
 * against Siddiqi 2017 / FDIC convention:
 *
 *   PSI < 0.10            — stable
 *   0.10 ≤ PSI < 0.25     — minor shift; investigate
 *   PSI ≥ 0.25            — major shift; recalibrate / pause
 *
 * Why a separate card from the trend chart: PSI's polarity is
 * inverted vs accuracy (lower is better) and the band thresholds
 * are different. Co-rendering on the same axis would mislead.
 *
 * The headline pill shows the *worst* feature — operationally
 * that's what triggers escalation. The per-feature rows then let
 * the operator drill into which input shifted.
 */

import {
  useMutation,
  useQueries,
  useQueryClient,
} from "@tanstack/react-query";
import { IconRefresh, IconChartHistogram } from "@tabler/icons-react";
import { toast } from "sonner";
import {
  api,
  type PSIDetails,
  type TaskDetail,
} from "@/lib/api";
import { Pill, type PillVariant } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";
import { Skeleton } from "@/app/components/Skeleton";
import { Tooltip } from "@/app/components/Tooltip";
import { NISTBadge } from "./NISTBadge";

const FEATURES: { task: string; label: string; tooltip: string }[] = [
  {
    task: "psi.loan_amount",
    label: "Loan amount",
    tooltip:
      "Numeric: quantile-binned (10 bins) against the reference window. Shifts here usually mean the borrower mix has changed — e.g. an inrush of small-dollar personal loans into a previously commercial-only pipeline.",
  },
  {
    task: "psi.loan_class",
    label: "Loan class (personal / business)",
    tooltip:
      "Categorical: distribution shift between personal and business loans. A sudden swing affects every downstream metric — the rules engine, AAL drafter, and intake email all branch on this.",
  },
  {
    task: "psi.loan_type",
    label: "Loan type (bridge / permanent / refi / construction)",
    tooltip:
      "Categorical: product mix. Refinance volume rising mid-cycle (rate moves) or construction concentration spiking both fall out as PSI here.",
  },
];

const FLAG_META: Record<
  string,
  { variant: PillVariant; label: string }
> = {
  stable: { variant: "success", label: "Stable" },
  minor: { variant: "warn", label: "Minor" },
  major: { variant: "danger", label: "Major" },
};

export function PSICard() {
  const queryClient = useQueryClient();

  // ``useQueries`` instead of N ``useQuery`` calls in a loop —
  // semantically identical for our static FEATURES list, but lets
  // react-query batch the cache subscriptions and keeps the
  // rules-of-hooks contract obvious for the next reader.
  const queries = useQueries({
    queries: FEATURES.map((f) => ({
      queryKey: ["eval-task-detail", f.task],
      queryFn: () => api.getTaskDetail(f.task),
      refetchInterval: 60_000,
    })),
  }) as ReturnType<
    typeof useQueries<{ data: TaskDetail; error: Error }[]>
  >;

  const refresh = useMutation({
    mutationFn: () => api.refreshPSI(),
    onSuccess: async (result) => {
      await Promise.all(
        FEATURES.map((f) =>
          queryClient.invalidateQueries({
            queryKey: ["eval-task-detail", f.task],
          }),
        ),
      );
      if (result.features.length === 0) {
        toast.info("PSI monitor ran — insufficient samples", {
          description: `Need ≥ 30 loans in BOTH the current (last ${result.window_current_days}d) and reference (prior ${result.window_reference_days}d) windows.`,
          duration: 7_000,
        });
      } else {
        const worst = [...result.features].sort(
          (a, b) => b.psi - a.psi,
        )[0];
        toast.success("PSI monitor ran", {
          description: `Worst feature: ${worst.feature} (PSI ${worst.psi.toFixed(2)} · ${worst.flag}).`,
        });
      }
    },
    onError: (e) =>
      toast.error("PSI monitor failed", {
        description: e instanceof Error ? e.message : String(e),
      }),
  });

  const isLoading = queries.some((q) => q.isLoading);
  const anyFound = queries.some(
    (q) => (q.data as TaskDetail | undefined)?.found,
  );

  if (isLoading) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <Skeleton className="mb-2 h-3 w-40" />
        <Skeleton className="h-[140px]" />
      </div>
    );
  }

  if (!anyFound) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <SectionLabel Icon={IconChartHistogram} dense>
            PSI — input-feature drift
          </SectionLabel>
          <RefreshButton
            disabled={refresh.isPending}
            onClick={() => refresh.mutate()}
          />
        </div>
        <p className="text-[12px] text-[var(--color-text-tertiary)]">
          No PSI data yet. Need ≥ 30 loans in BOTH the current
          (last 30d) AND reference (prior 90d) windows. Production
          traffic populates this over time; click <em>Refresh</em>
          to recompute on demand.
        </p>
        <p className="mt-2 text-[10.5px] text-[var(--color-text-tertiary)]">
          PSI = Σ (p_cur − p_ref) × ln(p_cur / p_ref). Bands:{" "}
          &lt; 0.10 stable · 0.10–0.25 minor · ≥ 0.25 major
          (Siddiqi 2017; FDIC supervisory guidance on model risk
          management).
        </p>
      </div>
    );
  }

  // Worst-feature pill: largest observed PSI. We ignore features
  // with no data so they don't masquerade as "stable" 0.0.
  let worstFlag: "stable" | "minor" | "major" = "stable";
  let worstPSI = 0;
  for (const q of queries) {
    const td = q.data as TaskDetail | undefined;
    if (!td?.found || !td.details) continue;
    const d = td.details as unknown as PSIDetails;
    if (d.psi > worstPSI) {
      worstPSI = d.psi;
      worstFlag = d.flag;
    }
  }
  const worstMeta = FLAG_META[worstFlag] ?? FLAG_META.stable;

  const populated = queries
    .map((q) => {
      const td = q.data as TaskDetail | undefined;
      return td?.details as PSIDetails | undefined;
    })
    .find((d) => d != null);

  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <SectionLabel Icon={IconChartHistogram} dense>
          PSI — input-feature drift
        </SectionLabel>
        <span className="flex items-center gap-2 text-[11px] text-[var(--color-text-tertiary)]">
          <Tooltip
            content={
              <>
                <strong>Worst-feature PSI.</strong> The dashboard
                shows whichever monitored input has the largest
                drift; the per-feature rows below decompose it.
                Bands follow Siddiqi 2017 / FDIC supervisory
                guidance: &lt; 0.10 stable, 0.10–0.25 minor (look
                into it), ≥ 0.25 major (recalibrate or pause).
              </>
            }
            underline
            maxWidth={340}
          >
            worst PSI
          </Tooltip>
          <Pill variant={worstMeta.variant}>
            {worstPSI.toFixed(3)} · {worstMeta.label}
          </Pill>
          <RefreshButton
            disabled={refresh.isPending}
            onClick={() => refresh.mutate()}
          />
        </span>
      </div>

      <div className="flex flex-col gap-2">
        {FEATURES.map((f, i) => {
          const td = queries[i].data as TaskDetail | undefined;
          const d = td?.found
            ? (td.details as unknown as PSIDetails | null)
            : null;
          return (
            <FeatureRow
              key={f.task}
              label={f.label}
              tooltip={f.tooltip}
              details={d}
            />
          );
        })}
      </div>

      {populated && (
        <p className="mt-3 flex flex-wrap items-center gap-1.5 text-[10.5px] text-[var(--color-text-tertiary)]">
          <span>
            Current window: last {populated.window_current_days}d.
            Reference window: prior {populated.window_reference_days}d
            (with a {populated.window_current_days}d gap). Siddiqi
            2017; FDIC SR 11-7.
          </span>
          <NISTBadge category="info_integrity" />
        </p>
      )}
    </div>
  );
}

function FeatureRow({
  label,
  tooltip,
  details,
}: {
  label: string;
  tooltip: string;
  details: PSIDetails | null;
}) {
  if (!details) {
    return (
      <div className="flex items-center gap-3 text-[11.5px]">
        <Tooltip content={tooltip} underline maxWidth={320}>
          <span className="w-[220px] truncate text-[var(--color-text-secondary)]">
            {label}
          </span>
        </Tooltip>
        <div className="h-2 flex-1 overflow-hidden rounded bg-[var(--color-background-secondary)]" />
        <span className="w-[110px] text-right text-[11px] text-[var(--color-text-tertiary)] tabular-value">
          insufficient data
        </span>
      </div>
    );
  }
  const meta = FLAG_META[details.flag] ?? FLAG_META.stable;
  // Bar saturation = PSI as fraction of the major threshold (0.25),
  // capped at 100%. Bands also colour-code so the bar is readable
  // even at low width.
  const width = Math.max(
    2,
    Math.min(100, Math.round((details.psi / 0.25) * 100)),
  );
  const colour =
    details.flag === "stable"
      ? "var(--color-text-success)"
      : details.flag === "minor"
        ? "var(--color-text-warning)"
        : "var(--color-text-danger)";
  return (
    <div className="flex items-center gap-3 text-[11.5px]">
      <Tooltip content={tooltip} underline maxWidth={320}>
        <span className="w-[220px] truncate text-[var(--color-text-secondary)]">
          {label}
        </span>
      </Tooltip>
      <div className="h-2 flex-1 overflow-hidden rounded bg-[var(--color-background-secondary)]">
        <div
          className="h-full rounded"
          style={{ width: `${width}%`, background: colour }}
        />
      </div>
      <span className="w-[68px] text-right">
        <Pill variant={meta.variant}>
          {details.psi.toFixed(3)}
        </Pill>
      </span>
      <span
        className="w-[110px] text-right text-[11px] text-[var(--color-text-tertiary)] tabular-value"
        title={`current=${details.n_current} · reference=${details.n_reference} · kind=${details.feature_kind}`}
      >
        cur {details.n_current} · ref {details.n_reference}
      </span>
    </div>
  );
}

function RefreshButton({
  disabled,
  onClick,
}: {
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title="Re-run the PSI monitor now"
      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10.5px] text-[var(--color-text-secondary)] hover:bg-[var(--color-background-secondary)] disabled:opacity-50"
    >
      <IconRefresh size={11} />
      {disabled ? "Running…" : "Refresh"}
    </button>
  );
}
