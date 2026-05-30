"use client";

/**
 * AAL-fidelity eval card — renders per-criterion pass rates for the
 * adverse-action letter drafter.
 *
 * The four criteria mirror what ``evals/tasks/aal_fidelity.py``
 * scores against, each load-bearing for a specific regulation:
 *
 * - ``principal_reasons_complete`` — CFPB Circular 2022-03 + 2023-09:
 *   every blocking rule must be cited as a principal reason.
 * - ``friendly_label_in_body`` — borrower comprehension: the prose
 *   must use plain-English labels, not opaque rule_id tokens.
 * - ``no_rule_id_in_body`` — the inverse: rule_ids belong in the
 *   machine-readable list, not in the body the borrower reads.
 * - ``right_to_know_disclosure`` — ECOA Reg B §1002.9(b): the letter
 *   must inform the applicant of their right to a statement of
 *   specific reasons.
 *
 * Surfacing them as separate bars (instead of just the AND-ed overall
 * pass-rate) means a regression on one criterion is visible without
 * losing the headline. E.g. if the prompt drifts and starts leaking
 * rule_ids into prose, the ``no_rule_id_in_body`` bar drops while the
 * others stay green — actionable signal.
 */

import { useQuery } from "@tanstack/react-query";
import { IconShieldCheck } from "@tabler/icons-react";
import { api, type AALFidelityDetails, type TaskDetail } from "@/lib/api";
import { EmptyState } from "@/app/components/EmptyState";
import { Pill } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";
import { Skeleton } from "@/app/components/Skeleton";
import { Tooltip } from "@/app/components/Tooltip";

const PCT = (v: number, digits = 0) => `${(v * 100).toFixed(digits)}%`;

/** Human-readable label + tooltip per criterion. Keeping these in one
 *  map means the dashboard's regulator-friendly language stays
 *  consistent with the eval task's docstring — touch one file. */
const CRITERION_META: Record<
  string,
  { label: string; tooltip: string }
> = {
  principal_reasons_complete: {
    label: "Principal reasons complete",
    tooltip:
      "Every blocking rule_id from the fixture's expected list appears in the drafted principal_reasons array. CFPB Circular 2022-03 + 2023-09 require lenders to cite every principal reason for an adverse-action decision — omitting any is a compliance failure regardless of intent.",
  },
  friendly_label_in_body: {
    label: "Friendly labels in body",
    tooltip:
      "Each expected friendly label (the borrower-readable phrase, e.g. 'loan-to-value above policy cap') appears in the body prose. ECOA Reg B requires reasons be communicated in language the applicant can understand — not opaque internal tokens.",
  },
  no_rule_id_in_body: {
    label: "No rule_id leakage",
    tooltip:
      "The raw rule_id token (e.g. 'ltv_under_cap') is NOT present as a word in the body prose. The constitutional judge also blocks this; the eval scores it per-example so a regression shows on the trend chart.",
  },
  right_to_know_disclosure: {
    label: "Right-to-know disclosure",
    tooltip:
      "The ECOA 'right to a statement of specific reasons' sentence appears in the body. Required by 12 CFR §1002.9(b). Multiple phrasings accepted — the eval matches any of: 'right to know', 'right to receive', 'specific reasons', 'statement of specific reasons', 'principal reasons'.",
  },
};

/** Canonical criterion order — keeps the bar list stable across renders
 *  even if the backend returns keys in a different order. Matches the
 *  order in evals/tasks/aal_fidelity.py:CRITERIA. */
const CRITERION_ORDER = [
  "principal_reasons_complete",
  "friendly_label_in_body",
  "no_rule_id_in_body",
  "right_to_know_disclosure",
];

interface BarProps {
  criterion: string;
  passed: number;
  n: number;
  rate: number;
}

function CriterionBar({ criterion, passed, n, rate }: BarProps) {
  const meta = CRITERION_META[criterion] ?? {
    label: criterion,
    tooltip: "No description available.",
  };
  // Same colour ramp as the per-field accuracy bars on the main eval
  // page — green ≥0.92, warn ≥0.75, danger below. Consistent visual
  // grammar across the whole dashboard.
  const colour =
    rate >= 0.92
      ? "var(--color-text-success)"
      : rate >= 0.75
        ? "var(--color-text-warning)"
        : "var(--color-text-danger)";
  const width = Math.max(2, Math.round(rate * 100));
  return (
    <div className="flex items-center gap-3 text-[11.5px]">
      <Tooltip content={meta.tooltip} underline>
        <span className="w-[180px] truncate text-[var(--color-text-secondary)]">
          {meta.label}
        </span>
      </Tooltip>
      <div className="h-2 flex-1 overflow-hidden rounded bg-[var(--color-background-secondary)]">
        <div
          className="h-full rounded"
          style={{ width: `${width}%`, background: colour }}
        />
      </div>
      <span className="w-[48px] text-right tabular-value font-medium">
        {PCT(rate)}
      </span>
      <span className="w-[64px] text-right text-[11px] text-[var(--color-text-tertiary)] tabular-value">
        {passed}/{n}
      </span>
    </div>
  );
}

export function AALFidelityCard() {
  const query = useQuery<TaskDetail, Error>({
    queryKey: ["eval-task-detail", "aal_fidelity"],
    queryFn: () => api.getTaskDetail("aal_fidelity"),
    refetchInterval: 60_000,
  });

  if (query.isLoading) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <Skeleton className="mb-2 h-3 w-32" />
        <Skeleton className="h-[140px]" />
      </div>
    );
  }
  if (query.isError) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <SectionLabel Icon={IconShieldCheck}>
          AAL fidelity (CFPB / ECOA)
        </SectionLabel>
        <EmptyState
          size="compact"
          variant="chart"
          title="Couldn't load AAL fidelity"
          description={
            <>
              {query.error?.message || "Backend request failed."}{" "}
              Retrying every minute.
            </>
          }
        />
      </div>
    );
  }
  if (!query.data?.found || !query.data.details) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <SectionLabel Icon={IconShieldCheck}>
          AAL fidelity (CFPB / ECOA)
        </SectionLabel>
        <EmptyState
          size="compact"
          variant="chart"
          title="No run yet"
          description={
            <>
              Run <code className="font-mono text-[11px]">cd api && uv run python -m evals.runner</code> or
              wait for the 4 AM UTC sweep.
            </>
          }
        />
      </div>
    );
  }

  const d = query.data.details as unknown as AALFidelityDetails;
  const ranAt = query.data.ran_at
    ? new Date(query.data.ran_at).toLocaleString()
    : "—";
  const overallAcc = query.data.accuracy ?? 0;

  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
      <div className="mb-2.5 flex items-center justify-between gap-2">
        <SectionLabel Icon={IconShieldCheck} dense>
          <Tooltip
            content="AAL = Adverse Action Letter. Required by ECOA / Reg B + CFPB Bulletin 2022-03 whenever credit is declined or counter-offered. We grade the LLM-drafted AAL against four AND-ed criteria (reasons, contact info, ECOA notice, plain language)."
            helpAnchor="aal"
            underline
          >
            AAL fidelity
          </Tooltip>{" "}
          <span className="text-[var(--color-text-tertiary)]">
            (CFPB / ECOA)
          </span>
        </SectionLabel>
        <span className="flex items-center gap-2 text-[11px] text-[var(--color-text-tertiary)]">
          <Tooltip
            content="Overall pass-rate — fraction of golden-set fixtures where all four criteria passed (strict AND). The per-criterion bars below decompose this so you can see which property is failing when overall < 100%."
            underline
          >
            overall
          </Tooltip>
          {/* Threshold matches the eval gate at
              api/evals/tasks/aal_fidelity.py:103. Showing "warn" while
              the gate says "pass" (the previous 0.85 vs 0.75 split)
              was an unexplained divergence. */}
          <Pill variant={overallAcc >= 0.75 ? "success" : "warn"}>
            {PCT(overallAcc, 1)}
          </Pill>
          <span>·</span>
          <span>n={query.data.n}</span>
          <span>·</span>
          <span>{ranAt}</span>
        </span>
      </div>

      <div className="flex flex-col gap-2">
        {CRITERION_ORDER.map((criterion) => {
          const stats = d.per_criterion[criterion];
          if (!stats) return null;
          return (
            <CriterionBar
              key={criterion}
              criterion={criterion}
              passed={stats.passed}
              n={stats.n}
              rate={stats.rate}
            />
          );
        })}
      </div>
    </div>
  );
}
