import {
  IconAlertTriangle,
  IconCheck,
  IconChevronDown,
  IconChevronRight,
  IconCircle,
  IconCircleDashed,
  IconInfoCircle,
  IconLoader2,
} from "@tabler/icons-react";
import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";
import type { AgentNode } from "@/lib/useAgentRun";

interface Props {
  nodes: AgentNode[];
  /** Header label — "Intake agent" / "Underwriting" / "Decision". */
  title: string;
  /** Last summary line from the terminal "done" event. Renders muted
   *  under the trail when the run has finished cleanly. */
  doneSummary?: string;
  /** If the stream surfaced an error, render it as the last item. */
  error?: string | null;
  /** Verbose technical detail (stack-trace gist, raw API message) shown
   *  behind a "Show details" toggle so the friendly ``error`` stays
   *  uncluttered but operators can still get to the gory bits. */
  errorDetail?: string | null;
  /** Human-readable reason the run short-circuited at a pre-flight gate
   *  (e.g. "No documents have been uploaded yet."). Renders as a calm
   *  info banner — this is the system politely declining to spend
   *  tokens, not a failure. */
  skipReason?: string | null;
}

/**
 * Live progress trail for one agent run.
 *
 * The agent runs through a known sequence of LangGraph nodes, and each
 * node fires a Server-Sent Event when it completes. We render the
 * sequence vertically — done nodes get a green check, the running node
 * spins, queued nodes show as a dashed ring, skipped nodes go muted.
 *
 * The connecting vertical rail between dots gives the trail a "this
 * is one continuous run" reading rather than a list of independent
 * steps. Per-step elapsed time lands next to the summary as soon as
 * the step completes, so the user can see "extraction took 2.4s,
 * drafting took 8.1s" — useful both as observability and as a sense
 * of "the agent is making progress." A header-right total counter
 * ticks while the run is live.
 *
 * This is the "visualization" the design doc calls for: every action
 * the agent takes is visible in the same surface as the audit log, in
 * real time, with the same vocabulary that's used elsewhere in the UI.
 */
export function AgentProgress({
  nodes,
  title,
  doneSummary,
  error,
  errorDetail,
  skipReason,
}: Props) {
  // "Show details" toggle for the technical error blurb — collapsed by
  // default because most operators only need the friendly headline; the
  // detail is for "why exactly did the LLM call fail?" forensics.
  const [showDetail, setShowDetail] = useState(false);
  if (nodes.length === 0) return null;

  const doneCount = nodes.filter((n) => n.status === "done").length;
  const activeIdx = nodes.findIndex((n) => n.status === "active");
  const isRunning = activeIdx !== -1;
  const hasFailed = nodes.some((n) => n.status === "failed");
  const totalElapsedMs = nodes.reduce(
    (sum, n) => sum + (n.elapsedMs ?? 0),
    0,
  );
  const allDoneOrSkipped =
    !isRunning &&
    !hasFailed &&
    nodes.every((n) => n.status === "done" || n.status === "skipped");

  return (
    <motion.div
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -4 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3"
    >
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="flex items-center gap-1.5 text-[12px] font-medium text-[var(--color-text-secondary)]">
          <span
            className="inline-flex h-4 w-4 items-center justify-center rounded-full"
            style={{
              background: hasFailed
                ? "var(--color-background-danger)"
                : allDoneOrSkipped
                  ? "var(--color-background-secondary)"
                  : "var(--color-background-success)",
              color: hasFailed
                ? "var(--color-text-danger)"
                : allDoneOrSkipped
                  ? "var(--color-text-secondary)"
                  : "var(--color-brand)",
            }}
          >
            {hasFailed ? (
              <IconAlertTriangle size={10} />
            ) : allDoneOrSkipped ? (
              <IconCheck size={10} />
            ) : (
              <IconLoader2 size={10} className="animate-spin" />
            )}
          </span>
          {title}
        </p>
        <span className="text-[11px] tabular-nums text-[var(--color-text-tertiary)]">
          {doneCount} / {nodes.length} steps ·{" "}
          {totalElapsedMs > 0 ? fmtElapsed(totalElapsedMs) : "—"}
        </span>
      </div>
      {/* The trail. The vertical rail behind the icons gives the
          impression of one continuous run; the rail color shifts to
          brand-green for completed segments and stays muted for the
          pending ones. */}
      <ol className="relative flex flex-col gap-0">
        <AnimatePresence initial={false}>
          {nodes.map((n, i) => (
            <motion.div
              key={n.key}
              layout
              transition={{ layout: { duration: 0.2, ease: "easeOut" } }}
            >
              <Step
                node={n}
                isLast={i === nodes.length - 1}
                isSegmentDone={
                  // The rail segment leading INTO this node is "done"
                  // when the previous node is done or this node itself
                  // is active/done.
                  i === 0 ||
                  nodes[i - 1]!.status === "done" ||
                  n.status === "done" ||
                  n.status === "active"
                }
              />
            </motion.div>
          ))}
        </AnimatePresence>
      </ol>
      {doneSummary && !skipReason && (
        <p className="mt-3 text-[11px] text-[var(--color-text-tertiary)]">
          {doneSummary}
        </p>
      )}
      {/* Skip banner — calm info colour, not danger. The run was a
          polite no-op (pre-flight gate fired before any LLM call), so
          the visual tone should match "FYI" not "broken". */}
      {skipReason && (
        <div
          className="mt-3 flex items-start gap-2 rounded bg-[var(--color-background-info)] px-3 py-2 text-[11.5px] text-[var(--color-text-info)]"
          role="status"
        >
          <IconInfoCircle size={13} className="mt-[1px] shrink-0" />
          <span className="leading-snug">{skipReason}</span>
        </div>
      )}
      {/* Failure banner. Friendly headline always visible; the gory
          technical detail (stack-trace gist, raw API message) is
          tucked behind a "Show details" toggle so the surface stays
          calm but the operator can still get to it. */}
      {error && (
        <div className="mt-3 rounded bg-[var(--color-background-danger)] px-3 py-2 text-[11.5px] text-[var(--color-text-danger)]">
          <div className="flex items-start gap-2">
            <IconAlertTriangle size={13} className="mt-[1px] shrink-0" />
            <span className="flex-1 leading-snug">{error}</span>
          </div>
          {errorDetail && (
            <div className="mt-2 pl-[19px]">
              <button
                type="button"
                onClick={() => setShowDetail((v) => !v)}
                className="inline-flex items-center gap-1 text-[10.5px] uppercase tracking-wider text-[var(--color-text-danger)] opacity-80 hover:opacity-100"
              >
                {showDetail ? (
                  <IconChevronDown size={11} />
                ) : (
                  <IconChevronRight size={11} />
                )}
                {showDetail ? "Hide details" : "Show details"}
              </button>
              {showDetail && (
                <pre className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-[var(--color-background-primary)] p-2 text-[10.5px] leading-snug text-[var(--color-text-secondary)]">
                  {errorDetail}
                </pre>
              )}
            </div>
          )}
        </div>
      )}
    </motion.div>
  );
}

function Step({
  node,
  isLast,
  isSegmentDone,
}: {
  node: AgentNode;
  isLast: boolean;
  isSegmentDone: boolean;
}) {
  const { status, label, summary, elapsedMs } = node;
  const s = STATE_STYLE[status];
  return (
    <li className="flex gap-2.5 text-[12.5px]">
      {/* Rail + dot column. The dot is the icon; the rail is the
          short vertical line below it that connects to the next step.
          Hidden on the last step. */}
      <div className="flex flex-col items-center">
        <span
          className="relative mt-[2px] inline-flex h-[20px] w-[20px] shrink-0 items-center justify-center rounded-full"
          style={{ background: s.bg, color: s.color }}
        >
          <s.Icon size={11} className={s.animation} />
          {/* A subtle pulse ring for the active step. Pure CSS to keep
              animation cheap; pauses on prefers-reduced-motion. */}
          {status === "active" && (
            <span
              aria-hidden="true"
              className="absolute inset-0 rounded-full"
              style={{
                boxShadow: "0 0 0 0 var(--color-text-info)",
                animation:
                  "mkopo-agent-pulse 1.6s cubic-bezier(0.4, 0, 0.6, 1) infinite",
              }}
            />
          )}
        </span>
        {!isLast && (
          <span
            className="my-0.5 w-[1.5px] flex-1"
            style={{
              background: isSegmentDone
                ? "var(--color-brand)"
                : "var(--color-border-tertiary)",
              opacity: isSegmentDone ? 0.5 : 1,
              minHeight: "16px",
            }}
          />
        )}
      </div>

      {/* Label + summary + timing column. */}
      <div className="min-w-0 flex-1 pb-3">
        <div className="flex items-baseline justify-between gap-2">
          <p style={{ color: s.labelColor }}>{label}</p>
          {elapsedMs != null && (
            <span className="text-[10.5px] tabular-nums text-[var(--color-text-tertiary)]">
              {fmtElapsed(elapsedMs)}
            </span>
          )}
        </div>
        {summary && (
          <p
            className="mt-0.5 text-[11px] leading-snug"
            style={{ color: s.summaryColor }}
          >
            {summary}
          </p>
        )}
      </div>
    </li>
  );
}

const STATE_STYLE: Record<
  AgentNode["status"],
  {
    Icon: React.ComponentType<{ size?: number; className?: string }>;
    color: string;
    bg: string;
    animation: string;
    labelColor: string;
    summaryColor: string;
  }
> = {
  done: {
    Icon: IconCheck,
    color: "var(--color-brand)",
    bg: "var(--color-background-success)",
    animation: "",
    labelColor: "var(--color-text-primary)",
    summaryColor: "var(--color-text-secondary)",
  },
  active: {
    Icon: IconLoader2,
    color: "var(--color-text-info)",
    bg: "var(--color-background-info)",
    animation: "animate-spin",
    labelColor: "var(--color-text-primary)",
    summaryColor: "var(--color-text-secondary)",
  },
  pending: {
    Icon: IconCircleDashed,
    color: "var(--color-text-tertiary)",
    bg: "var(--color-background-secondary)",
    animation: "",
    labelColor: "var(--color-text-tertiary)",
    summaryColor: "var(--color-text-tertiary)",
  },
  skipped: {
    Icon: IconCircle,
    color: "var(--color-text-tertiary)",
    bg: "var(--color-background-secondary)",
    animation: "",
    labelColor: "var(--color-text-tertiary)",
    summaryColor: "var(--color-text-tertiary)",
  },
  failed: {
    Icon: IconAlertTriangle,
    color: "var(--color-text-danger)",
    bg: "var(--color-background-danger)",
    animation: "",
    labelColor: "var(--color-text-primary)",
    summaryColor: "var(--color-text-danger)",
  },
};

/** Friendly elapsed formatter — milliseconds for sub-second, seconds
 *  with one decimal otherwise. */
function fmtElapsed(ms: number): string {
  if (ms < 1000) return `${Math.max(1, Math.round(ms))} ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}
