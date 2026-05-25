"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  IconAlertTriangle,
  IconCheck,
  IconChevronRight,
  IconCircleDashed,
  IconHandStop,
  IconLoader2,
  IconRobot,
} from "@tabler/icons-react";
import { api, type AgentRunRow } from "@/lib/api";
import { titleCase } from "@/lib/humanize";
import { Pill, type PillVariant } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";
import { Skeleton } from "@/app/components/Skeleton";
import { AgentRunDrawer } from "@/app/observability/AgentRunDrawer";
import { LLMCallDrawer } from "@/app/observability/LLMCallDrawer";

interface Props {
  loanId: string;
}

/**
 * Per-loan trace: a chronological list of every agent run that
 * touched this loan. Auditor-facing — the question this answers is
 * "how did this loan get from intake to a decision?" Click any run
 * to open its step-by-step trace in the same drawer the observability
 * page uses.
 *
 * The underlying data is the ``/observability/agents`` endpoint
 * filtered client-side to this loan id. We don't paginate yet — a
 * single loan rarely has more than a handful of runs in practice
 * (one intake, one underwriting, maybe one decision, plus retries).
 * If that ever changes, the right move is a dedicated
 * ``/loans/{id}/agent-runs`` endpoint with server-side filtering.
 */
export function LoanTrace({ loanId }: Props) {
  const [openRunId, setOpenRunId] = useState<string | null>(null);
  const [openCallId, setOpenCallId] = useState<string | null>(null);

  // Pull a generous window — 30 days is long enough that any loan
  // currently in flight will have its full history rendered. The
  // backend caps this internally at 720h (30d) and we don't expect
  // the demo to need more.
  const runsQuery = useQuery({
    queryKey: ["loan-trace", loanId],
    queryFn: () => api.getAgentsObservability(720),
  });

  const runs = useMemo<AgentRunRow[]>(() => {
    if (!runsQuery.data) return [];
    return runsQuery.data.recent
      .filter((r) => r.loan_id === loanId)
      .sort(
        (a, b) =>
          new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      );
  }, [runsQuery.data, loanId]);

  return (
    <div className="flex flex-col gap-3">
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-[13px] font-medium">How this loan got here</p>
            <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
              Every agent run that touched this loan, in order. Click any
              row to see the step-by-step trace, model calls, and where
              the run paused for human approval.
            </p>
          </div>
        </div>

        <div className="mt-4">
          <SectionLabel Icon={IconRobot}>Agent runs</SectionLabel>
          {runsQuery.isPending && (
            <div className="mt-2 flex flex-col gap-2">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} width="w-full" height="h-12" />
              ))}
            </div>
          )}
          {runsQuery.error && (
            <p className="mt-2 text-[12px] text-[var(--color-text-danger)]">
              Couldn&apos;t load runs: {runsQuery.error.message}
            </p>
          )}
          {runsQuery.data && runs.length === 0 && (
            <p className="mt-2 text-[12px] text-[var(--color-text-tertiary)]">
              No agent runs against this loan yet. The intake, underwriting,
              and decision agents each create a run when they execute.
            </p>
          )}
          {runs.length > 0 && (
            <ol className="mt-3 flex flex-col gap-0">
              {runs.map((run, i) => (
                <RunRow
                  key={run.id}
                  run={run}
                  isLast={i === runs.length - 1}
                  onOpen={() => setOpenRunId(run.id)}
                />
              ))}
            </ol>
          )}
        </div>
      </div>

      <AgentRunDrawer
        runId={openRunId}
        onClose={() => setOpenRunId(null)}
        onOpenLLMCall={(id) => {
          setOpenRunId(null);
          setOpenCallId(id);
        }}
      />
      <LLMCallDrawer callId={openCallId} onClose={() => setOpenCallId(null)} />
    </div>
  );
}

function RunRow({
  run,
  isLast,
  onOpen,
}: {
  run: AgentRunRow;
  isLast: boolean;
  onOpen: () => void;
}) {
  const meta = STATUS_META[run.status] ?? STATUS_META.running;
  return (
    <li className="flex gap-2.5 text-[12.5px]">
      <div className="flex flex-col items-center">
        <span
          className="relative mt-[2px] inline-flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full"
          style={{ background: meta.bg, color: meta.color }}
        >
          <meta.Icon size={12} className={meta.animation} />
        </span>
        {!isLast && (
          <span
            className="my-0.5 w-[1.5px] flex-1"
            style={{
              background: "var(--color-brand)",
              opacity: 0.35,
              minHeight: "20px",
            }}
          />
        )}
      </div>

      <button
        type="button"
        onClick={onOpen}
        className="group flex min-w-0 flex-1 items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left hover:bg-[var(--color-background-secondary)]"
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <p className="font-medium text-[var(--color-text-primary)]">
              {titleCase(run.agent_name)} agent
            </p>
            <Pill variant={runStatusVariant(run.status)} size="xs">
              {titleCase(run.status)}
            </Pill>
          </div>
          <p
            className="mt-0.5 text-[11px] text-[var(--color-text-tertiary)]"
            title={new Date(run.created_at).toISOString()}
          >
            {new Date(run.created_at).toLocaleString(undefined, {
              dateStyle: "medium",
              timeStyle: "short",
            })}
            {/* Thread id moves into the hover tooltip + the drawer
                detail view. On the trace tab it was rendering as a
                monospaced ``intake-<uuid>`` string that signalled
                "internal token" without giving the auditor anything
                they could act on. The drawer (one click away) still
                has the full id when it's needed. */}
          </p>
        </div>
        <IconChevronRight
          size={14}
          className="text-[var(--color-text-tertiary)] group-hover:text-[var(--color-text-primary)]"
        />
      </button>
    </li>
  );
}

function runStatusVariant(status: string): PillVariant {
  if (status === "complete") return "success";
  if (status === "interrupted") return "info";
  if (status === "skipped") return "neutral";
  if (status === "failed") return "danger";
  return "warn";
}

const STATUS_META: Record<
  string,
  {
    Icon: React.ComponentType<{ size?: number; className?: string }>;
    color: string;
    bg: string;
    animation: string;
  }
> = {
  complete: {
    Icon: IconCheck,
    color: "var(--color-brand)",
    bg: "var(--color-background-success)",
    animation: "",
  },
  interrupted: {
    Icon: IconHandStop,
    color: "var(--color-text-info)",
    bg: "var(--color-background-info)",
    animation: "",
  },
  skipped: {
    Icon: IconCircleDashed,
    color: "var(--color-text-tertiary)",
    bg: "var(--color-background-secondary)",
    animation: "",
  },
  failed: {
    Icon: IconAlertTriangle,
    color: "var(--color-text-danger)",
    bg: "var(--color-background-danger)",
    animation: "",
  },
  running: {
    Icon: IconLoader2,
    color: "var(--color-text-info)",
    bg: "var(--color-background-info)",
    animation: "animate-spin",
  },
};
