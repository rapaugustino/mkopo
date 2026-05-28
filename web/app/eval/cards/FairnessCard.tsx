"use client";

/**
 * Adverse Impact Ratio eval card — four-fifths rule fairness screen.
 *
 * Backs ``mkopo/services/fairness.py``. Renders:
 *
 * - Headline pill: the AIR itself, banded against the EEOC 0.80
 *   threshold (``ok`` ≥ 0.85 · ``watch`` 0.80–0.85 · ``concern``
 *   < 0.80).
 * - Per-class approval-rate bars, side by side. Even split = AIR
 *   near 1.0; large split = potential disparate impact.
 * - Per-class counts (n_decisioned + approved + declined) so an
 *   operator can spot whether the AIR is statistically meaningful
 *   or is just noise from small samples.
 *
 * Regulator framing: this is a SCREENING heuristic. The four-fifths
 * rule (EEOC Uniform Guidelines, 1978) is necessary but not
 * sufficient — disparate-treatment analysis, residual disparity
 * after controls, and proxy-feature audits are separate work.
 * Watkins et al. 2024 ("The Four-Fifths Rule is Not Disparate
 * Impact") covers the limits.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { IconRefresh, IconScale } from "@tabler/icons-react";
import { toast } from "sonner";
import {
  api,
  type FairnessDetails,
  type TaskDetail,
} from "@/lib/api";
import { Pill, type PillVariant } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";
import { Skeleton } from "@/app/components/Skeleton";
import { Tooltip } from "@/app/components/Tooltip";
import { NISTBadge } from "./NISTBadge";

const PCT = (v: number | null | undefined, digits = 0) =>
  v == null ? "—" : `${(v * 100).toFixed(digits)}%`;

/** Flag → (pill variant, headline word). Matches the band thresholds
 *  in services/fairness.py:_GroupStats.from_groups. */
const FLAG_META: Record<
  string,
  { variant: PillVariant; label: string }
> = {
  ok: { variant: "success", label: "OK" },
  watch: { variant: "warn", label: "Watch" },
  concern: { variant: "danger", label: "Concern" },
  insufficient_data: { variant: "neutral", label: "No data" },
};

export function FairnessCard() {
  const queryClient = useQueryClient();
  const query = useQuery<TaskDetail, Error>({
    queryKey: ["eval-task-detail", "fairness.adverse_impact_ratio"],
    queryFn: () =>
      api.getTaskDetail("fairness.adverse_impact_ratio"),
    refetchInterval: 60_000,
  });

  // Manual refresh — same code path as the 3:45 AM cron. Useful
  // after staff transition a loan to APPROVED / DECLINED and want
  // to see the AIR move before the next sweep.
  const refresh = useMutation({
    mutationFn: () => api.refreshFairness(),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({
        queryKey: ["eval-task-detail", "fairness.adverse_impact_ratio"],
      });
      if (result.flag === "insufficient_data") {
        toast.info("Fairness monitor ran — insufficient data", {
          description:
            "Need decisioned loans in at least two protected-class buckets to compute AIR.",
          duration: 6_000,
        });
      } else {
        toast.success("Fairness monitor ran", {
          description: `AIR = ${PCT(result.air, 1)} · n=${result.n_loans_decisioned} · flag=${result.flag}`,
        });
      }
    },
    onError: (e) =>
      toast.error("Fairness monitor failed", {
        description: e instanceof Error ? e.message : String(e),
      }),
  });

  if (query.isLoading) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <Skeleton className="mb-2 h-3 w-40" />
        <Skeleton className="h-[160px]" />
      </div>
    );
  }

  // Empty state — no row in task_runs yet. Render the explainer +
  // the refresh button. The four-fifths-rule context is the value
  // of this card even when there's no AIR to display.
  if (!query.data?.found || !query.data.details) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <SectionLabel Icon={IconScale} dense>
            Adverse Impact Ratio (fairness)
          </SectionLabel>
          <RefreshButton
            disabled={refresh.isPending}
            onClick={() => refresh.mutate()}
          />
        </div>
        <p className="text-[12px] text-[var(--color-text-tertiary)]">
          No fairness data yet. The monitor needs at least two
          protected-class groups with decisioned loans (APPROVED /
          DECLINED) in the last 90 days. Run more intakes + close
          out the decisions, or click <em>Refresh</em>.
        </p>
        <p className="mt-2 text-[10.5px] text-[var(--color-text-tertiary)]">
          AIR = lowest-group approval rate ÷ highest-group approval
          rate. EEOC four-fifths rule flags AIR &lt; 0.80 for
          investigation (screening only — not a per-se finding;
          Watkins et al. 2024).
        </p>
      </div>
    );
  }

  const d = query.data.details as unknown as FairnessDetails;
  const ranAt = query.data.ran_at
    ? new Date(query.data.ran_at).toLocaleString()
    : "—";
  const flagMeta = FLAG_META[d.flag] ?? FLAG_META.insufficient_data;

  // Sort groups by approval rate so the "lowest rate" sits on top —
  // that's the regulator-relevant comparator anyway.
  const sortedGroups = [...d.groups].sort(
    (a, b) => a.approval_rate - b.approval_rate,
  );

  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <SectionLabel Icon={IconScale} dense>
          Adverse Impact Ratio (fairness)
        </SectionLabel>
        <span className="flex items-center gap-2 text-[11px] text-[var(--color-text-tertiary)]">
          <Tooltip
            content={
              <>
                <strong>AIR = min group approval rate ÷ max group
                approval rate.</strong> EEOC's Uniform Guidelines on
                Employee Selection (1978) — applied to credit
                decisions per CFPB Circular 2022-03. AIR &lt; 0.80
                triggers further investigation; it is a SCREENING
                heuristic, not a per-se finding of discrimination
                (Watkins et al. 2024).
              </>
            }
            underline
            maxWidth={340}
          >
            AIR
          </Tooltip>
          <Pill variant={flagMeta.variant}>
            {d.air != null ? PCT(d.air, 1) : "—"} · {flagMeta.label}
          </Pill>
          <span>·</span>
          <span>n={query.data.n}</span>
          <span>·</span>
          <span>{ranAt}</span>
          <RefreshButton
            disabled={refresh.isPending}
            onClick={() => refresh.mutate()}
          />
        </span>
      </div>

      <div className="flex flex-col gap-2">
        {sortedGroups.map((g) => (
          <GroupBar
            key={g.name}
            name={g.name}
            rate={g.approval_rate}
            n={g.n_decisioned}
            approved={g.n_approved}
            declined={g.n_declined}
          />
        ))}
      </div>

      <p className="mt-3 flex flex-wrap items-center gap-1.5 text-[10.5px] text-[var(--color-text-tertiary)]">
        <span>
          Window = {d.window_days} days. Threshold ={" "}
          {PCT(d.four_fifths_threshold, 0)}. Protected class is{" "}
          <strong>synthetic</strong> in this deployment (stable
          hash of loan_id); production replaces with the
          HMDA-collected demographic. See{" "}
          <code>services/fairness.py</code>.
        </span>
        <NISTBadge category="harmful_bias" />
      </p>
    </div>
  );
}

function GroupBar({
  name,
  rate,
  n,
  approved,
  declined,
}: {
  name: string;
  rate: number;
  n: number;
  approved: number;
  declined: number;
}) {
  const width = Math.max(2, Math.round(rate * 100));
  return (
    <div className="flex items-center gap-3 text-[11.5px]">
      <span className="w-[80px] truncate text-[var(--color-text-secondary)]">
        {name}
      </span>
      <div className="h-2 flex-1 overflow-hidden rounded bg-[var(--color-background-secondary)]">
        <div
          className="h-full rounded"
          style={{
            width: `${width}%`,
            background: "var(--color-brand)",
          }}
        />
      </div>
      <span className="w-[48px] text-right tabular-value font-medium">
        {PCT(rate)}
      </span>
      <span
        className="w-[110px] text-right text-[11px] text-[var(--color-text-tertiary)] tabular-value"
        title="approved / declined / n decisioned"
      >
        {approved}A / {declined}D · n={n}
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
      title="Re-run the fairness monitor now"
      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10.5px] text-[var(--color-text-secondary)] hover:bg-[var(--color-background-secondary)] disabled:opacity-50"
    >
      <IconRefresh size={11} />
      {disabled ? "Running…" : "Refresh"}
    </button>
  );
}
