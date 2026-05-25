"use client";

import Link from "next/link";
import {
  IconFlagCheck,
  IconGavel,
  IconInbox,
  IconListCheck,
  IconMicroscope,
} from "@tabler/icons-react";

import { RiskBadge } from "./components/RiskBadge";
import { daysSince } from "@/lib/formatting";
import type { Loan, LoanStage } from "@/lib/api";

/**
 * Kanban view of the pipeline.
 *
 * Sibling to the list table on /. Same data, different layout —
 * one column per active stage, cards inside. Read-only here:
 * stage transitions need a reason string per audit-policy, so
 * drag-to-stage is intentionally not supported. Click a card to
 * open the loan detail; transitions happen there.
 *
 * Stages match the pipeline-table's STAGES list and stay in the
 * same order so users switching views see the same lifecycle.
 * Terminal stages (approved/declined/withdrawn/closing post-funding)
 * are not columns here because the kanban is an "in flight" view —
 * a closed deal moves out of the board, exactly like an inbox.
 */

const COLUMNS: {
  stage: LoanStage;
  label: string;
  Icon: React.ComponentType<{ size?: number }>;
}[] = [
  { stage: "intake", label: "Intake", Icon: IconInbox },
  { stage: "underwriting", label: "Underwriting", Icon: IconMicroscope },
  { stage: "decision", label: "Decision", Icon: IconGavel },
  { stage: "conditions", label: "Conditions", Icon: IconListCheck },
  { stage: "closing", label: "Closing", Icon: IconFlagCheck },
];

function formatAmount(s: string): string {
  const n = Number(s);
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${Math.round(n / 1_000)}K`;
  return `$${n.toFixed(0)}`;
}

// ``daysSince`` lives in ``@/lib/formatting`` — shared with the
// pipeline table so both views agree on the day boundary.

function agingColor(days: number): string {
  if (days >= 7) return "var(--color-text-danger)";
  if (days >= 3) return "var(--color-text-warning)";
  return "var(--color-text-tertiary)";
}

export function PipelineKanban({ loans }: { loans: Loan[] }) {
  // Group filtered loans by stage. Loans not in one of the five
  // columns (e.g. approved, declined, withdrawn) are skipped here;
  // they're surfaced in the table view + on the case-file page.
  const byStage = new Map<LoanStage, Loan[]>();
  for (const l of loans) {
    const arr = byStage.get(l.stage) ?? [];
    arr.push(l);
    byStage.set(l.stage, arr);
  }

  return (
    // Layout strategy:
    // - md+ (≥768px): the original 5-up kanban with horizontal scroll
    //   if the columns don't fit (rare on a real desktop).
    // - <md: columns stack vertically — each becomes a labelled
    //   accordion-like section the user scrolls through. No
    //   horizontal scroll anywhere. The kanban stays the right
    //   mental model (one section per stage) without forcing a
    //   pinch-zoom interaction.
    <div className="overflow-hidden rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] md:overflow-x-auto">
      <div
        className="flex flex-col md:grid"
        style={{
          // Inline style only applies at md+ via the grid display
          // flip above; below md the flex-col layout wins and these
          // grid-template values are inert.
          gridTemplateColumns: `repeat(${COLUMNS.length}, minmax(220px, 1fr))`,
          minWidth: "fit-content",
        }}
      >
        {COLUMNS.map((col, i) => {
          const colLoans = byStage.get(col.stage) ?? [];
          const Icon = col.Icon;
          return (
            <div
              key={col.stage}
              className={
                "flex flex-col md:min-w-[220px] " +
                // Bottom border separates stacked columns on mobile;
                // right border separates side-by-side columns on
                // desktop. Last column gets neither.
                (i < COLUMNS.length - 1
                  ? "border-b-[0.5px] border-[var(--color-border-tertiary)] md:border-b-0 md:border-r-[0.5px]"
                  : "")
              }
            >
              {/* Column header */}
              <div className="flex items-center justify-between gap-2 border-b-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-secondary)] px-3 py-2">
                <div className="flex items-center gap-1.5">
                  <Icon size={12} />
                  <span className="text-[11.5px] font-medium tracking-tight text-[var(--color-text-primary)]">
                    {col.label}
                  </span>
                </div>
                <span className="rounded-md bg-[var(--color-background-primary)] px-1.5 text-[10.5px] font-medium text-[var(--color-text-secondary)]">
                  {colLoans.length}
                </span>
              </div>

              {/* Cards */}
              <div className="flex flex-col gap-1.5 p-2">
                {colLoans.map((loan) => {
                  const age = daysSince(loan.stage_entered_at);
                  return (
                    <Link
                      key={loan.id}
                      href={`/loans/${loan.id}`}
                      className="group block rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] p-2.5 transition-colors hover:border-[var(--color-text-tertiary)] hover:bg-[var(--color-background-secondary)]"
                    >
                      <div className="flex items-baseline justify-between gap-2">
                        <span className="truncate text-[11.5px] font-medium text-[var(--color-text-info)] group-hover:underline">
                          {loan.reference}
                        </span>
                        <span className="shrink-0 text-[11px] tabular-nums text-[var(--color-text-primary)]">
                          {formatAmount(loan.amount)}
                        </span>
                      </div>
                      <p className="mt-0.5 truncate text-[11.5px] text-[var(--color-text-primary)]">
                        {loan.borrower?.name ?? (
                          <span className="text-[var(--color-text-tertiary)]">
                            Borrower TBD
                          </span>
                        )}
                      </p>
                      <div className="mt-2 flex items-center justify-between gap-2 text-[10.5px]">
                        <div className="flex items-center gap-2">
                          {loan.owner ? (
                            <span
                              className="inline-flex h-[18px] w-[18px] items-center justify-center rounded-full bg-[var(--color-background-secondary)] text-[9.5px] font-medium text-[var(--color-text-secondary)]"
                              title={loan.owner.name}
                            >
                              {loan.owner.initials}
                            </span>
                          ) : (
                            <span className="text-[var(--color-text-tertiary)]">
                              Unassigned
                            </span>
                          )}
                          <span style={{ color: agingColor(age) }}>{age}d</span>
                        </div>
                        <RiskBadge band={loan.risk_band} size="xs" />
                      </div>
                    </Link>
                  );
                })}
                {colLoans.length === 0 && (
                  <div className="rounded-md border border-dashed border-[var(--color-border-tertiary)] bg-[var(--color-background-secondary)] px-2 py-3 text-center text-[10.5px] text-[var(--color-text-tertiary)]">
                    No loans
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
