"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  IconAlertTriangle,
  IconArrowRight,
  IconBolt,
  IconCheck,
  IconChevronDown,
  IconChevronRight,
  IconCircleDashed,
  IconHandStop,
  IconX,
} from "@tabler/icons-react";
import { AnimatePresence, motion } from "motion/react";
import { api, type AgentRunDetail, type AgentStepRow, type LLMCallRow } from "@/lib/api";
import { titleCase } from "@/lib/humanize";
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
  return (
    <div className="flex flex-col gap-5">
      <RunHeader detail={detail} />
      <StepsTrace steps={detail.steps} />
      <LLMCallList calls={detail.llm_calls} onOpenLLMCall={onOpenLLMCall} />
      <RunPayload payload={detail.payload} />
    </div>
  );
}

function RunHeader({ detail }: { detail: AgentRunDetail }) {
  return (
    <section className="grid grid-cols-3 gap-3 rounded-md bg-[var(--color-background-secondary)] px-4 py-3">
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
    </section>
  );
}

function StepsTrace({ steps }: { steps: AgentStepRow[] }) {
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
            <StepRow key={step.id} step={step} isLast={i === steps.length - 1} />
          ))}
        </ol>
      )}
    </section>
  );
}

function StepRow({ step, isLast }: { step: AgentStepRow; isLast: boolean }) {
  const [open, setOpen] = useState(false);
  const meta = STEP_META[step.status] ?? STEP_META.ok;
  const hasPayload = Object.keys(step.payload).length > 0;
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
          onClick={() => hasPayload && setOpen((v) => !v)}
          className={
            "flex w-full items-baseline justify-between gap-2 text-left " +
            (hasPayload ? "cursor-pointer" : "cursor-default")
          }
        >
          <span className="flex items-center gap-1.5 font-medium text-[var(--color-text-primary)]">
            {hasPayload && (
              <span className="text-[var(--color-text-tertiary)]">
                {open ? <IconChevronDown size={11} /> : <IconChevronRight size={11} />}
              </span>
            )}
            {step.node}
            <span className="ml-1 text-[10.5px] font-normal text-[var(--color-text-tertiary)]">
              {titleCase(step.status)}
            </span>
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
        {open && hasPayload && (
          <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded bg-[var(--color-background-secondary)] p-2 text-[10.5px] leading-snug text-[var(--color-text-secondary)]">
            {JSON.stringify(step.payload, null, 2)}
          </pre>
        )}
      </div>
    </li>
  );
}

function LLMCallList({
  calls,
  onOpenLLMCall,
}: {
  calls: LLMCallRow[];
  onOpenLLMCall?: (callId: string) => void;
}) {
  return (
    <section>
      <SectionLabel>
        LLM calls
        <span className="ml-1 font-normal text-[var(--color-text-tertiary)]">
          · {calls.length}
        </span>
      </SectionLabel>
      {calls.length === 0 ? (
        <p className="mt-2 text-[12px] text-[var(--color-text-tertiary)]">
          No LLM calls recorded for this run. (Either the run skipped at
          a pre-flight gate before any LLM call, or it ran entirely
          through deterministic nodes.)
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
