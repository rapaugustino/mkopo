"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  IconAlertTriangle,
  IconArrowRight,
  IconBolt,
  IconCheck,
  IconChevronDown,
  IconChevronRight,
  IconCircleDashed,
  IconHandStop,
  IconRefresh,
  IconX,
} from "@tabler/icons-react";
import { toast } from "sonner";
import { AnimatePresence, motion } from "motion/react";
import { api, type AgentRunDetail, type AgentStepRow, type LLMCallRow } from "@/lib/api";
import { titleCase } from "@/lib/humanize";
import { AnnotationPanel } from "@/app/components/AnnotationPanel";
import { IconButton } from "@/app/components/IconButton";
import { Pill, type PillVariant } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";

interface Props {
  runId: string | null;
  onClose: () => void;
  /** Opening an LLM call from inside this drawer hands the id back up
   *  to the parent page so it can swap to the LLM-call drawer. */
  onOpenLLMCall?: (callId: string) => void;
}

/**
 * Drill-in drawer for one agent run.
 *
 * Renders the step-by-step trace the auditor needs: which LangGraph
 * nodes ran, in what order, for how long, what they produced. Each
 * step expands to show its full payload (small, PII-safe — extracted
 * field counts, missing-fields lists, rule-outcome counts).
 *
 * Below the trail is the **LLM calls** table for the same thread —
 * every model call this run issued, with status + latency. Clicking
 * one hands the id up to the parent so the LLM-call drawer can open
 * with the full prompt-hash forensics.
 *
 * This is the unit of explainability the loan-origination use case
 * needs: "what did the intake agent do on loan X at 14:32, what was
 * the LLM doing, and where did the run pause for human approval?"
 */
export function AgentRunDrawer({ runId, onClose, onOpenLLMCall }: Props) {
  const detailQuery = useQuery<AgentRunDetail, Error>({
    queryKey: ["agent-run-detail", runId],
    queryFn: () => api.getAgentRunDetail(runId!),
    enabled: runId != null,
  });

  return (
    <AnimatePresence>
      {runId && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.14 }}
          className="fixed inset-0 z-40 bg-black/30"
          onClick={onClose}
          role="dialog"
          aria-modal="true"
          aria-label="Agent run details"
        >
          <motion.aside
            initial={{ x: 24, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: 24, opacity: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            onClick={(e) => e.stopPropagation()}
            className="absolute right-0 top-0 flex h-full w-full max-w-[720px] flex-col overflow-hidden bg-[var(--color-background-primary)] shadow-2xl"
          >
            <header className="flex items-start justify-between gap-3 border-b-[0.5px] border-[var(--color-border-tertiary)] px-5 py-4">
              <div>
                <p className="text-[11px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                  Agent run
                </p>
                {detailQuery.data && (
                  <p className="mt-0.5 text-[14px] font-medium tracking-tight">
                    {titleCase(detailQuery.data.agent_name)}
                    <span className="ml-2 font-mono text-[11px] font-normal text-[var(--color-text-secondary)]">
                      {detailQuery.data.thread_id}
                    </span>
                  </p>
                )}
              </div>
              <IconButton label="Close" Icon={IconX} onClick={onClose} />
            </header>

            <div className="flex-1 overflow-y-auto px-5 py-4">
              {detailQuery.isPending && (
                <p className="text-[12px] text-[var(--color-text-tertiary)]">
                  Loading agent trace…
                </p>
              )}
              {detailQuery.error && (
                <p className="text-[12px] text-[var(--color-text-danger)]">
                  Couldn&apos;t load trace: {detailQuery.error.message}
                </p>
              )}
              {detailQuery.data && (
                <Body detail={detailQuery.data} onOpenLLMCall={onOpenLLMCall} />
              )}
            </div>
          </motion.aside>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

// ---- body ---------------------------------------------------------------

function Body({
  detail,
  onOpenLLMCall,
}: {
  detail: AgentRunDetail;
  onOpenLLMCall?: (callId: string) => void;
}) {
  // Group LLM calls under their owning step using parent_step_id,
  // backfilled server-side by the streaming layer's _persist_step.
  // Calls without a parent (ad-hoc, pre-backfill, or somehow
  // orphaned) fall through to the "Unattributed calls" section
  // below — better than silently dropping them.
  const callsByStep = new Map<string, LLMCallRow[]>();
  const orphanCalls: LLMCallRow[] = [];
  for (const c of detail.llm_calls) {
    if (c.parent_step_id) {
      const bucket = callsByStep.get(c.parent_step_id) ?? [];
      bucket.push(c);
      callsByStep.set(c.parent_step_id, bucket);
    } else {
      orphanCalls.push(c);
    }
  }
  // Replay link, when this run is itself a replay of something
  // earlier. Renders inline in the header so an auditor can navigate
  // back to the original to compare side-by-side via the regression
  // diff (task 6d).
  const replaysRunId =
    typeof detail.payload?.replays_run_id === "string"
      ? detail.payload.replays_run_id
      : null;

  return (
    <div className="flex flex-col gap-5">
      <RunHeader detail={detail} replaysRunId={replaysRunId} />
      {/* Annotations sit above the step trace so the verdict
          buttons are visible the moment the drawer opens — the
          common "I'm reviewing this failed run" workflow shouldn't
          require scrolling past the trace to record a verdict. */}
      <AnnotationPanel targetKind="agent_run" targetId={detail.id} />
      <ReplayBar detail={detail} />
      <StepsTrace
        steps={detail.steps}
        callsByStep={callsByStep}
        onOpenLLMCall={onOpenLLMCall}
      />
      <LLMCallList
        calls={orphanCalls}
        onOpenLLMCall={onOpenLLMCall}
        title={
          callsByStep.size > 0
            ? "Unattributed LLM calls"
            : "LLM calls"
        }
      />
      <RunPayload payload={detail.payload} />
    </div>
  );
}


/** Compact replay control. Kicks off the same agent against the same
 *  loan, marks the new run as ``replays_run_id=<original>`` so the
 *  drawer can later show the back-link, and toasts the user.
 *
 *  The new run runs in the background; the user navigates to the
 *  observability page (or the loan's case file) to watch it. We
 *  invalidate the recent-runs list 1 second after triggering so the
 *  new row appears without a manual refresh.
 */
function ReplayBar({ detail }: { detail: AgentRunDetail }) {
  const queryClient = useQueryClient();

  const replay = useMutation({
    mutationFn: async () => {
      const url = `${
        process.env.NEXT_PUBLIC_API_URL ?? ""
      }/api/v1/loans/${detail.loan_id}/agents/${detail.agent_name}/run?replays_run_id=${detail.id}`;
      // Fire-and-(mostly-)forget: open the SSE stream so the backend
      // actually starts running, but discard the events. The drawer
      // isn't a streaming UI — the user can switch to the loan page
      // or observability to watch the new run live.
      const res = await fetch(url, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      // Drain the stream in the background so connection isn't
      // held by the browser forever. We don't render the events.
      void (async () => {
        try {
          const reader = res.body?.getReader();
          if (!reader) return;
          while (true) {
            const { done } = await reader.read();
            if (done) break;
          }
        } catch {
          // Network drop is fine — the agent run has its own
          // lifecycle on the server.
        }
      })();
    },
    onSuccess: () => {
      toast.success("Replay started", {
        description: `Running ${detail.agent_name} again — new run will appear in observability shortly.`,
        duration: 6_000,
      });
      // Give the backend a moment to write the AgentRun row, then
      // refresh the recent-runs query.
      setTimeout(() => {
        queryClient.invalidateQueries({
          queryKey: ["observability", "agents"],
        });
      }, 1_500);
    },
    onError: (e) =>
      toast.error("Couldn't start replay", {
        description: e instanceof Error ? e.message : String(e),
      }),
  });

  return (
    <div className="flex items-center justify-between gap-3 rounded-md bg-[var(--color-background-secondary)] px-3 py-2.5">
      <p className="text-[11.5px] text-[var(--color-text-secondary)]">
        Replay this run with the current code + active prompts. The
        original stays unchanged; the new run records a back-link.
      </p>
      <button
        type="button"
        onClick={() => replay.mutate()}
        disabled={replay.isPending}
        className="inline-flex items-center gap-1 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2.5 py-1 text-[11.5px] font-medium hover:bg-[var(--color-background-tertiary)] disabled:opacity-50"
      >
        <IconRefresh size={11} />
        {replay.isPending ? "Starting…" : "Replay"}
      </button>
    </div>
  );
}

function RunHeader({
  detail,
  replaysRunId,
}: {
  detail: AgentRunDetail;
  replaysRunId: string | null;
}) {
  return (
    <section className="grid grid-cols-4 gap-3 rounded-md bg-[var(--color-background-secondary)] px-4 py-3">
      <Meta label="Status">
        <Pill variant={runStatusVariant(detail.status)} size="sm">
          {titleCase(detail.status)}
        </Pill>
      </Meta>
      <Meta label="When">
        <time
          className="text-[12.5px]"
          title={new Date(detail.created_at).toISOString()}
        >
          {new Date(detail.created_at).toLocaleString(undefined, {
            dateStyle: "medium",
            timeStyle: "short",
          })}
        </time>
      </Meta>
      <Meta label="Loan">
        <Link
          href={`/loans/${detail.loan_id}`}
          className="inline-flex items-center gap-1 text-[12.5px] text-[var(--color-text-info)] hover:underline"
        >
          Open
          <IconArrowRight size={11} />
        </Link>
      </Meta>
      {replaysRunId && (
        // Back-link to the run this one was replayed from. Useful for
        // the side-by-side flow: open the original, compare its
        // steps + LLM calls to this one.
        <Meta label="Replays">
          {/* Cross-link to the original run's drawer. Same observability
              page, ``?run=<id>`` opens the drawer for that id. Was
              previously ``href="#"`` (dead) — this is a real route
              now so the back-link comparison flow works. */}
          <Link
            href={`/observability?run=${replaysRunId}`}
            className="inline-flex items-center gap-1 text-[12.5px] text-[var(--color-text-info)] hover:underline"
            title={`Original run id ${replaysRunId}`}
          >
            run {replaysRunId.slice(0, 8)}…
            <IconArrowRight size={11} />
          </Link>
        </Meta>
      )}
    </section>
  );
}

function StepsTrace({
  steps,
  callsByStep,
  onOpenLLMCall,
}: {
  steps: AgentStepRow[];
  callsByStep: Map<string, LLMCallRow[]>;
  onOpenLLMCall?: (callId: string) => void;
}) {
  return (
    <section>
      <SectionLabel>
        Step trace
        <span className="ml-1 font-normal text-[var(--color-text-tertiary)]">
          · {steps.length} {steps.length === 1 ? "step" : "steps"}
        </span>
      </SectionLabel>
      {steps.length === 0 ? (
        <p className="mt-2 text-[12px] text-[var(--color-text-tertiary)]">
          No steps persisted. The run either hadn&apos;t reached a node
          before completing, or the persistence layer was disabled.
        </p>
      ) : (
        <ol className="mt-2 flex flex-col gap-0">
          {steps.map((step, i) => (
            <StepRow
              key={step.id}
              step={step}
              isLast={i === steps.length - 1}
              calls={callsByStep.get(step.id) ?? []}
              onOpenLLMCall={onOpenLLMCall}
            />
          ))}
        </ol>
      )}
    </section>
  );
}

function StepRow({
  step,
  isLast,
  calls,
  onOpenLLMCall,
}: {
  step: AgentStepRow;
  isLast: boolean;
  calls: LLMCallRow[];
  onOpenLLMCall?: (callId: string) => void;
}) {
  // Auto-open the nested view when the step has child calls — the
  // common case is "I want to see what the LLM said", so hiding
  // them by default would force a click on every step. Steps with
  // no calls and no payload stay collapsed.
  const [open, setOpen] = useState(calls.length > 0);
  const meta = STEP_META[step.status] ?? STEP_META.ok;
  const hasPayload = Object.keys(step.payload).length > 0;
  const hasChildren = calls.length > 0 || hasPayload;
  return (
    <li className="flex gap-2.5 text-[12.5px]">
      {/* Rail + icon column */}
      <div className="flex flex-col items-center">
        <span
          className="relative mt-[2px] inline-flex h-[20px] w-[20px] shrink-0 items-center justify-center rounded-full"
          style={{ background: meta.bg, color: meta.color }}
        >
          <meta.Icon size={11} />
        </span>
        {!isLast && (
          <span
            className="my-0.5 w-[1.5px] flex-1"
            style={{
              background:
                meta.color === "var(--color-text-danger)"
                  ? "var(--color-text-danger)"
                  : "var(--color-brand)",
              opacity: 0.4,
              minHeight: "16px",
            }}
          />
        )}
      </div>

      {/* Content column */}
      <div className="min-w-0 flex-1 pb-3">
        <button
          type="button"
          onClick={() => hasChildren && setOpen((v) => !v)}
          className={
            "flex w-full items-baseline justify-between gap-2 text-left " +
            (hasChildren ? "cursor-pointer" : "cursor-default")
          }
        >
          <span className="flex items-center gap-1.5 font-medium text-[var(--color-text-primary)]">
            {hasChildren && (
              <span className="text-[var(--color-text-tertiary)]">
                {open ? <IconChevronDown size={11} /> : <IconChevronRight size={11} />}
              </span>
            )}
            {step.node}
            <span className="ml-1 text-[10.5px] font-normal text-[var(--color-text-tertiary)]">
              {titleCase(step.status)}
            </span>
            {/* Inline child-call counter — visible without expanding
                so the operator can tell at a glance which steps had
                LLM activity. */}
            {calls.length > 0 && (
              <span className="ml-1 inline-flex items-center gap-1 rounded bg-[var(--color-background-secondary)] px-1.5 py-0.5 text-[10px] font-normal normal-case tracking-normal text-[var(--color-text-secondary)]">
                {calls.length} call{calls.length === 1 ? "" : "s"}
              </span>
            )}
          </span>
          {step.elapsed_ms != null && (
            <span className="text-[10.5px] tabular-nums text-[var(--color-text-tertiary)]">
              {fmtElapsed(step.elapsed_ms)}
            </span>
          )}
        </button>
        {step.summary && (
          <p
            className="mt-0.5 text-[11.5px] leading-snug"
            style={{ color: meta.summaryColor }}
          >
            {step.summary}
          </p>
        )}
        {open && calls.length > 0 && (
          <div className="mt-2 flex flex-col gap-1">
            {calls.map((c) => (
              <NestedCallRow
                key={c.id}
                call={c}
                onOpen={() => onOpenLLMCall?.(c.id)}
              />
            ))}
          </div>
        )}
        {open && hasPayload && (
          <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded bg-[var(--color-background-secondary)] p-2 text-[10.5px] leading-snug text-[var(--color-text-secondary)]">
            {JSON.stringify(step.payload, null, 2)}
          </pre>
        )}
      </div>
    </li>
  );
}


/** Compact LLM call row used when the call is rendered nested under
 *  its owning step. Keeps the chrome lighter than the standalone
 *  ``LLMCallList`` rows so the visual hierarchy reads (step →
 *  calls) rather than (step :: calls).
 */
function NestedCallRow({
  call,
  onOpen,
}: {
  call: LLMCallRow;
  onOpen: () => void;
}) {
  const variant: PillVariant =
    call.status === "ok"
      ? "success"
      : call.status === "schema_failed" || call.status === "error"
        ? "danger"
        : "warn";
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex items-center gap-2 rounded-md bg-[var(--color-background-secondary)] px-2.5 py-1.5 text-left text-[11.5px] hover:bg-[var(--color-background-tertiary)]"
    >
      <Pill variant={variant} size="sm">
        {call.status}
      </Pill>
      <span className="font-medium text-[var(--color-text-primary)]">
        {call.model}
      </span>
      {call.schema_name && (
        <span className="text-[10.5px] text-[var(--color-text-tertiary)]">
          → {call.schema_name}
        </span>
      )}
      <span className="ml-auto text-[10.5px] tabular-nums text-[var(--color-text-tertiary)]">
        {call.elapsed_seconds.toFixed(2)}s
        {(call.input_tokens || call.output_tokens) != null && (
          <>
            {" · "}
            {call.input_tokens ?? 0}/{call.output_tokens ?? 0} tok
          </>
        )}
      </span>
    </button>
  );
}

function LLMCallList({
  calls,
  onOpenLLMCall,
  title = "LLM calls",
}: {
  calls: LLMCallRow[];
  onOpenLLMCall?: (callId: string) => void;
  /** When the run has nested calls under steps, this list shows
   *  only the orphans — relabel so the operator knows what they're
   *  looking at. */
  title?: string;
}) {
  return (
    <section>
      <SectionLabel>
        {title}
        <span className="ml-1 font-normal text-[var(--color-text-tertiary)]">
          · {calls.length}
        </span>
      </SectionLabel>
      {calls.length === 0 ? (
        <p className="mt-2 text-[12px] text-[var(--color-text-tertiary)]">
          {title === "LLM calls"
            ? "No LLM calls recorded for this run. (Either the run skipped at a pre-flight gate before any LLM call, or it ran entirely through deterministic nodes.)"
            : "Every LLM call in this run is attributed to a step above."}
        </p>
      ) : (
        <table className="mt-2 w-full text-[12px]">
          <thead>
            <tr className="border-b-[0.5px] border-[var(--color-border-tertiary)]">
              {["Time", "Model", "Schema", "Status", "Latency"].map((h, i) => (
                <th
                  key={h}
                  className={
                    "py-1.5 text-[10px] font-medium uppercase tracking-[0.03em] text-[var(--color-text-secondary)] " +
                    (i === 0 ? "pl-1 text-left" : i === 4 ? "pr-1 text-right" : "px-1 text-left")
                  }
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {calls.map((c) => (
              <tr
                key={c.id}
                onClick={() => onOpenLLMCall?.(c.id)}
                className={
                  "border-b-[0.5px] border-[var(--color-border-tertiary)] last:border-b-0 " +
                  (onOpenLLMCall ? "cursor-pointer hover:bg-[var(--color-background-secondary)]" : "")
                }
              >
                <td
                  className="py-1.5 pl-1 text-[var(--color-text-secondary)]"
                  title={new Date(c.created_at).toISOString()}
                >
                  {new Date(c.created_at).toLocaleTimeString(undefined, {
                    hour: "2-digit",
                    minute: "2-digit",
                    second: "2-digit",
                  })}
                </td>
                <td className="px-1 py-1.5 font-medium">{c.model}</td>
                <td className="px-1 py-1.5 text-[var(--color-text-secondary)]">
                  {c.schema_name ?? "—"}
                </td>
                <td className="px-1 py-1.5">
                  <Pill variant={llmStatusVariant(c.status)} size="xs">
                    {titleCase(c.status)}
                  </Pill>
                </td>
                <td className="pr-1 text-right tabular-nums">
                  {c.elapsed_seconds.toFixed(2)}s
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function RunPayload({ payload }: { payload: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  if (Object.keys(payload).length === 0) return null;
  return (
    <section>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 text-[10.5px] uppercase tracking-wider text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
      >
        {open ? <IconChevronDown size={11} /> : <IconChevronRight size={11} />}
        Run payload
      </button>
      {open && (
        <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded bg-[var(--color-background-secondary)] p-3 text-[10.5px] leading-snug text-[var(--color-text-secondary)]">
          {JSON.stringify(payload, null, 2)}
        </pre>
      )}
    </section>
  );
}

// ---- helpers ------------------------------------------------------------

function Meta({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
        {label}
      </span>
      <span className="text-[var(--color-text-primary)]">{children}</span>
    </div>
  );
}

function runStatusVariant(status: string): PillVariant {
  if (status === "complete") return "success";
  if (status === "interrupted") return "info";
  if (status === "skipped") return "neutral";
  if (status === "failed") return "danger";
  if (status === "running") return "warn";
  return "neutral";
}

function llmStatusVariant(status: string): PillVariant {
  if (status === "ok") return "success";
  if (status === "schema_failed" || status === "error") return "danger";
  return "warn";
}

const STEP_META: Record<
  string,
  {
    Icon: React.ComponentType<{ size?: number }>;
    color: string;
    bg: string;
    summaryColor: string;
  }
> = {
  ok: {
    Icon: IconCheck,
    color: "var(--color-brand)",
    bg: "var(--color-background-success)",
    summaryColor: "var(--color-text-secondary)",
  },
  skipped: {
    Icon: IconCircleDashed,
    color: "var(--color-text-tertiary)",
    bg: "var(--color-background-secondary)",
    summaryColor: "var(--color-text-tertiary)",
  },
  interrupt: {
    Icon: IconHandStop,
    color: "var(--color-text-info)",
    bg: "var(--color-background-info)",
    summaryColor: "var(--color-text-info)",
  },
  failed: {
    Icon: IconAlertTriangle,
    color: "var(--color-text-danger)",
    bg: "var(--color-background-danger)",
    summaryColor: "var(--color-text-danger)",
  },
};

// Reuse the brand bolt for LLM-call rows, which currently isn't used
// anywhere visible here but keeps the import tree honest.
void IconBolt;

function fmtElapsed(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}
