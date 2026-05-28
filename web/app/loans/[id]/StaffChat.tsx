"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  IconAlertTriangle,
  IconCheck,
  IconLoader2,
  IconSend,
  IconSparkles,
  IconTool,
  IconX,
} from "@tabler/icons-react";
import { AnimatePresence, motion } from "motion/react";
import { toast } from "sonner";

import {
  streamAgentChat,
  type ChatEvent,
  type ChatMessage,
  type ConfirmRequiredEvent,
  type ToolResume,
} from "@/lib/agentChat";
import { BorrowerMessagePreviewModal } from "@/app/components/BorrowerMessagePreviewModal";
import { MarkdownBlock } from "@/app/components/MarkdownBlock";
import { humanizeRisk, humanizeStage } from "@/lib/humanize";

/**
 * Internal staff chat agent. Mirrors the borrower chat surface but:
 *
 *   - Auths via bearer token (the staff side has no session cookie).
 *   - Hits ``/staff/chat/stream``.
 *   - Tools are role-bound to ``underwriter`` / ``admin`` —
 *     ``search_loans``, ``override_extraction``, ``advance_loan_stage``,
 *     ``send_borrower_message``, etc.
 *
 * Render-wise it's deliberately near-identical to ``BorrowerChat`` —
 * one consistent agent surface across both sides of the desk is part
 * of the showcase, not boilerplate. When we eventually extract a
 * shared ``<AgentChat>`` primitive, the diff between this file and
 * ``BorrowerChat.tsx`` shrinks to a config object (URL + auth mode +
 * placeholder text). For now we keep them parallel.
 */

type Status = "idle" | "streaming" | "awaiting_confirm";

interface RenderedToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
  human_action: string;
  ok: boolean | null;
  result_text: string | null;
}

type TranscriptEntry =
  | { kind: "user"; key: string; text: string }
  | { kind: "assistant"; key: string; text: string }
  | { kind: "system"; key: string; text: string }
  | { kind: "tool"; key: string; call: RenderedToolCall };

interface Props {
  loanId: string;
}

export function StaffChat({ loanId }: Props) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<Status>("idle");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [userInput, setUserInput] = useState("");
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [pendingConfirm, setPendingConfirm] = useState<ConfirmRequiredEvent | null>(
    null,
  );
  const transcriptEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcript.length, status]);

  const runStream = useCallback(
    async (args: { userMessage?: string; toolResume?: ToolResume }) => {
      setStatus("streaming");
      try {
        for await (const ev of streamAgentChat({
          path: "/staff/chat/stream",
          auth: "bearer",
          extras: { loan_id: loanId },
          messages,
          userMessage: args.userMessage,
          toolResume: args.toolResume,
        })) {
          handleEvent(ev, {
            setTranscript,
            setMessages,
            setPendingConfirm,
            setStatus,
            onMutationLanded: () => {
              // The staff side runs mutations that change loan state.
              // Invalidate the loan + audit queries so the rest of the
              // workspace catches up without a manual refresh.
              queryClient.invalidateQueries({ queryKey: ["loan", loanId] });
              queryClient.invalidateQueries({
                queryKey: ["loan", loanId, "audit"],
              });
            },
          });
          if (ev.type === "confirm_required" || ev.type === "done" || ev.type === "error") {
            return;
          }
        }
      } catch (e) {
        toast.error("Chat connection lost", {
          description: e instanceof Error ? e.message : String(e),
        });
        setStatus("idle");
      }
    },
    [loanId, messages, queryClient],
  );

  const send = useCallback(() => {
    const text = userInput.trim();
    if (!text || status !== "idle") return;
    setTranscript((t) => [
      ...t,
      { kind: "user", text, key: `u-${t.length}` },
    ]);
    setUserInput("");
    void runStream({ userMessage: text });
  }, [userInput, status, runStream]);

  const confirm = useCallback(
    async (
      action: "confirm" | "cancel",
      // Optional override for the tool's input args. Used by the
      // preview-and-edit modal path for ``send_borrower_message``
      // so the staff member's edits to the body land in the
      // resume payload — the backend reads from ``input`` and
      // executes the tool with whatever's there.
      overrideArgs?: Record<string, unknown>,
    ) => {
      if (!pendingConfirm) return;
      setTranscript((t) => [
        ...t,
        {
          kind: "system",
          text:
            action === "confirm"
              ? `Confirmed: ${pendingConfirm.human_action.toLowerCase()}.`
              : `Cancelled: ${pendingConfirm.human_action.toLowerCase()}.`,
          key: `s-${t.length}`,
        },
      ]);
      const resume: ToolResume = {
        tool_use_id: pendingConfirm.id,
        name: pendingConfirm.name,
        input: overrideArgs ?? pendingConfirm.args,
        action,
      };
      setPendingConfirm(null);
      await runStream({ toolResume: resume });
    },
    [pendingConfirm, runStream],
  );

  return (
    <section className="flex flex-col gap-2 rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-5 py-4">
      <header className="flex items-center gap-2">
        <span
          className="inline-flex h-7 w-7 items-center justify-center rounded-full"
          style={{
            background: "var(--color-background-info)",
            color: "var(--color-text-info)",
          }}
        >
          <IconSparkles size={14} />
        </span>
        <div>
          <p className="text-[13.5px] font-medium">Underwriting copilot</p>
          <p className="text-[11.5px] text-[var(--color-text-secondary)]">
            Search the pipeline, override extractions, advance stages, or
            send a message to the borrower — by typing what you want.
          </p>
        </div>
      </header>

      <div className="mt-2 flex max-h-[520px] min-h-[200px] flex-col gap-2 overflow-y-auto rounded-md bg-[var(--color-background-secondary)] px-3 py-3 text-[12.5px]">
        {transcript.length === 0 && (
          <p className="text-center text-[11.5px] text-[var(--color-text-tertiary)]">
            Try: <em>"What's happened on this loan recently?"</em> ·{" "}
            <em>"Override annual_noi to 284200 — rent roll shows the right number."</em> ·{" "}
            <em>"Send the borrower a note asking for the latest tax return."</em>
          </p>
        )}
        {transcript.map((entry) => (
          <TranscriptItem key={entry.key} entry={entry} />
        ))}
        {status === "streaming" && (
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex items-center gap-1.5 text-[11.5px] text-[var(--color-text-tertiary)]"
          >
            <IconLoader2 size={11} className="animate-spin" /> Thinking…
          </motion.p>
        )}
        <div ref={transcriptEndRef} />
      </div>

      <div className="mt-2 flex gap-2">
        <textarea
          value={userInput}
          onChange={(e) => setUserInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="Ask the copilot to look something up or take an action."
          disabled={status !== "idle"}
          rows={2}
          className="form-input-on-card flex-1 resize-y"
        />
        <button
          type="button"
          onClick={send}
          disabled={status !== "idle" || !userInput.trim()}
          className="inline-flex h-9 items-center gap-1.5 self-start rounded-md px-3 text-[12px] font-medium disabled:opacity-45"
          style={{
            background: "var(--color-brand)",
            color: "var(--color-brand-light)",
          }}
        >
          <IconSend size={13} />
          Send
        </button>
      </div>

      <AnimatePresence>
        {pendingConfirm &&
          (pendingConfirm.name === "send_borrower_message" ? (
            // The borrower-message tool gets the rich preview-and-edit
            // modal — same UX as the decision panel — so the staff
            // member sees the actual text and can refine before it
            // ships. Other destructive tools (stage transitions,
            // overrides) use the read-only confirm modal because
            // there's nothing useful to edit.
            <BorrowerMessagePreviewModal
              open
              title={`Confirm: ${pendingConfirm.human_action || "send a message"}`}
              description="The borrower will see this exact text on their /apply page."
              initialBody={
                typeof pendingConfirm.args.body === "string"
                  ? pendingConfirm.args.body
                  : ""
              }
              confirmLabel="Send"
              onConfirm={({ body }) => {
                // Merge the edited body into the original args so any
                // other fields the LLM set (loan_id, etc.) flow through
                // unchanged.
                void confirm("confirm", {
                  ...pendingConfirm.args,
                  body,
                });
              }}
              onClose={() => void confirm("cancel")}
            />
          ) : (
            <ConfirmModal
              event={pendingConfirm}
              onDecide={(action) => void confirm(action)}
            />
          ))}
      </AnimatePresence>
    </section>
  );
}

// ---- SSE event → state reducers ------------------------------------------

interface HandlerSetters {
  setTranscript: React.Dispatch<React.SetStateAction<TranscriptEntry[]>>;
  setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
  setPendingConfirm: React.Dispatch<
    React.SetStateAction<ConfirmRequiredEvent | null>
  >;
  setStatus: React.Dispatch<React.SetStateAction<Status>>;
  onMutationLanded: () => void;
}

function handleEvent(ev: ChatEvent, s: HandlerSetters) {
  switch (ev.type) {
    case "thinking":
      // Driven by ``status`` alone.
      break;
    case "message":
      s.setTranscript((t) => [
        ...t,
        { kind: "assistant", text: ev.text, key: `a-${t.length}` },
      ]);
      break;
    case "tool_call":
      s.setTranscript((t) => [
        ...t,
        {
          kind: "tool",
          key: ev.id,
          call: {
            id: ev.id,
            name: ev.name,
            args: ev.args,
            human_action: ev.human_action,
            ok: null,
            result_text: null,
          },
        },
      ]);
      break;
    case "tool_result":
      s.setTranscript((t) =>
        t.map((entry) =>
          entry.kind === "tool" && entry.call.id === ev.id
            ? {
                ...entry,
                call: {
                  ...entry.call,
                  ok: ev.ok,
                  result_text: ev.ok
                    ? summariseStaffResult(entry.call.name, ev.result)
                    : (ev.error ?? "Tool failed"),
                },
              }
            : entry,
        ),
      );
      // Any successful tool call could have changed loan state —
      // tell the parent to refresh.
      if (ev.ok) s.onMutationLanded();
      break;
    case "confirm_required":
      s.setMessages(ev.messages);
      s.setPendingConfirm(ev);
      s.setStatus("awaiting_confirm");
      break;
    case "done":
      s.setMessages(ev.messages);
      s.setStatus("idle");
      break;
    case "error":
      toast.error(ev.reason, { description: ev.detail });
      s.setStatus("idle");
      break;
  }
}

function TranscriptItem({ entry }: { entry: TranscriptEntry }) {
  if (entry.kind === "user") {
    return (
      <div className="flex justify-end">
        <p className="max-w-[80%] whitespace-pre-wrap rounded-md bg-[var(--color-background-primary)] px-3 py-1.5 text-[var(--color-text-primary)]">
          {entry.text}
        </p>
      </div>
    );
  }
  if (entry.kind === "assistant") {
    return (
      <div className="flex justify-start">
        <div className="max-w-[85%]">
          <MarkdownBlock>{entry.text}</MarkdownBlock>
        </div>
      </div>
    );
  }
  if (entry.kind === "system") {
    return (
      <p className="text-center text-[10.5px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
        {entry.text}
      </p>
    );
  }
  const c = entry.call;
  const Icon = c.ok === null ? IconLoader2 : c.ok ? IconCheck : IconAlertTriangle;
  const color =
    c.ok === null
      ? "var(--color-text-secondary)"
      : c.ok
        ? "var(--color-brand)"
        : "var(--color-text-danger)";
  return (
    <div className="flex items-start gap-2 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3 py-2">
      <span
        className="mt-[2px] inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full"
        style={{
          background: c.ok === null ? "var(--color-background-secondary)" : "transparent",
          color,
        }}
      >
        <Icon size={10} className={c.ok === null ? "animate-spin" : ""} />
      </span>
      <div className="flex-1">
        <p className="flex items-center gap-1.5 text-[11.5px] font-medium">
          <IconTool size={10} className="text-[var(--color-text-tertiary)]" />
          {c.human_action || c.name}
        </p>
        {c.result_text && (
          <p
            className="mt-1 whitespace-pre-wrap text-[11.5px] leading-snug"
            style={{ color }}
          >
            {c.result_text}
          </p>
        )}
      </div>
    </div>
  );
}

function ConfirmModal({
  event,
  onDecide,
}: {
  event: ConfirmRequiredEvent;
  onDecide: (a: "confirm" | "cancel") => void;
}) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.14 }}
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "var(--color-overlay-medium)" }}
      role="dialog"
      aria-modal="true"
    >
      <motion.div
        initial={{ opacity: 0, y: 8, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 4, scale: 0.99 }}
        transition={{ duration: 0.18, ease: "easeOut" }}
        className="w-full max-w-md overflow-hidden rounded-lg border-[0.5px] border-[var(--color-text-danger)] bg-[var(--color-background-primary)] shadow-xl"
      >
        <header className="border-b-[0.5px] border-[var(--color-border-tertiary)] px-5 py-4">
          <p className="flex items-center gap-2 text-[13.5px] font-medium text-[var(--color-text-danger)]">
            <IconAlertTriangle size={14} />
            Confirm: {event.human_action || event.name}
          </p>
          <p className="mt-1 text-[12px] text-[var(--color-text-secondary)]">
            The copilot wants to run this on the loan. It's destructive
            or sensitive enough that we ask you to confirm — the
            action is logged on the audit timeline either way.
          </p>
        </header>
        <div className="px-5 py-4">
          <p className="text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
            Action details
          </p>
          <pre className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded bg-[var(--color-background-secondary)] p-2.5 text-[11.5px] leading-snug text-[var(--color-text-primary)]">
            {event.summary || JSON.stringify(event.args, null, 2)}
          </pre>
        </div>
        <footer className="flex items-center justify-end gap-2 border-t-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-secondary)] px-5 py-3">
          <button
            type="button"
            onClick={() => onDecide("cancel")}
            className="inline-flex items-center gap-1 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3 py-1.5 text-[12px] hover:bg-[var(--color-background-secondary)]"
          >
            <IconX size={12} />
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onDecide("confirm")}
            className="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-medium"
            style={{
              background: "var(--color-text-danger)",
              color: "white",
            }}
          >
            <IconCheck size={12} />
            Yes, do it
          </button>
        </footer>
      </motion.div>
    </motion.div>
  );
}

function summariseStaffResult(name: string, result: unknown): string {
  if (!result || typeof result !== "object") return "Done.";
  const r = result as Record<string, unknown>;
  if (typeof r.note === "string") return r.note as string;
  if (typeof r.message === "string") return r.message as string;
  if (name === "get_loan_overview") {
    // Humanize both enum surfaces — even on a staff-facing chat
    // a "$2,400,000, risk low" reads better than "risk low" and far
    // better than "stage: underwriting" rendered raw.
    const stageStr = typeof r.stage === "string" ? r.stage : null;
    const riskStr = typeof r.risk_band === "string" ? r.risk_band : null;
    return `${r.reference}: ${humanizeStage(stageStr)}, $${Number(r.amount ?? 0).toLocaleString()}, risk ${humanizeRisk(riskStr)}.`;
  }
  if (name === "list_recent_activity") {
    return `${r.count ?? 0} event(s) loaded.`;
  }
  if (name === "search_loans") {
    const n = Number(r.count ?? 0);
    return `${n} match${n === 1 ? "" : "es"}.`;
  }
  if (name === "get_borrower_messages") {
    return `${r.count ?? 0} message(s).`;
  }
  if (name === "override_extraction") {
    return `Overrode ${r.field}: ${r.from} → ${r.to}.`;
  }
  if (name === "advance_loan_stage") {
    // ``r.from`` / ``r.to`` are raw enum strings from the tool
    // result. Run them through ``humanizeStage`` so the transcript
    // reads "Stage: Intake → Underwriting" rather than the
    // snake_cased version.
    const fromStr = typeof r.from === "string" ? r.from : null;
    const toStr = typeof r.to === "string" ? r.to : null;
    return `Stage: ${humanizeStage(fromStr)} → ${humanizeStage(toStr)}.`;
  }
  if (name === "send_borrower_message") {
    return `Message sent to borrower on ${r.reference}.`;
  }
  const compact = JSON.stringify(result);
  return compact.length > 200 ? compact.slice(0, 200) + "…" : compact;
}
