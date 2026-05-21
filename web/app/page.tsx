"use client";

import Link from "next/link";
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  IconFilter,
  IconPlus,
  IconInbox,
  IconMicroscope,
  IconGavel,
  IconListCheck,
  IconFlagCheck,
} from "@tabler/icons-react";
import { api, type Loan, type LoanStage, type RiskBand } from "@/lib/api";
import { BrandHeader } from "./components/BrandHeader";
import { PrimaryButton } from "./components/PrimaryButton";
import { SecondaryButton } from "./components/SecondaryButton";
import { StatTile } from "./components/StatTile";

const STAGES: {
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

const RISK_STYLE: Record<RiskBand, { color: string; label: string }> = {
  low: { color: "var(--color-text-success)", label: "Low" },
  med: { color: "var(--color-text-warning)", label: "Med" },
  high: { color: "var(--color-text-danger)", label: "High" },
};

function formatAmount(s: string): string {
  const n = Number(s);
  return `$${(n / 1_000_000).toFixed(1)}M`;
}

function daysSince(iso: string): number {
  return Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000));
}

function agingClass(days: number): string {
  if (days >= 7) return "text-[var(--color-text-danger)] font-medium";
  if (days >= 3) return "text-[var(--color-text-warning)] font-medium";
  return "text-[var(--color-text-secondary)]";
}

/** Per-stage subtitle — mirrors the mockup's trail metadata.
 *
 * The mockup invents richer signals ("2 missing docs", "avg 4.2 days",
 * "$11.2M to fund"). We compute what we honestly can from /loans data and
 * fall through to a "—" when there's nothing meaningful to say.
 */
function stageTrail(stage: LoanStage, loansInStage: Loan[]): string {
  if (loansInStage.length === 0) return "—";
  if (stage === "underwriting" || stage === "intake" || stage === "decision") {
    const avg =
      loansInStage.reduce((s, l) => s + daysSince(l.stage_entered_at), 0) /
      loansInStage.length;
    return `avg ${avg.toFixed(1)} days`;
  }
  if (stage === "conditions") {
    return "awaiting borrower";
  }
  if (stage === "closing") {
    const total = loansInStage.reduce((s, l) => s + Number(l.amount), 0);
    return `$${(total / 1_000_000).toFixed(1)}M to fund`;
  }
  return `${loansInStage.length} active`;
}

export default function PipelinePage() {
  const {
    data: loans = [],
    isPending,
    error,
  } = useQuery<Loan[], Error>({
    queryKey: ["loans"],
    queryFn: () => api.listLoans(),
  });

  const byStage = useMemo(() => {
    const m = new Map<LoanStage, Loan[]>();
    for (const l of loans) {
      const arr = m.get(l.stage) ?? [];
      arr.push(l);
      m.set(l.stage, arr);
    }
    return m;
  }, [loans]);

  // The "active" stage tile in the mockup is the most-populated in-flight
  // stage. Tied stages → earliest in the funnel wins.
  const activeStage = useMemo<LoanStage | null>(() => {
    let best: LoanStage | null = null;
    let bestN = 0;
    for (const s of STAGES) {
      const n = (byStage.get(s.stage) ?? []).length;
      if (n > bestN) {
        best = s.stage;
        bestN = n;
      }
    }
    return best;
  }, [byStage]);

  if (isPending) {
    return (
      <p className="text-sm text-[var(--color-text-secondary)]">Loading pipeline…</p>
    );
  }
  if (error) {
    return <p className="text-sm text-[var(--color-text-danger)]">Error: {error.message}</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      <BrandHeader
        leading={
          <div
            className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-md text-[13px] font-medium tracking-tight"
            style={{ background: "var(--color-brand)", color: "var(--color-brand-light)" }}
          >
            MK
          </div>
        }
        title="Mkopo Lens"
        sub="AI-first origination · Atlas Capital workspace"
        actions={
          <>
            <SecondaryButton Icon={IconFilter}>Filter</SecondaryButton>
            <PrimaryButton Icon={IconPlus}>New loan</PrimaryButton>
          </>
        }
      />

      <div className="grid grid-cols-5 overflow-hidden rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
        {STAGES.map((s, i) => {
          const loansInStage = byStage.get(s.stage) ?? [];
          return (
            <div
              key={s.stage}
              className={
                i < STAGES.length - 1
                  ? "border-r-[0.5px] border-[var(--color-border-tertiary)]"
                  : ""
              }
            >
              <StatTile
                label={s.label}
                value={loansInStage.length}
                trend={stageTrail(s.stage, loansInStage)}
                Icon={s.Icon}
                active={s.stage === activeStage}
              />
            </div>
          );
        })}
      </div>

      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3">
        <table className="w-full text-[12.5px]">
          <thead>
            <tr className="border-b-[0.5px] border-[var(--color-border-tertiary)]">
              <th className="px-2 py-3 text-left text-[11px] font-medium uppercase tracking-[0.03em] text-[var(--color-text-secondary)]">
                Loan
              </th>
              <th className="px-2 py-3 text-left text-[11px] font-medium uppercase tracking-[0.03em] text-[var(--color-text-secondary)]">
                Borrower
              </th>
              <th className="px-2 py-3 text-right text-[11px] font-medium uppercase tracking-[0.03em] text-[var(--color-text-secondary)]">
                Amount
              </th>
              <th className="px-2 py-3 text-left text-[11px] font-medium uppercase tracking-[0.03em] text-[var(--color-text-secondary)]">
                Aging
              </th>
              <th className="px-2 py-3 text-left text-[11px] font-medium uppercase tracking-[0.03em] text-[var(--color-text-secondary)]">
                Owner
              </th>
              <th className="px-2 py-3 text-right text-[11px] font-medium uppercase tracking-[0.03em] text-[var(--color-text-secondary)]">
                Risk
              </th>
            </tr>
          </thead>
          <tbody>
            {loans.map((loan) => {
              const age = daysSince(loan.stage_entered_at);
              const risk = loan.risk_band ? RISK_STYLE[loan.risk_band] : null;
              return (
                <tr
                  key={loan.id}
                  className="border-b-[0.5px] border-[var(--color-border-tertiary)] last:border-b-0"
                >
                  <td className="px-2 py-3">
                    <Link
                      href={`/loans/${loan.id}`}
                      className="font-medium text-[var(--color-text-info)] hover:underline"
                    >
                      {loan.reference}
                    </Link>
                  </td>
                  <td className="px-2 py-3 text-[var(--color-text-primary)]">
                    {loan.borrower?.name ?? (
                      <span className="text-[var(--color-text-tertiary)]">—</span>
                    )}
                  </td>
                  <td className="px-2 py-3 text-right">{formatAmount(loan.amount)}</td>
                  <td className={`px-2 py-3 ${agingClass(age)}`}>{age}d</td>
                  <td className="px-2 py-3">
                    {loan.owner ? (
                      <span
                        className="inline-flex h-[22px] w-[22px] items-center justify-center rounded-full bg-[var(--color-background-secondary)] text-[10px] font-medium text-[var(--color-text-secondary)]"
                        title={loan.owner.name}
                      >
                        {loan.owner.initials}
                      </span>
                    ) : (
                      <span className="text-[var(--color-text-tertiary)]">—</span>
                    )}
                  </td>
                  <td className="px-2 py-3 text-right">
                    {risk ? (
                      <span
                        className="inline-flex items-center gap-1.5 font-medium"
                        style={{ color: risk.color }}
                      >
                        <span
                          className="inline-block h-[6px] w-[6px] rounded-full"
                          style={{ background: risk.color }}
                        />
                        {risk.label}
                      </span>
                    ) : (
                      <span className="text-[var(--color-text-tertiary)]">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {loans.length === 0 && (
          <p className="p-6 text-center text-sm text-[var(--color-text-secondary)]">
            No loans yet. Run{" "}
            <code className="rounded bg-[var(--color-background-secondary)] px-1.5 py-0.5">
              uv run python scripts/seed.py
            </code>{" "}
            to seed.
          </p>
        )}
      </div>
    </div>
  );
}
