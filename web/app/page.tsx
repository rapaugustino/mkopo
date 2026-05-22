"use client";

import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  IconPlus,
  IconInbox,
  IconMicroscope,
  IconGavel,
  IconListCheck,
  IconFlagCheck,
} from "@tabler/icons-react";
import { api, type Loan, type LoanStage, type Owner, type RiskBand } from "@/lib/api";
import { NewLoanModal } from "./NewLoanModal";
import { EMPTY_FILTERS, PipelineFilters, type PipelineFilterState } from "./PipelineFilters";
import { BrandHeader } from "./components/BrandHeader";
import { PrimaryButton } from "./components/PrimaryButton";
import { RiskBadge } from "./components/RiskBadge";
import { Skeleton } from "./components/Skeleton";
import { StagePill } from "./components/StagePill";
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
/**
 * URL ⇄ filter-state codec.
 *
 * Keeps the filters round-trippable through ``router.replace`` so the
 * UI state mirrors the URL, the URL mirrors the UI state, and a
 * refresh / paste-into-another-tab reconstructs the exact view.
 *
 * The encoding is intentionally human-readable (comma-separated, not
 * base64) — pasted URLs read as "I had retail + multifamily filtered
 * to high-risk" rather than as a binary blob.
 */
function filtersFromQuery(params: URLSearchParams): PipelineFilterState {
  const csv = (key: string): Set<string> =>
    new Set(
      (params.get(key) ?? "")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
    );
  return {
    search: params.get("q") ?? "",
    stages: csv("stage") as Set<LoanStage>,
    risks: csv("risk") as Set<RiskBand>,
    ownerIds: csv("owner"),
  };
}

function filtersToQuery(state: PipelineFilterState): string {
  const params = new URLSearchParams();
  if (state.search.trim()) params.set("q", state.search.trim());
  if (state.stages.size) params.set("stage", [...state.stages].join(","));
  if (state.risks.size) params.set("risk", [...state.risks].join(","));
  if (state.ownerIds.size) params.set("owner", [...state.ownerIds].join(","));
  return params.toString();
}

/**
 * Renders the active-filter chips above the pipeline table.
 *
 * The strip materializes whenever any facet is set. Each chip carries
 * the facet's label + value and removes itself on click. The "Clear"
 * link wipes everything.
 *
 * Why a separate component rather than inlining: the strip is rendered
 * on the pipeline page but conceptually belongs to the filter system,
 * and pulling it out keeps the page render legible.
 */
function ActiveFiltersStrip({
  filters,
  owners,
  onChange,
}: {
  filters: PipelineFilterState;
  owners: Owner[];
  onChange: (next: PipelineFilterState) => void;
}) {
  const ownerById = useMemo(() => {
    const m = new Map<string, Owner>();
    for (const o of owners) m.set(o.id, o);
    return m;
  }, [owners]);

  const chips: { key: string; label: string; onRemove: () => void }[] = [];
  if (filters.search.trim()) {
    chips.push({
      key: "search",
      label: `Search: "${filters.search.trim()}"`,
      onRemove: () => onChange({ ...filters, search: "" }),
    });
  }
  for (const s of filters.stages) {
    chips.push({
      key: `stage:${s}`,
      label: `Stage: ${s.charAt(0).toUpperCase() + s.slice(1)}`,
      onRemove: () => {
        const next = new Set(filters.stages);
        next.delete(s);
        onChange({ ...filters, stages: next });
      },
    });
  }
  for (const r of filters.risks) {
    chips.push({
      key: `risk:${r}`,
      label: `Risk: ${r.charAt(0).toUpperCase() + r.slice(1)}`,
      onRemove: () => {
        const next = new Set(filters.risks);
        next.delete(r);
        onChange({ ...filters, risks: next });
      },
    });
  }
  for (const oid of filters.ownerIds) {
    const o = ownerById.get(oid);
    chips.push({
      key: `owner:${oid}`,
      label: `Owner: ${o?.name ?? oid.slice(0, 6)}`,
      onRemove: () => {
        const next = new Set(filters.ownerIds);
        next.delete(oid);
        onChange({ ...filters, ownerIds: next });
      },
    });
  }

  if (chips.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-md bg-[var(--color-background-secondary)] px-3 py-2 text-[12px]">
      <span className="text-[var(--color-text-secondary)]">Filtering by:</span>
      {chips.map((c) => (
        <button
          key={c.key}
          type="button"
          onClick={c.onRemove}
          className="group inline-flex items-center gap-1 rounded-md bg-[var(--color-background-primary)] px-2 py-0.5 text-[11px] font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-background-secondary)]"
          style={{ border: "0.5px solid var(--color-border-tertiary)" }}
          title="Remove this filter"
        >
          {c.label}
          <span className="text-[var(--color-text-tertiary)] group-hover:text-[var(--color-text-secondary)]">
            ×
          </span>
        </button>
      ))}
      <button
        type="button"
        onClick={() => onChange(EMPTY_FILTERS)}
        className="ml-1 text-[11px] text-[var(--color-text-info)] hover:underline"
      >
        Clear all
      </button>
    </div>
  );
}

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

/**
 * Loading placeholder for the pipeline. The shape matches the rendered
 * page — brand header strip, five stage tiles, and a table body — so
 * the layout doesn't reflow when the data arrives. This is what makes
 * the page feel fast even when the API takes a beat.
 */
function PipelineSkeleton() {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <div className="flex items-center gap-3">
          <Skeleton width="w-[30px]" height="h-[30px]" shape="md" />
          <div className="flex flex-col gap-1.5">
            <Skeleton width="w-32" height="h-3.5" />
            <Skeleton width="w-56" height="h-2.5" />
          </div>
        </div>
        <div className="flex gap-1.5">
          <Skeleton width="w-16" height="h-7" />
          <Skeleton width="w-24" height="h-7" />
        </div>
      </div>

      <div className="grid grid-cols-5 overflow-hidden rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
        {[0, 1, 2, 3, 4].map((i) => (
          <div
            key={i}
            className={`flex flex-col gap-1.5 px-3 py-2.5 ${
              i < 4 ? "border-r-[0.5px] border-[var(--color-border-tertiary)]" : ""
            }`}
          >
            <Skeleton width="w-20" height="h-2.5" />
            <Skeleton width="w-10" height="h-5" />
            <Skeleton width="w-24" height="h-2.5" />
          </div>
        ))}
      </div>

      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3 py-2">
        {[0, 1, 2, 3, 4].map((i) => (
          <div
            key={i}
            className="flex items-center justify-between gap-3 border-b-[0.5px] border-[var(--color-border-tertiary)] py-3 last:border-b-0"
          >
            <Skeleton width="w-28" height="h-3" />
            <Skeleton width="w-40" height="h-3" />
            <Skeleton width="w-16" height="h-3" />
            <Skeleton width="w-20" height="h-5" shape="sm" />
            <Skeleton width="w-8" height="h-3" />
            <Skeleton width="w-[22px]" height="h-[22px]" shape="full" />
            <Skeleton width="w-12" height="h-3" />
          </div>
        ))}
      </div>
    </div>
  );
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

  // Filter state lives in the URL so it survives navigation away and
  // back, and so a shared/bookmarked URL recreates the exact view a
  // user was looking at. Query keys map 1:1 to the four facets:
  //   ?q=          search string
  //   ?stage=      comma-separated LoanStage values
  //   ?risk=       comma-separated RiskBand values
  //   ?owner=      comma-separated owner UUIDs
  const router = useRouter();
  const searchParams = useSearchParams();
  const filters: PipelineFilterState = useMemo(
    () => filtersFromQuery(searchParams),
    [searchParams],
  );
  const setFilters = useCallback(
    (next: PipelineFilterState) => {
      const qp = filtersToQuery(next);
      router.replace(qp ? `/?${qp}` : "/", { scroll: false });
    },
    [router],
  );
  const [newLoanOpen, setNewLoanOpen] = useState(false);

  /** Apply filters to the raw loan list.
   *
   *  - search is case-insensitive across reference + borrower name
   *  - stages, risks, ownerIds are OR'd within a facet, AND'd across
   *    facets (matching most filter UIs underwriters expect)
   */
  const filteredLoans = useMemo(() => {
    const q = filters.search.trim().toLowerCase();
    return loans.filter((l) => {
      if (q) {
        const hay = [
          l.reference,
          l.borrower?.name ?? "",
          l.owner?.name ?? "",
        ]
          .join(" ")
          .toLowerCase();
        if (!hay.includes(q)) return false;
      }
      if (filters.stages.size > 0 && !filters.stages.has(l.stage)) return false;
      if (
        filters.risks.size > 0 &&
        (!l.risk_band || !filters.risks.has(l.risk_band))
      ) {
        return false;
      }
      if (
        filters.ownerIds.size > 0 &&
        (!l.owner || !filters.ownerIds.has(l.owner.id))
      ) {
        return false;
      }
      return true;
    });
  }, [loans, filters]);

  /** Distinct owners across the loaded set, sorted by name. The filter
   *  popover renders this as the owner picker. */
  const owners = useMemo<Owner[]>(() => {
    const seen = new Map<string, Owner>();
    for (const l of loans) {
      if (l.owner && !seen.has(l.owner.id)) seen.set(l.owner.id, l.owner);
    }
    return Array.from(seen.values()).sort((a, b) => a.name.localeCompare(b.name));
  }, [loans]);

  // Stage tiles always reflect the *full* loan set, not the filtered one —
  // they're a portfolio overview, not a view of the current filter.
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
    return <PipelineSkeleton />;
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
            <PipelineFilters
              owners={owners}
              value={filters}
              onChange={setFilters}
            />
            <PrimaryButton
              Icon={IconPlus}
              onClick={() => setNewLoanOpen(true)}
            >
              New loan
            </PrimaryButton>
          </>
        }
      />

      <NewLoanModal open={newLoanOpen} onClose={() => setNewLoanOpen(false)} />

      {/* Active-filter strip. Renders only when at least one filter is
          set. Each chip removes its own facet on click, and "Clear" wipes
          everything. This is the persistent reminder that the table the
          user is looking at is a *subset* — without it, an empty-looking
          pipeline after a filter is a real foot-gun. */}
      <ActiveFiltersStrip
        filters={filters}
        owners={owners}
        onChange={setFilters}
      />

      <div className="grid grid-cols-5 overflow-hidden rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
        {STAGES.map((s, i) => {
          const loansInStage = byStage.get(s.stage) ?? [];
          // Stage tiles double as a fast filter affordance: clicking a
          // tile toggles its membership in the stage filter. When the
          // filter is set, non-selected tiles dim so the in-focus stages
          // pop visually.
          const filterHasStage = filters.stages.has(s.stage);
          const dimmed =
            filters.stages.size > 0 && !filterHasStage;
          const onClick = () => {
            const next = new Set(filters.stages);
            next.has(s.stage) ? next.delete(s.stage) : next.add(s.stage);
            setFilters({ ...filters, stages: next });
          };
          return (
            <button
              key={s.stage}
              type="button"
              onClick={onClick}
              title={
                filterHasStage
                  ? `Remove "${s.label}" filter`
                  : `Filter to "${s.label}"`
              }
              className={
                "text-left transition-opacity " +
                (i < STAGES.length - 1
                  ? "border-r-[0.5px] border-[var(--color-border-tertiary)]"
                  : "") +
                (dimmed ? " opacity-45" : " hover:bg-[var(--color-background-secondary)]/60")
              }
            >
              <StatTile
                label={s.label}
                value={loansInStage.length}
                trend={stageTrail(s.stage, loansInStage)}
                Icon={s.Icon}
                active={filterHasStage || (filters.stages.size === 0 && s.stage === activeStage)}
              />
            </button>
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
                Stage
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
            {filteredLoans.map((loan) => {
              const age = daysSince(loan.stage_entered_at);
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
                  <td className="px-2 py-3">
                    <StagePill stage={loan.stage} />
                  </td>
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
                    <RiskBadge band={loan.risk_band} size="xs" />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {loans.length === 0 && (
          <p className="p-6 text-center text-sm text-[var(--color-text-secondary)]">
            No loans yet. Click{" "}
            <strong>New loan</strong> to create one, or run{" "}
            <code className="rounded bg-[var(--color-background-secondary)] px-1.5 py-0.5">
              uv run python scripts/seed.py
            </code>{" "}
            for seeded demo data.
          </p>
        )}
        {loans.length > 0 && filteredLoans.length === 0 && (
          <p className="p-6 text-center text-sm text-[var(--color-text-secondary)]">
            No loans match the current filter. Adjust or clear filters above.
          </p>
        )}
      </div>
    </div>
  );
}
