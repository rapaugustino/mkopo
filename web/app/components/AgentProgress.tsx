import {
  IconCheck,
  IconCircle,
  IconCircleDashed,
  IconLoader2,
} from "@tabler/icons-react";
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
}

/**
 * Live progress trail for one agent run.
 *
 * The agent runs through a known sequence of LangGraph nodes, and each
 * node fires a Server-Sent Event when it completes. We render the
 * sequence vertically — done nodes get a green check, the running node
 * spins, queued nodes show as a dashed ring, skipped nodes go muted.
 *
 * This is the "visualization" the design doc calls for: every action
 * the agent takes is visible in the same surface as the audit log, in
 * real time, with the same vocabulary that's used elsewhere in the UI.
 */
export function AgentProgress({ nodes, title, doneSummary, error }: Props) {
  if (nodes.length === 0) return null;
  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
      <p className="mb-3 flex items-center gap-1.5 text-[12px] font-medium text-[var(--color-text-secondary)]">
        <span
          className="inline-flex h-4 w-4 items-center justify-center rounded-full"
          style={{
            background: "var(--color-background-success)",
            color: "var(--color-brand)",
          }}
        >
          <IconLoader2 size={10} className="animate-spin" />
        </span>
        {title}
      </p>
      <ol className="flex flex-col gap-1.5">
        {nodes.map((n) => (
          <Step key={n.key} node={n} />
        ))}
      </ol>
      {doneSummary && (
        <p className="mt-3 text-[11px] text-[var(--color-text-tertiary)]">
          {doneSummary}
        </p>
      )}
      {error && (
        <p className="mt-3 rounded bg-[var(--color-background-danger)] px-3 py-2 text-[11px] text-[var(--color-text-danger)]">
          {error}
        </p>
      )}
    </div>
  );
}

function Step({ node }: { node: AgentNode }) {
  const { status, label, summary } = node;
  const { Icon, color, bg, animation, labelColor, summaryColor } =
    STATE_STYLE[status];
  return (
    <li className="flex items-start gap-2.5 text-[12.5px]">
      <span
        className="mt-[2px] inline-flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full"
        style={{ background: bg, color }}
      >
        <Icon size={11} className={animation} />
      </span>
      <div className="min-w-0 flex-1">
        <p style={{ color: labelColor }}>{label}</p>
        {summary && (
          <p
            className="text-[11px] leading-snug"
            style={{ color: summaryColor }}
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
    bg: "transparent",
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
};
