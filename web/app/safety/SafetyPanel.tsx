"use client";

/**
 * Reusable Safety panel — rendered as the body of /safety AND embedded
 * as a tab inside /observability.
 *
 * Two surfaces, one component:
 * - On /safety it lives inside the BrandHeader as the page body, with
 *   the full set of panels visible (KPIs, severity histogram, source
 *   pie, top patterns, judgment rollup, recent detections).
 * - On /observability it's mounted inside the existing tabbed area
 *   with the ``compact`` prop set, which hides the deep-dive
 *   sections (recent table + drawer-open behavior) and just shows
 *   the KPI strip + recent-5 list with deep-links to /safety.
 *
 * Inline charts (severity histogram + source pie) are deliberately
 * SVG with no chart library — keeps bundle weight off the page and
 * the chart shapes match the design tokens.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import {
  IconAlertHexagon,
  IconBolt,
  IconCoin,
  IconShieldCheck,
  IconShieldHalf,
  IconShieldX,
} from "@tabler/icons-react";
import {
  api,
  type InjectionDetectionRow,
  type JudgmentSummary,
  type SafetySummary,
} from "@/lib/api";
import { Pill, type PillVariant } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";
import { Skeleton } from "@/app/components/Skeleton";
import { StatTile } from "@/app/components/StatTile";
import { InjectionDrawer } from "./InjectionDrawer";

interface Props {
  windowHours: number;
  /** When true, hide the deep-dive table + drawer (used inside
   *  /observability where the SafetyPanel is just a quick-look). */
  compact?: boolean;
}

const NUM = (n: number) => n.toLocaleString();
const USD = (n: number) =>
  n < 0.1 ? `$${n.toFixed(4)}` : n < 100 ? `$${n.toFixed(2)}` : `$${n.toFixed(0)}`;

function severityVariant(severity: string): PillVariant {
  if (severity === "high" || severity === "block") return "danger";
  if (severity === "medium" || severity === "warn") return "warn";
  return "success";
}

function decisionVariant(decision: string): PillVariant {
  if (decision === "blocked") return "danger";
  if (decision === "flagged") return "warn";
  return "success";
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

/** Stacked horizontal bar — three segments for low/medium/high. */
function SeverityBar({ counts }: { counts: Record<string, number> }) {
  const low = counts.low ?? 0;
  const med = counts.medium ?? 0;
  const high = counts.high ?? 0;
  const total = low + med + high;
  if (total === 0) {
    return (
      <p className="text-xs text-[var(--color-text-tertiary)]">
        No detections in this window.
      </p>
    );
  }
  const lowPct = (low / total) * 100;
  const medPct = (med / total) * 100;
  const highPct = (high / total) * 100;
  return (
    <div className="space-y-2">
      <div className="flex h-3 overflow-hidden rounded-full bg-[var(--color-background-secondary)]">
        <div
          style={{
            width: `${lowPct}%`,
            background: "var(--color-background-success)",
          }}
          title={`low: ${low}`}
        />
        <div
          style={{
            width: `${medPct}%`,
            background: "var(--color-background-warning)",
          }}
          title={`medium: ${med}`}
        />
        <div
          style={{
            width: `${highPct}%`,
            background: "var(--color-background-danger)",
          }}
          title={`high: ${high}`}
        />
      </div>
      <div className="flex flex-wrap gap-3 text-xs">
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: "var(--color-background-success)" }}
          />
          low {NUM(low)}
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: "var(--color-background-warning)" }}
          />
          medium {NUM(med)}
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: "var(--color-background-danger)" }}
          />
          high {NUM(high)}
        </span>
      </div>
    </div>
  );
}

/** Compact horizontal-bars chart by source kind. Cheaper than SVG
 *  for the four-row case. */
function SourceKindBreakdown({ counts }: { counts: Record<string, number> }) {
  const items = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (items.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-tertiary)]">No data.</p>
    );
  }
  const max = items[0][1];
  return (
    <div className="space-y-2">
      {items.map(([kind, count]) => (
        <div key={kind} className="space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span className="text-[var(--color-text-secondary)]">
              {kind.replace(/_/g, " ")}
            </span>
            <span className="tabular-value text-[var(--color-text-primary)]">
              {NUM(count)}
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-[var(--color-background-secondary)]">
            <div
              className="h-full"
              style={{
                width: `${(count / max) * 100}%`,
                background: "var(--color-brand)",
              }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

export function SafetyPanel({ windowHours, compact = false }: Props) {
  const [openDetectionId, setOpenDetectionId] = useState<string | null>(null);

  const summaryQuery = useQuery<SafetySummary, Error>({
    queryKey: ["safety", "summary", windowHours],
    queryFn: () => api.getSafetySummary(windowHours, compact ? 10 : 25),
    refetchInterval: 30_000,
  });
  const judgmentQuery = useQuery<JudgmentSummary, Error>({
    queryKey: ["safety", "judgments", windowHours],
    queryFn: () => api.getJudgmentSummary(windowHours),
    refetchInterval: 30_000,
    // The judgment view only appears on the full dashboard.
    enabled: !compact,
  });

  const summary = summaryQuery.data;
  const judgments = judgmentQuery.data;

  return (
    <div className="flex flex-col gap-3">
      {/* KPI strip — 5 tiles wide on desktop, 2 across on narrow. */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          {summary ? (
            <StatTile
              label="Scanned"
              value={NUM(summary.total_scanned)}
              trend={`window ${summary.window_hours}h`}
              Icon={IconShieldHalf}
            />
          ) : (
            <Skeleton className="h-[78px]" />
          )}
        </div>
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          {summary ? (
            <StatTile
              label="Allowed"
              value={NUM(summary.total_allowed)}
              Icon={IconShieldCheck}
            />
          ) : (
            <Skeleton className="h-[78px]" />
          )}
        </div>
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          {summary ? (
            <StatTile
              label="Flagged"
              value={NUM(summary.total_flagged)}
              trend={summary.total_flagged > 0 ? "needs review" : undefined}
              trendColor={
                summary.total_flagged > 0
                  ? "var(--color-text-warning)"
                  : undefined
              }
              Icon={IconAlertHexagon}
            />
          ) : (
            <Skeleton className="h-[78px]" />
          )}
        </div>
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          {summary ? (
            <StatTile
              label="Blocked"
              value={NUM(summary.total_blocked)}
              trend={summary.total_blocked > 0 ? "fail-closed fired" : undefined}
              trendColor={
                summary.total_blocked > 0
                  ? "var(--color-text-danger)"
                  : undefined
              }
              Icon={IconShieldX}
            />
          ) : (
            <Skeleton className="h-[78px]" />
          )}
        </div>
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          {summary ? (
            <StatTile
              label="Haiku judge"
              value={NUM(summary.llm_judge_calls)}
              trend={USD(summary.cost_estimate_usd)}
              Icon={IconCoin}
            />
          ) : (
            <Skeleton className="h-[78px]" />
          )}
        </div>
      </div>

      {!compact && (
        <>
          {/* Mid-section: severity histogram + source breakdown side-by-side */}
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] p-3">
              <SectionLabel>By severity</SectionLabel>
              {summary ? (
                <div className="mt-2">
                  <SeverityBar counts={summary.by_severity} />
                </div>
              ) : (
                <Skeleton className="mt-2 h-[60px]" />
              )}
            </div>
            <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] p-3">
              <SectionLabel>By source kind</SectionLabel>
              {summary ? (
                <div className="mt-2">
                  <SourceKindBreakdown counts={summary.by_source_kind} />
                </div>
              ) : (
                <Skeleton className="mt-2 h-[80px]" />
              )}
            </div>
          </div>

          {/* Pattern top-10 + judgment rollup */}
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] p-3">
              <SectionLabel>Top patterns</SectionLabel>
              {summary ? (
                summary.pattern_top.length === 0 ? (
                  <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">
                    No patterns matched in this window.
                  </p>
                ) : (
                  <ul className="mt-2 divide-y divide-[var(--color-border-tertiary)]">
                    {summary.pattern_top.map((p) => (
                      <li
                        key={p.pattern_id}
                        className="flex items-center justify-between py-1.5"
                      >
                        <div className="min-w-0 pr-2">
                          <p className="truncate text-xs font-medium text-[var(--color-text-primary)]">
                            {p.pattern_id}
                          </p>
                          <p className="truncate text-[11px] text-[var(--color-text-tertiary)]">
                            {p.description}
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          <Pill variant={severityVariant(p.severity_floor)}>
                            {p.severity_floor}
                          </Pill>
                          <span className="tabular-value text-xs">
                            {NUM(p.hits)}
                          </span>
                        </div>
                      </li>
                    ))}
                  </ul>
                )
              ) : (
                <Skeleton className="mt-2 h-[120px]" />
              )}
            </div>

            <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] p-3">
              <SectionLabel>Constitutional judge</SectionLabel>
              {judgments ? (
                <JudgmentRollup summary={judgments} />
              ) : (
                <Skeleton className="mt-2 h-[120px]" />
              )}
            </div>
          </div>
        </>
      )}

      {/* Recent detections table. Always rendered — it's the
          primary "what just happened" surface. In compact mode
          it's capped to 5 rows and clicking goes to /safety. */}
      <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
        <div className="flex items-center justify-between border-b-[0.5px] border-[var(--color-border-tertiary)] px-3 py-2">
          <SectionLabel>Recent detections</SectionLabel>
          {compact && (
            <Link
              href="/safety"
              className="text-[11px] text-[var(--color-brand)] hover:underline"
            >
              View all →
            </Link>
          )}
        </div>
        {summary ? (
          summary.recent.length === 0 ? (
            <p className="px-3 py-4 text-xs text-[var(--color-text-tertiary)]">
              No detections — every input in this window passed clean.
            </p>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
                  <th className="px-3 py-2">When</th>
                  <th className="px-3 py-2">Source</th>
                  <th className="px-3 py-2">Severity</th>
                  <th className="px-3 py-2">Decision</th>
                  <th className="px-3 py-2">Actor</th>
                  <th className="px-3 py-2">Loan</th>
                  <th className="px-3 py-2 text-right">Patterns</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border-tertiary)]">
                {(compact
                  ? summary.recent.slice(0, 5)
                  : summary.recent
                ).map((row) => (
                  <DetectionRow
                    key={row.id}
                    row={row}
                    onOpen={
                      compact
                        ? undefined
                        : () => setOpenDetectionId(row.id)
                    }
                  />
                ))}
              </tbody>
            </table>
          )
        ) : (
          <Skeleton className="m-3 h-[160px]" />
        )}
      </div>

      {openDetectionId && (
        <InjectionDrawer
          detectionId={openDetectionId}
          onClose={() => setOpenDetectionId(null)}
        />
      )}
    </div>
  );
}

// --- Row + judgment helper ----------------------------------------------

function DetectionRow({
  row,
  onOpen,
}: {
  row: InjectionDetectionRow;
  onOpen: (() => void) | undefined;
}) {
  return (
    <tr
      className={
        onOpen
          ? "cursor-pointer hover:bg-[var(--color-background-secondary)]"
          : ""
      }
      onClick={onOpen}
    >
      <td className="px-3 py-2 text-[var(--color-text-tertiary)]">
        {relativeTime(row.created_at)}
      </td>
      <td className="px-3 py-2">{row.source_kind.replace(/_/g, " ")}</td>
      <td className="px-3 py-2">
        <Pill variant={severityVariant(row.severity)}>{row.severity}</Pill>
      </td>
      <td className="px-3 py-2">
        <Pill variant={decisionVariant(row.decision)}>{row.decision}</Pill>
      </td>
      <td className="px-3 py-2 text-[var(--color-text-tertiary)]">
        {row.actor_kind}
        {row.actor_id && ` · ${row.actor_id}`}
      </td>
      <td className="px-3 py-2 text-[var(--color-text-tertiary)]">
        {row.loan_id ? (
          <Link
            href={`/loans/${row.loan_id}`}
            className="hover:underline"
            onClick={(e) => e.stopPropagation()}
          >
            {row.loan_id.slice(0, 8)}…
          </Link>
        ) : (
          "—"
        )}
      </td>
      <td className="px-3 py-2 text-right tabular-value">{row.n_patterns}</td>
    </tr>
  );
}

function JudgmentRollup({ summary }: { summary: JudgmentSummary }) {
  if (summary.total_judgments === 0) {
    return (
      <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">
        No agent runs in this window invoked the constitutional judge.
      </p>
    );
  }
  const sev = summary.by_severity;
  const blocks = sev.block ?? 0;
  const warns = sev.warn ?? 0;
  const oks = sev.ok ?? 0;
  return (
    <div className="mt-2 space-y-3">
      <div className="grid grid-cols-3 gap-2 text-xs">
        <div>
          <p className="text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
            Passed
          </p>
          <p className="tabular-value mt-0.5 text-base">{NUM(oks)}</p>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
            Warned
          </p>
          <p className="tabular-value mt-0.5 text-base text-[var(--color-text-warning)]">
            {NUM(warns)}
          </p>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
            Blocked
          </p>
          <p className="tabular-value mt-0.5 text-base text-[var(--color-text-danger)]">
            {NUM(blocks)}
          </p>
        </div>
      </div>
      <div>
        <p className="text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
          Recent judgments
        </p>
        <ul className="mt-1 divide-y divide-[var(--color-border-tertiary)]">
          {summary.rows.slice(0, 5).map((j) => (
            <li
              key={j.agent_run_id}
              className="flex items-start gap-2 py-1.5"
            >
              <Pill variant={severityVariant(j.severity)}>
                {j.severity}
              </Pill>
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs">
                  <span className="font-medium">{j.agent_name}</span>{" "}
                  <span className="text-[var(--color-text-tertiary)]">
                    · {j.attempts} attempt{j.attempts === 1 ? "" : "s"} ·{" "}
                    {relativeTime(j.started_at)}
                  </span>
                </p>
                {j.critique && (
                  <p className="mt-0.5 text-[11px] text-[var(--color-text-tertiary)] line-clamp-2">
                    {j.critique}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
