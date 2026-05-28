"use client";

/**
 * Per-agent $/run + p95 latency — the cost / quality / latency
 * trilemma made visible.
 *
 * Backs ``mkopo/services/agent_economics.py`` and the
 * ``GET /eval/agent-economics`` endpoint. One row per agent
 * (intake / underwriting / decision / borrower_chat / staff_chat)
 * over the last 30 days. The headline metric is ``cost_per_run``;
 * p95 latency sits next to it so the operator can spot the case
 * where cost is stable but the tail blew up (or vice-versa).
 *
 * Why this matters operationally: a model upgrade can shift
 * accuracy + cost + latency in different directions. If accuracy
 * goes up but cost-per-run triples and the p95 tail doubles,
 * that's a deployment decision the dashboard should surface
 * inline with the accuracy trend — not in some other observability
 * page the staff member never visits.
 *
 * NIST AI 600-1 mapping: this card addresses "Information
 * Integrity" + "Value Chain and Component Integration" — cost
 * regressions on one agent are a leading indicator that an
 * upstream model change has flipped a knob nobody asked it to.
 */

import { useQuery } from "@tanstack/react-query";
import { IconCoin, IconClockHour4 } from "@tabler/icons-react";
import {
  api,
  type AgentEconResponse,
} from "@/lib/api";
import { Pill, type PillVariant } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";
import { Skeleton } from "@/app/components/Skeleton";
import { Tooltip } from "@/app/components/Tooltip";
import { titleCase } from "@/lib/humanize";
import { NISTBadge } from "./NISTBadge";

const USD = (v: number, digits = 4) =>
  `$${v.toFixed(digits).replace(/\.?0+$/, (s) => (s.startsWith(".") ? s : ""))}`;

const SECS = (v: number | null | undefined) =>
  v == null ? "—" : `${v.toFixed(2)}s`;

// Cost bands ($/run). These are workshop-grade defaults — the
// expensive Opus-judge agents (decision, judge) sit higher than
// the Haiku-only agents (chat tools). Real thresholds depend on
// business margins; tune per deployment.
function costPill(v: number): { variant: PillVariant; label: string } {
  if (v < 0.05) return { variant: "success", label: USD(v) };
  if (v < 0.20) return { variant: "info", label: USD(v) };
  if (v < 0.50) return { variant: "warn", label: USD(v) };
  return { variant: "danger", label: USD(v) };
}

// p95 latency bands. 10s is the perceptual floor for a "fast"
// agent run; 30s starts to feel sluggish; 60s+ is concerning.
function latencyPill(v: number | null): {
  variant: PillVariant;
  label: string;
} {
  if (v == null) return { variant: "neutral", label: "—" };
  if (v < 10) return { variant: "success", label: SECS(v) };
  if (v < 30) return { variant: "info", label: SECS(v) };
  if (v < 60) return { variant: "warn", label: SECS(v) };
  return { variant: "danger", label: SECS(v) };
}

export function AgentEconomicsCard() {
  const query = useQuery<AgentEconResponse, Error>({
    queryKey: ["eval-agent-economics"],
    queryFn: () => api.getAgentEconomics(),
    refetchInterval: 60_000,
  });

  if (query.isLoading) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <Skeleton className="mb-2 h-3 w-44" />
        <Skeleton className="h-[140px]" />
      </div>
    );
  }

  const rows = query.data?.rows ?? [];
  const windowDays = query.data?.window_days ?? 30;

  if (rows.length === 0) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <SectionLabel Icon={IconCoin} dense>
          Per-agent economics ($/run + p95 latency)
        </SectionLabel>
        <p className="mt-2 text-[12px] text-[var(--color-text-tertiary)]">
          No agent runs in the last {windowDays} days.
        </p>
      </div>
    );
  }

  const total = rows.reduce((s, r) => s + r.total_cost_usd, 0);
  const totalRuns = rows.reduce((s, r) => s + r.n_runs, 0);

  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <SectionLabel Icon={IconCoin} dense>
          Per-agent economics
        </SectionLabel>
        <span className="flex items-center gap-2 text-[11px] text-[var(--color-text-tertiary)]">
          <Tooltip
            content={
              <>
                <strong>$/run</strong> = total LLM cost in the
                window ÷ run count.{" "}
                <strong>p95 latency</strong> = 95th percentile of
                per-call ``elapsed_seconds``. Both are computed
                from ``llm_calls`` joined to ``agent_runs`` by
                ``thread_id`` — same population the observability
                page reads from, sliced per agent.
              </>
            }
            underline
            maxWidth={340}
          >
            total
          </Tooltip>
          <Pill variant="neutral">{USD(total, 2)}</Pill>
          <span>·</span>
          <span>{totalRuns} run{totalRuns === 1 ? "" : "s"}</span>
          <span>·</span>
          <span>last {windowDays}d</span>
        </span>
      </div>

      <table className="w-full border-collapse text-[11.5px]">
        <thead>
          <tr className="border-b-[0.5px] border-[var(--color-border-tertiary)]">
            <th className="py-1.5 pr-3 text-left text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
              Agent
            </th>
            <th className="py-1.5 px-2 text-right text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
              Runs
            </th>
            <th className="py-1.5 px-2 text-right text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
              Calls
            </th>
            <th className="py-1.5 px-2 text-right text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
              <Tooltip
                content="Total LLM cost (input + output) divided by run count. Lower is better; bands: <$0.05 ≈ chat-Haiku, $0.05–0.20 ≈ Sonnet agent, $0.20–0.50 ≈ Opus judge, >$0.50 ≈ runaway."
                underline
              >
                $/run
              </Tooltip>
            </th>
            <th className="py-1.5 px-2 text-right text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
              <Tooltip
                content="95th percentile of LLMCall.elapsed_seconds for this agent. The tail matters more than the median — users feel the slow runs."
                underline
              >
                p95 latency
              </Tooltip>
            </th>
            <th className="py-1.5 pl-2 text-right text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
              p50
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const cost = costPill(r.cost_per_run_usd);
            const p95 = latencyPill(r.p95_latency_seconds);
            return (
              <tr
                key={r.agent_name}
                className="border-b-[0.5px] border-[var(--color-border-tertiary)] last:border-b-0"
              >
                <td className="py-1.5 pr-3 font-medium text-[var(--color-text-primary)]">
                  {titleCase(r.agent_name)}
                </td>
                <td className="py-1.5 px-2 text-right tabular-value">
                  {r.n_runs}
                </td>
                <td className="py-1.5 px-2 text-right text-[var(--color-text-tertiary)] tabular-value">
                  {r.n_calls}
                </td>
                <td className="py-1.5 px-2 text-right">
                  <Pill variant={cost.variant}>{cost.label}</Pill>
                </td>
                <td className="py-1.5 px-2 text-right">
                  <Pill variant={p95.variant}>
                    <IconClockHour4 size={10} />
                    {p95.label}
                  </Pill>
                </td>
                <td className="py-1.5 pl-2 text-right text-[var(--color-text-tertiary)] tabular-value">
                  {SECS(r.p50_latency_seconds)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <p className="mt-3 flex flex-wrap items-center gap-1.5 text-[10.5px] text-[var(--color-text-tertiary)]">
        <span>
          Trend chart picks up persisted{" "}
          <code>economics.&lt;agent&gt;</code> rows nightly. The
          trilemma: accuracy ↔ cost ↔ latency rarely move together
          — this card lines them up so a regression doesn't hide in
          the wrong dashboard.
        </span>
        <NISTBadge category="value_chain" />
      </p>
    </div>
  );
}
