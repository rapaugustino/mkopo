"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  IconActivity,
  IconAlertOctagon,
  IconBolt,
  IconBug,
  IconClockHour4,
  IconCoin,
  IconReload,
  IconSparkles,
} from "@tabler/icons-react";
import {
  api,
  type AgentRunRow,
  type AgentSummary,
  type ErrorClassStat,
  type InfrastructureErrorRow,
  type InfrastructureErrorSummary,
  type LLMCallRow,
  type LLMSummary,
  type ModelStats,
} from "@/lib/api";
import { titleCase } from "@/lib/humanize";
import { BrandHeader } from "@/app/components/BrandHeader";
import { Pill, type PillVariant } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";
import { Skeleton } from "@/app/components/Skeleton";
import { StatTile } from "@/app/components/StatTile";
import { AgentRunDrawer } from "./AgentRunDrawer";
import { LLMCallDrawer } from "./LLMCallDrawer";

const WINDOWS: { hours: number; label: string }[] = [
  { hours: 1, label: "1h" },
  { hours: 24, label: "24h" },
  { hours: 168, label: "7d" },
  { hours: 720, label: "30d" },
];

const PCT = (v: number | null | undefined, digits = 1) =>
  v == null ? "—" : `${(v * 100).toFixed(digits)}%`;
const SEC = (v: number | null | undefined) =>
  v == null ? "—" : `${v.toFixed(2)}s`;
/** Format USD with sensible precision: <$0.10 to 4dp, $0.10–$100 to
 *  2dp, $100+ to whole dollars. Avoids "$0.00" lies on tiny rollups. */
const USD = (v: number | null | undefined): string => {
  if (v == null) return "—";
  if (v < 0.1) return `$${v.toFixed(4)}`;
  if (v < 100) return `$${v.toFixed(2)}`;
  return `$${v.toFixed(0)}`;
};

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

function statusVariant(status: string): PillVariant {
  if (status === "ok") return "success";
  if (status === "schema_failed" || status === "error") return "danger";
  return "warn";
}

export default function ObservabilityPage() {
  const [windowHours, setWindowHours] = useState(24);
  // ID of the LLM call whose detail drawer is open. ``null`` means
  // the drawer is closed. Plain state (not a route segment) because
  // observability is a transient inspection — a deep link to "call X"
  // doesn't carry meaning across server restarts since IDs are uuids.
  const [openCallId, setOpenCallId] = useState<string | null>(null);
  // ID of the agent run whose trace drawer is open. Same shape as
  // openCallId. Clicking an LLM-call row inside the agent-run drawer
  // hands the id back here so the LLM drawer can take over.
  const [openRunId, setOpenRunId] = useState<string | null>(null);
  // ID of the infrastructure-error row whose detail drawer is open.
  // Same pattern as the LLM/agent drawers above.
  const [openErrorId, setOpenErrorId] = useState<string | null>(null);

  const llmQuery = useQuery<LLMSummary, Error>({
    queryKey: ["observability", "llm", windowHours],
    queryFn: () => api.getLLMObservability(windowHours),
    refetchInterval: 30_000,
  });
  const agentsQuery = useQuery<AgentSummary, Error>({
    queryKey: ["observability", "agents", windowHours],
    queryFn: () => api.getAgentsObservability(windowHours),
    refetchInterval: 30_000,
  });

  const errorsQuery = useQuery<InfrastructureErrorSummary, Error>({
    queryKey: ["observability", "errors", windowHours],
    // Errors are rare — a 24h view on a healthy install is usually
    // empty. We honour the picker so the user can correlate against
    // the LLM window, but the empty-state copy explains the cadence.
    queryFn: () => api.getErrorsObservability(windowHours),
    refetchInterval: 30_000,
  });

  const llm = llmQuery.data;
  const agents = agentsQuery.data;
  const errors = errorsQuery.data;

  return (
    <div className="flex flex-col gap-3">
      <BrandHeader
        title="Observability"
        sub="Live signals from the LLM gateway and LangGraph orchestrator. Updates every 30s."
        actions={<WindowPicker hours={windowHours} onChange={setWindowHours} />}
      />

      {/* Headline KPIs across the chosen window. Six tiles: LLM-side
          health (calls / p95 / error rate / cost) + system-side
          health (agent runs / server errors). The cost tile is the
          one that turns abstract "the AI ran" into "the AI spent X
          dollars" — the most important business signal here. */}
      <div className="grid grid-cols-3 gap-2 lg:grid-cols-6">
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          {llm ? (
            <StatTile
              label="LLM calls"
              value={llm.total_calls.toLocaleString()}
              trend={`window ${llm.window_hours}h`}
              Icon={IconBolt}
            />
          ) : (
            <SkeletonStat />
          )}
        </div>
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          {llm ? (
            <StatTile
              label="p95 latency"
              value={SEC(llm.p95_seconds)}
              trend={`p50 ${SEC(llm.p50_seconds)}`}
              Icon={IconClockHour4}
            />
          ) : (
            <SkeletonStat />
          )}
        </div>
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          {llm ? (
            <StatTile
              label="Error rate"
              value={PCT(llm.error_rate)}
              trend={`schema fails ${PCT(llm.schema_fail_rate)}`}
              Icon={IconAlertOctagon}
              trendColor={
                (llm.error_rate ?? 0) > 0.05
                  ? "var(--color-text-danger)"
                  : undefined
              }
            />
          ) : (
            <SkeletonStat />
          )}
        </div>
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          {llm ? (
            <StatTile
              label="LLM cost"
              value={USD(llm.total_cost_usd)}
              trend={
                llm.uncosted_calls > 0
                  ? `${llm.uncosted_calls} uncosted`
                  : `${llm.total_input_tokens.toLocaleString()} in · ${llm.total_output_tokens.toLocaleString()} out`
              }
              trendColor={
                llm.uncosted_calls > 0
                  ? "var(--color-text-warning)"
                  : undefined
              }
              Icon={IconCoin}
            />
          ) : (
            <SkeletonStat />
          )}
        </div>
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          {agents ? (
            <StatTile
              label="Agent runs"
              value={agents.total_runs.toLocaleString()}
              trend={Object.entries(agents.by_agent)
                .map(([k, v]) => `${k}: ${v}`)
                .join(" · ")}
              Icon={IconActivity}
            />
          ) : (
            <SkeletonStat />
          )}
        </div>
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          {errors ? (
            <StatTile
              label="Server errors"
              value={errors.total.toLocaleString()}
              trend={
                errors.total === 0
                  ? "none in window — healthy"
                  : `${errors.by_class.length} error class${errors.by_class.length === 1 ? "" : "es"}`
              }
              trendColor={
                errors.total > 0 ? "var(--color-text-danger)" : undefined
              }
              Icon={IconBug}
            />
          ) : (
            <SkeletonStat />
          )}
        </div>
      </div>

      {/* Per-model rollup. The "good" lever is to push expensive work
          to a cheaper model when accuracy holds — this table is what
          tells you which model is doing most of the spending. */}
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <SectionLabel
          Icon={IconSparkles}
          trailing={`${llm?.by_model.length ?? 0} model${
            llm?.by_model.length === 1 ? "" : "s"
          } seen`}
        >
          By model
        </SectionLabel>
        {llm ? (
          <ModelTable rows={llm.by_model} />
        ) : (
          <Skeleton width="w-full" height="h-24" />
        )}
      </div>

      {/* Recent calls — the raw row. Use this when an agent run looked
          off and you want to inspect which LLM call slowed it down or
          failed schema validation. */}
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <SectionLabel
          Icon={IconBolt}
          trailing={`${llm?.recent.length ?? 0} most recent`}
        >
          LLM calls
        </SectionLabel>
        {llm ? (
          <LLMCallTable rows={llm.recent} onSelect={setOpenCallId} />
        ) : (
          <RowSkeletons />
        )}
      </div>

      {/* Agent runs — pair to the LLM table. Each agent run is a
          parent for any number of LLM calls; the thread_id is what
          links them. */}
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <SectionLabel
          Icon={IconReload}
          trailing={`${agents?.recent.length ?? 0} most recent`}
        >
          Agent runs
        </SectionLabel>
        {agents ? (
          <AgentRunTable rows={agents.recent} onSelect={setOpenRunId} />
        ) : (
          <RowSkeletons />
        )}
      </div>

      {/* Server errors — what blew up before the LLM ever ran. Lives
          at the bottom of the page because on a healthy install it's
          mostly empty; when it's not, the headline "Server errors"
          tile above will already have lit up red. */}
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <SectionLabel
          Icon={IconBug}
          trailing={
            errors
              ? errors.total === 0
                ? "none in window"
                : `${errors.recent.length} most recent`
              : ""
          }
        >
          Server errors
        </SectionLabel>
        {errors ? (
          <ErrorTable
            classes={errors.by_class}
            rows={errors.recent}
            onSelect={setOpenErrorId}
          />
        ) : (
          <RowSkeletons />
        )}
      </div>

      {/* Drill-in drawers. All three mount at the page root so the
          slide-in isn't clipped by the table's container; all close
          via the parent's setter. The agent-run drawer hands LLM-call
          ids back up to ``setOpenCallId`` so clicking through goes
          call → run → LLM call seamlessly. */}
      <AgentRunDrawer
        runId={openRunId}
        onClose={() => setOpenRunId(null)}
        onOpenLLMCall={(id) => {
          setOpenRunId(null);
          setOpenCallId(id);
        }}
      />
      <LLMCallDrawer callId={openCallId} onClose={() => setOpenCallId(null)} />
      <ErrorDrawer
        errorId={openErrorId}
        onClose={() => setOpenErrorId(null)}
      />
    </div>
  );
}

// ----- small UI bits ------------------------------------------------------

function WindowPicker({
  hours,
  onChange,
}: {
  hours: number;
  onChange: (h: number) => void;
}) {
  return (
    <div className="inline-flex overflow-hidden rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
      {WINDOWS.map((w) => {
        const active = w.hours === hours;
        return (
          <button
            key={w.hours}
            onClick={() => onChange(w.hours)}
            className={
              "px-2.5 py-1 text-[11px] font-medium transition-colors " +
              (active
                ? "bg-[var(--color-background-secondary)] text-[var(--color-text-primary)]"
                : "text-[var(--color-text-secondary)] hover:bg-[var(--color-background-secondary)]")
            }
          >
            {w.label}
          </button>
        );
      })}
    </div>
  );
}

function SkeletonStat() {
  return (
    <div className="p-3">
      <Skeleton width="w-16" height="h-2.5" />
      <div className="mt-2">
        <Skeleton width="w-20" height="h-5" />
      </div>
    </div>
  );
}

function RowSkeletons() {
  return (
    <div className="flex flex-col gap-2 py-2">
      {[0, 1, 2, 3, 4].map((i) => (
        <div key={i} className="flex items-center gap-3">
          <Skeleton width="w-20" height="h-3" />
          <Skeleton width="w-32" height="h-3" />
          <Skeleton width="w-16" height="h-3" />
          <Skeleton width="w-12" height="h-3" />
          <Skeleton width="w-20" height="h-3" />
        </div>
      ))}
    </div>
  );
}

function ModelTable({ rows }: { rows: ModelStats[] }) {
  if (rows.length === 0) {
    return (
      <p className="text-[12px] text-[var(--color-text-tertiary)]">
        No LLM calls in the selected window.
      </p>
    );
  }
  return (
    <table className="w-full text-[12.5px]">
      <thead>
        <tr className="border-b-[0.5px] border-[var(--color-border-tertiary)]">
          {["Model", "Calls", "p50", "p95", "Error rate", "Retry rate", "Cost"].map(
            (h, i) => (
              <th
                key={h}
                className={
                  "px-2 py-2 text-[10px] font-medium uppercase tracking-[0.03em] text-[var(--color-text-secondary)] " +
                  (i === 0 ? "text-left" : "text-right")
                }
              >
                {h}
              </th>
            ),
          )}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr
            key={r.model}
            className="border-b-[0.5px] border-[var(--color-border-tertiary)] last:border-b-0"
          >
            <td className="px-2 py-2 font-medium">{r.model}</td>
            <td className="px-2 py-2 text-right">{r.calls.toLocaleString()}</td>
            <td className="px-2 py-2 text-right">{SEC(r.p50_seconds)}</td>
            <td className="px-2 py-2 text-right">{SEC(r.p95_seconds)}</td>
            <td
              className="px-2 py-2 text-right font-medium"
              style={{
                color:
                  (r.error_rate ?? 0) > 0.05
                    ? "var(--color-text-danger)"
                    : undefined,
              }}
            >
              {PCT(r.error_rate)}
            </td>
            <td
              className="px-2 py-2 text-right"
              style={{
                color:
                  (r.retry_rate ?? 0) > 0.1
                    ? "var(--color-text-warning)"
                    : "var(--color-text-secondary)",
              }}
            >
              {PCT(r.retry_rate)}
            </td>
            <td
              className="px-2 py-2 text-right tabular-nums"
              title={
                r.cost_usd == null
                  ? "Model not in pricing registry"
                  : `${r.input_tokens.toLocaleString()} in / ${r.output_tokens.toLocaleString()} out tokens`
              }
            >
              {USD(r.cost_usd)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function LLMCallTable({
  rows,
  onSelect,
}: {
  rows: LLMCallRow[];
  onSelect: (id: string) => void;
}) {
  if (rows.length === 0) {
    return (
      <p className="text-[12px] text-[var(--color-text-tertiary)]">
        No LLM calls yet in the selected window. Trigger an intake or
        underwriting run on any loan to populate.
      </p>
    );
  }
  return (
    <table className="w-full text-[12.5px]">
      <thead>
        <tr className="border-b-[0.5px] border-[var(--color-border-tertiary)]">
          {["Time", "Model", "Schema", "Status", "Attempt", "Latency", "Tokens"].map(
            (h, i) => (
              <th
                key={h}
                className={
                  "px-2 py-2 text-[10px] font-medium uppercase tracking-[0.03em] text-[var(--color-text-secondary)] " +
                  (i === 0 ? "text-left" : i >= 4 ? "text-right" : "text-left")
                }
              >
                {h}
              </th>
            ),
          )}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr
            key={r.id}
            // Row click opens the detail drawer — answers "what
            // actually went wrong with this call?" Cursor + hover bg
            // signal the row is interactive.
            onClick={() => onSelect(r.id)}
            className="cursor-pointer border-b-[0.5px] border-[var(--color-border-tertiary)] last:border-b-0 hover:bg-[var(--color-background-secondary)]"
          >
            <td
              className="px-2 py-2 text-[var(--color-text-secondary)]"
              title={new Date(r.created_at).toISOString()}
            >
              {relativeTime(r.created_at)}
            </td>
            <td className="px-2 py-2 font-medium">{r.model}</td>
            <td className="px-2 py-2 text-[var(--color-text-secondary)]">
              {r.schema_name ?? "—"}
            </td>
            <td className="px-2 py-2">
              <div className="flex items-center gap-2">
                <Pill variant={statusVariant(r.status)} size="xs">
                  {titleCase(r.status)}
                </Pill>
                {/* Inline error hint — gives the operator the gist
                    without opening the drawer. Truncated; the full
                    reason + detail live behind the row click. */}
                {r.error_reason && (
                  <span
                    className="max-w-[260px] truncate text-[11px] text-[var(--color-text-danger)]"
                    title={r.error_reason}
                  >
                    {r.error_reason}
                  </span>
                )}
              </div>
            </td>
            <td className="px-2 py-2 text-right">{r.attempt}</td>
            <td className="px-2 py-2 text-right font-medium">
              {SEC(r.elapsed_seconds)}
            </td>
            <td className="px-2 py-2 text-right text-[var(--color-text-secondary)]">
              {r.input_tokens != null && r.output_tokens != null
                ? `${r.input_tokens}→${r.output_tokens}`
                : "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function AgentRunTable({
  rows,
  onSelect,
}: {
  rows: AgentRunRow[];
  onSelect: (id: string) => void;
}) {
  // Memoise loan-id-to-short-form mapping — the table can have lots
  // of rows and slicing strings inside the map runs every render.
  const shorten = useMemo(
    () => (id: string) => id.slice(0, 8),
    [],
  );
  if (rows.length === 0) {
    return (
      <p className="text-[12px] text-[var(--color-text-tertiary)]">
        No agent runs yet in the selected window.
      </p>
    );
  }
  return (
    <table className="w-full text-[12.5px]">
      <thead>
        <tr className="border-b-[0.5px] border-[var(--color-border-tertiary)]">
          {["Time", "Agent", "Status", "Thread", "Loan"].map((h, i) => (
            <th
              key={h}
              className={
                "px-2 py-2 text-[10px] font-medium uppercase tracking-[0.03em] text-[var(--color-text-secondary)] " +
                (i === 0 ? "text-left" : "text-left")
              }
            >
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr
            key={r.id}
            onClick={() => onSelect(r.id)}
            // Whole row is interactive — clicking opens the trace
            // drawer. The Loan-link cell stops propagation so a
            // direct loan click doesn't also open the run drawer.
            className="cursor-pointer border-b-[0.5px] border-[var(--color-border-tertiary)] last:border-b-0 hover:bg-[var(--color-background-secondary)]"
          >
            <td
              className="px-2 py-2 text-[var(--color-text-secondary)]"
              title={new Date(r.created_at).toISOString()}
            >
              {relativeTime(r.created_at)}
            </td>
            <td className="px-2 py-2 font-medium">{r.agent_name}</td>
            <td className="px-2 py-2">
              <Pill variant={statusVariant(r.status)} size="xs">
                {titleCase(r.status)}
              </Pill>
            </td>
            <td className="px-2 py-2 font-mono text-[11px] text-[var(--color-text-secondary)]">
              {shorten(r.thread_id)}…
            </td>
            <td className="px-2 py-2 text-[var(--color-text-info)]">
              <a
                href={`/loans/${r.loan_id}`}
                className="hover:underline"
                onClick={(e) => e.stopPropagation()}
              >
                {shorten(r.loan_id)}…
              </a>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}


// ----- Errors table + drawer ---------------------------------------------


/**
 * Renders the per-error-class rollup on top of a recent-rows table,
 * with a click target into ErrorDrawer.
 */
function ErrorTable({
  classes,
  rows,
  onSelect,
}: {
  classes: ErrorClassStat[];
  rows: InfrastructureErrorRow[];
  onSelect: (id: string) => void;
}) {
  if (rows.length === 0) {
    return (
      <p className="text-[12px] text-[var(--color-text-success)]">
        No server errors in the selected window — healthy.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      {classes.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {classes.map((c) => (
            <span
              key={c.error_class}
              className="inline-flex items-center gap-1 rounded bg-[var(--color-background-secondary)] px-1.5 py-0.5 text-[11px]"
              title={`Last seen ${new Date(c.last_seen).toLocaleString()}`}
            >
              <span className="font-medium text-[var(--color-text-danger)]">
                {c.error_class}
              </span>
              <span className="text-[var(--color-text-tertiary)]">× {c.count}</span>
            </span>
          ))}
        </div>
      )}

      <table className="w-full text-[12.5px]">
        <thead>
          <tr className="border-b-[0.5px] border-[var(--color-border-tertiary)]">
            {["Time", "Method", "Path", "Class", "Message"].map((h) => (
              <th
                key={h}
                className="px-2 py-2 text-left text-[10px] font-medium uppercase tracking-[0.03em] text-[var(--color-text-secondary)]"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={r.id}
              onClick={() => onSelect(r.id)}
              className="cursor-pointer border-b-[0.5px] border-[var(--color-border-tertiary)] hover:bg-[var(--color-background-secondary)] last:border-b-0"
            >
              <td className="px-2 py-2 text-[var(--color-text-secondary)]">
                {relativeTime(r.created_at)}
              </td>
              <td className="px-2 py-2 font-mono text-[11px]">{r.method}</td>
              <td className="px-2 py-2 font-mono text-[11px] text-[var(--color-text-info)]">
                {r.path}
              </td>
              <td className="px-2 py-2 font-medium text-[var(--color-text-danger)]">
                {r.error_class}
              </td>
              <td className="px-2 py-2 truncate text-[var(--color-text-secondary)]">
                {r.error_message}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


/**
 * Drill-in drawer for one error row. Shows the full traceback in a
 * mono pre block plus the path/method/user/request-id forensics.
 * Reuses the same slide-in pattern as LLMCallDrawer and AgentRunDrawer.
 */
function ErrorDrawer({
  errorId,
  onClose,
}: {
  errorId: string | null;
  onClose: () => void;
}) {
  const detailQuery = useQuery({
    queryKey: ["error-detail", errorId],
    queryFn: () => api.getErrorDetail(errorId!),
    enabled: errorId != null,
  });
  const detail = detailQuery.data;

  if (errorId == null) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/30"
      onClick={onClose}
    >
      <div
        className="flex h-full w-full max-w-2xl flex-col overflow-y-auto bg-[var(--color-background-primary)] shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 border-b-[0.5px] border-[var(--color-border-tertiary)] px-5 py-4">
          <div>
            <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-secondary)]">
              Server error
            </p>
            <p className="mt-1 font-mono text-[13px] font-medium text-[var(--color-text-danger)]">
              {detail?.error_class ?? "Loading…"}
            </p>
            {detail && (
              <p className="mt-1 text-[12px] text-[var(--color-text-secondary)]">
                {detail.method} <code className="font-mono">{detail.path}</code>
                {" · "}
                <span className="text-[var(--color-text-tertiary)]">
                  {new Date(detail.created_at).toLocaleString()}
                </span>
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 hover:bg-[var(--color-background-secondary)]"
            aria-label="Close"
          >
            <IconBug size={14} />
          </button>
        </div>

        {detailQuery.isPending ? (
          <div className="flex-1 px-5 py-4">
            <Skeleton width="w-full" height="h-32" />
          </div>
        ) : detail ? (
          <div className="flex flex-col gap-3 px-5 py-4">
            <section>
              <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
                Message
              </p>
              <p className="mt-1 text-[12.5px] text-[var(--color-text-primary)]">
                {detail.error_message}
              </p>
            </section>

            <section className="grid grid-cols-2 gap-3">
              <div>
                <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
                  User
                </p>
                <p className="mt-1 font-mono text-[11.5px]">
                  {detail.user_id ?? (
                    <em className="text-[var(--color-text-tertiary)]">none</em>
                  )}
                </p>
              </div>
              <div>
                <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
                  Request ID
                </p>
                <p className="mt-1 font-mono text-[11.5px]">
                  {detail.request_id ?? (
                    <em className="text-[var(--color-text-tertiary)]">none</em>
                  )}
                </p>
              </div>
            </section>

            {detail.traceback && (
              <section>
                <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
                  Traceback
                </p>
                <pre className="mt-1 max-h-[60vh] overflow-auto rounded-md bg-[var(--color-background-secondary)] px-3 py-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-[var(--color-text-primary)]">
                  {detail.traceback}
                </pre>
              </section>
            )}
          </div>
        ) : (
          <p className="px-5 py-4 text-[12px] text-[var(--color-text-danger)]">
            Couldn&apos;t load detail.
          </p>
        )}
      </div>
    </div>
  );
}
