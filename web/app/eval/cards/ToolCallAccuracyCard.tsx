"use client";

/**
 * Tool-call accuracy eval card — borrower-chat tool selection.
 *
 * Backs ``evals/tasks/tool_call_accuracy.py``. The borrower chat
 * agent is the only LLM surface where a real applicant can talk
 * directly to the system; mis-routing their intent to the wrong
 * tool ranges from annoying (LLM answers in prose instead of
 * calling a tool) to materially wrong (calls ``update_loan_field``
 * or ``withdraw_application`` when the user only asked a question).
 *
 * Two views in one card:
 *
 * 1. **Per-criterion** — three scoring properties, each its own
 *    bar:
 *      - ``trajectory_inclusion`` — every expected tool got called
 *      - ``no_forbidden`` — no mutating tool was called for a
 *        read-only intent (e.g. ``withdraw_application`` was NOT
 *        triggered by a status question)
 *      - ``argument_correctness`` — required arg keys present
 *
 * 2. **Per-tool** — was each expected tool actually selected when
 *    a fixture requested it? Lets the operator see "the agent
 *    always picks ``get_loan_status`` correctly but misses
 *    ``list_missing_fields`` half the time".
 *
 * The card only covers the BORROWER chat agent surface — the
 * staff-chat agent has its own catalog and isn't tested here yet.
 * Documented in the footnote so a reviewer doesn't mistake "100%
 * tool-call accuracy" as covering both surfaces.
 */

import { useQuery } from "@tanstack/react-query";
import { IconRoute } from "@tabler/icons-react";
import {
  api,
  type TaskDetail,
  type ToolCallAccuracyDetails,
} from "@/lib/api";
import { EmptyState } from "@/app/components/EmptyState";
import { Pill } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";
import { Skeleton } from "@/app/components/Skeleton";
import { Tooltip } from "@/app/components/Tooltip";
import { NISTBadge } from "./NISTBadge";

const PCT = (v: number, digits = 0) => `${(v * 100).toFixed(digits)}%`;

/** Human-readable label + tooltip per criterion. The tooltips name
 *  the failure mode each criterion catches so a reviewer can map
 *  a regression to a concrete agent bug. */
const CRITERION_META: Record<
  string,
  { label: string; tooltip: string }
> = {
  trajectory_inclusion: {
    label: "Trajectory inclusion",
    tooltip:
      "Every tool the fixture expected was actually called. Catches the 'LLM answered in prose instead of calling get_loan_status' failure mode — most common when a prompt is under-specified and the model decides it has enough context to reply without a tool.",
  },
  no_forbidden: {
    label: "No forbidden mutating tools",
    tooltip:
      "The agent did NOT call a mutating tool (update_loan_field, withdraw_application) on a read-only intent. Catches the 'borrower asked about status and the agent withdrew their loan' worst case — the safety-critical regression class.",
  },
  argument_correctness: {
    label: "Required arg keys present",
    tooltip:
      "For each expected tool call, the predicted arguments include every required key. Doesn't check VALUES because the runtime supplies loan_id from the session — we can't know what the LLM should pass. Catches forgotten-arg bugs.",
  },
};

const CRITERION_ORDER = [
  "trajectory_inclusion",
  "no_forbidden",
  "argument_correctness",
];

interface BarProps {
  label: string;
  tooltip: string;
  passed: number;
  n: number;
  rate: number;
}

function CriterionBar({ label, tooltip, passed, n, rate }: BarProps) {
  const colour =
    rate >= 0.92
      ? "var(--color-text-success)"
      : rate >= 0.75
        ? "var(--color-text-warning)"
        : "var(--color-text-danger)";
  const width = Math.max(2, Math.round(rate * 100));
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
      <span className="w-[48px] text-right tabular-value font-medium">
        {PCT(rate)}
      </span>
      <span className="w-[64px] text-right text-[11px] text-[var(--color-text-tertiary)] tabular-value">
        {passed}/{n}
      </span>
    </div>
  );
}

export function ToolCallAccuracyCard() {
  const query = useQuery<TaskDetail, Error>({
    queryKey: ["eval-task-detail", "tool_call_accuracy"],
    queryFn: () => api.getTaskDetail("tool_call_accuracy"),
    refetchInterval: 60_000,
  });

  if (query.isLoading) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <Skeleton className="mb-2 h-3 w-40" />
        <Skeleton className="h-[160px]" />
      </div>
    );
  }
  if (!query.data?.found || !query.data.details) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <SectionLabel Icon={IconRoute}>
          Tool-call accuracy (borrower chat)
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

  const d = query.data.details as unknown as ToolCallAccuracyDetails;
  const ranAt = query.data.ran_at
    ? new Date(query.data.ran_at).toLocaleString()
    : "—";
  const overallAcc = query.data.accuracy ?? 0;

  // Sort per-tool entries by selection rate ascending so any tool
  // the agent is forgetting to call floats to the top.
  const sortedTools = Object.entries(d.per_tool).sort(
    ([, a], [, b]) => a.rate - b.rate,
  );

  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
      <div className="mb-2.5 flex items-center justify-between gap-2">
        <SectionLabel Icon={IconRoute} dense>
          Tool-call accuracy (borrower chat)
        </SectionLabel>
        <span className="flex items-center gap-2 text-[11px] text-[var(--color-text-tertiary)]">
          <Tooltip
            content="Overall pass-rate — fraction of golden fixtures where ALL three criteria (trajectory inclusion, no forbidden mutating tool, arg keys present) passed. Threshold 0.75 because chat agents add legitimate extra tool calls more often than structured-output tasks."
            underline
          >
            overall
          </Tooltip>
          <Pill variant={overallAcc >= 0.75 ? "success" : "warn"}>
            {PCT(overallAcc, 1)}
          </Pill>
          <span>·</span>
          <span>n={query.data.n}</span>
          <span>·</span>
          <span>{ranAt}</span>
        </span>
      </div>

      <div className="flex flex-col gap-3 lg:flex-row lg:items-start">
        {/* Left — per-criterion bars. */}
        <div className="flex flex-1 flex-col gap-2">
          <p className="text-[10px] font-medium uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
            Per-criterion
          </p>
          {CRITERION_ORDER.map((c) => {
            const stats = d.per_criterion[c];
            if (!stats) return null;
            const meta = CRITERION_META[c] ?? {
              label: c,
              tooltip: "No description available.",
            };
            return (
              <CriterionBar
                key={c}
                label={meta.label}
                tooltip={meta.tooltip}
                passed={stats.passed}
                n={stats.n}
                rate={stats.rate}
              />
            );
          })}
        </div>

        {/* Right — per-tool selection. Sorted ascending by rate so
            any tool the agent under-selects surfaces at the top. */}
        <div className="flex flex-col gap-1 rounded-md bg-[var(--color-background-secondary)] px-3 py-2 text-[11px] lg:w-[280px]">
          <p className="text-[10px] font-medium uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
            Per-tool selection
          </p>
          {sortedTools.length === 0 ? (
            <p className="text-[11px] text-[var(--color-text-tertiary)]">
              No tools tested yet — add fixtures under{" "}
              <code>evals/golden_sets/tool_call_accuracy/</code>.
            </p>
          ) : (
            sortedTools.map(([tool, stats]) => (
              <div
                key={tool}
                className="flex items-center justify-between"
              >
                <code className="truncate text-[11px] text-[var(--color-text-secondary)]">
                  {tool}
                </code>
                <span className="flex items-center gap-1.5">
                  <span
                    className="tabular-value font-medium"
                    style={{
                      color:
                        stats.rate >= 0.92
                          ? "var(--color-text-success)"
                          : stats.rate >= 0.75
                            ? "var(--color-text-warning)"
                            : "var(--color-text-danger)",
                    }}
                  >
                    {PCT(stats.rate)}
                  </span>
                  <span className="text-[10px] text-[var(--color-text-tertiary)] tabular-value">
                    ({stats.selected}/{stats.n})
                  </span>
                </span>
              </div>
            ))
          )}
        </div>
      </div>

      <p className="mt-3 flex flex-wrap items-center gap-1.5 text-[10.5px] text-[var(--color-text-tertiary)]">
        <span>
          Covers the <strong>borrower</strong> chat agent's tool
          catalog (<code>mkopo/agents/tools/borrower.py</code>).
          Staff-chat coverage is a separate task; production
          arg-value correctness is gated by the tool itself
          (re-auth on <code>withdraw_application</code>, owner
          check on <code>update_loan_field</code>).
        </span>
        <NISTBadge category="info_integrity" />
      </p>
    </div>
  );
}
