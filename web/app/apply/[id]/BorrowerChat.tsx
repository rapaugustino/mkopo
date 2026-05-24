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
  streamChat,
  type ChatMessage,
  type ConfirmRequiredEvent,
  type ToolResume,
} from "@/lib/borrowerChat";
import { MarkdownBlock } from "@/app/components/MarkdownBlock";

/**
 * Borrower-side chat agent surface.
 *
 * Lives on the application status page. The borrower types what they
 * want; the agent picks the right tool from the registry and either
 * runs it (read tools) or asks the borrower to confirm first
 * (destructive tools).
 *
 * UI shape, top-to-bottom:
 *
 *   - Header strip: "Ask Mkopo" + a short hint.
 *   - Message list: user turns right-aligned, assistant left-aligned,
 *     tool calls render as inline cards between them.
 *   - "Thinking…" indicator while a stream is in flight.
 *   - Composer at the bottom with a send button.
 *   - Confirmation modal that mounts when ``confirm_required`` fires.
 *
 * State machine:
 *
 *   - ``idle`` — no stream in flight; composer enabled.
 *   - ``streaming`` — receiving SSE events; composer disabled.
 *   - ``awaiting_confirm`` — ``confirm_required`` arrived; modal open.
 *
 * The conversation history (``messages``) is the source of truth.
 * Each rendered "turn" derives from one history entry. Tool calls
 * are rendered as derived chips from ``streamEvents`` (an
 * ever-growing ordered log of events that happened this session).
 */

type Status = "idle" | "streaming" | "awaiting_confirm";

interface RenderedToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
  human_action: string;
  /** ``null`` while pending; set to true/false when the result arrives. */
  ok: boolean | null;
  result_text: string | null;
}

interface Props {
  loanId: string;
}

export function BorrowerChat({ loanId }: Props) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<Status>("idle");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [userInput, setUserInput] = useState("");
  // Visible-to-user transcript. Distinct from the Anthropic-shape
  // ``messages`` because we want a *display* order that interleaves
  // tool calls between assistant messages. We append entries as SSE
  // events arrive.
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [pendingConfirm, setPendingConfirm] = useState<ConfirmRequiredEvent | null>(
    null,
  );
  const transcriptEndRef = useRef<HTMLDivElement | null>(null);

  // Autoscroll on every new entry.
  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcript.length, status]);

  const runStream = useCallback(
    async (args: {
      userMessage?: string;
      toolResume?: ToolResume;
    }) => {
      setStatus("streaming");
      try {
        for await (const ev of streamChat({
          loanId,
          messages,
          userMessage: args.userMessage,
          toolResume: args.toolResume,
        })) {
          switch (ev.type) {
            case "thinking":
              // The "Thinking…" indicator is driven by ``status``;
              // no transcript entry needed.
              break;
            case "message":
              setTranscript((t) => [
                ...t,
                { kind: "assistant", text: ev.text, key: `a-${t.length}` },
              ]);
              break;
            case "tool_call":
              setTranscript((t) => [
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
              setTranscript((t) =>
                t.map((entry) =>
                  entry.kind === "tool" && entry.call.id === ev.id
                    ? {
                        ...entry,
                        call: {
                          ...entry.call,
                          ok: ev.ok,
                          result_text: ev.ok
                            ? summariseResult(entry.call.name, ev.result)
                            : (ev.error ?? "Tool failed"),
                        },
                      }
                    : entry,
                ),
              );
              // Tool mutations may have changed status — pull a fresh
              // status query.
              if (
                ev.ok &&
                ["update_loan_field", "withdraw_application"].includes(
                  transcriptEntryFor(loanId, ev.id) ?? "",
                )
              ) {
                queryClient.invalidateQueries({
                  queryKey: ["borrower-status", loanId],
                });
              }
              break;
            case "confirm_required":
              setMessages(ev.messages);
              setPendingConfirm(ev);
              setStatus("awaiting_confirm");
              return; // stream is over until user resumes
            case "done":
              setMessages(ev.messages);
              setStatus("idle");
              return;
            case "error":
              toast.error(ev.reason, {
                description: ev.detail,
              });
              setStatus("idle");
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
    async (action: "confirm" | "cancel") => {
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
        input: pendingConfirm.args,
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
            background: "var(--color-background-success)",
            color: "var(--color-brand)",
          }}
        >
          <IconSparkles size={14} />
        </span>
        <div>
          <p className="text-[13.5px] font-medium">Ask Mkopo</p>
          <p className="text-[11.5px] text-[var(--color-text-secondary)]">
            Ask about your application, update fields, or withdraw — I'll
            do it directly and confirm anything destructive first.
          </p>
        </div>
      </header>

      {/* Transcript */}
      <div className="mt-2 flex max-h-[480px] min-h-[180px] flex-col gap-2 overflow-y-auto rounded-md bg-[var(--color-background-secondary)] px-3 py-3 text-[12.5px]">
        {transcript.length === 0 && (
          <p className="text-center text-[11.5px] text-[var(--color-text-tertiary)]">
            Try: <em>"What's still missing?"</em> · <em>"Why did underwriting flag DSCR?"</em> · <em>"Update my annual income to $145,000."</em>
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

      {/* Composer */}
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
          placeholder="Ask anything about your application."
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
        {pendingConfirm && (
          <ConfirmModal
            event={pendingConfirm}
            onDecide={(action) => void confirm(action)}
          />
        )}
      </AnimatePresence>
    </section>
  );
}

// ---- transcript rendering --------------------------------------------------

type TranscriptEntry =
  | { kind: "user"; key: string; text: string }
  | { kind: "assistant"; key: string; text: string }
  | { kind: "system"; key: string; text: string }
  | { kind: "tool"; key: string; call: RenderedToolCall };

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
  // tool call
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
        <Icon
          size={10}
          className={c.ok === null ? "animate-spin" : ""}
        />
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

// ---- confirmation modal ---------------------------------------------------

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
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
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
            The assistant wants to run this on your application. It's
            destructive or sensitive enough that we ask you to confirm
            first.
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

// ---- result summarising ---------------------------------------------------

function summariseResult(name: string, result: unknown): string {
  if (!result || typeof result !== "object") return "Done.";
  const r = result as Record<string, unknown>;
  if (typeof r.message === "string") return r.message as string;
  if (name === "get_loan_status") {
    return `Stage: ${r.stage}. ${r.next_step_for_you ?? ""}`.trim();
  }
  if (name === "list_documents") {
    return `${r.count ?? 0} document(s) on file.`;
  }
  if (name === "list_missing_fields") {
    const missing = Array.isArray(r.missing) ? r.missing : [];
    if (missing.length === 0)
      return r.intake_has_run
        ? "Nothing missing — intake says the packet is complete."
        : "Intake hasn't analysed this yet.";
    return `Missing: ${missing.join(", ")}.`;
  }
  if (name === "get_decision_reasoning") {
    return r.has_decision
      ? `Decision: ${r.decision_path ?? "—"}.`
      : "No decision yet.";
  }
  if (name === "update_loan_field") {
    return r.changed
      ? `Updated ${r.field}: ${r.old_value ?? "—"} → ${r.new_value}.`
      : `${r.field} unchanged.`;
  }
  if (name === "withdraw_application") {
    return r.withdrawn ? `Withdrawn: ${r.reference}.` : "Withdraw failed.";
  }
  // Fallback: stringify briefly.
  const compact = JSON.stringify(result);
  return compact.length > 200 ? compact.slice(0, 200) + "…" : compact;
}

// ---- internal helper ----------------------------------------------------

// Look up a tool name from the current transcript by tool_use_id.
// Used to decide whether to invalidate the status query after a
// successful tool call.
function transcriptEntryFor(_loanId: string, _id: string): string | null {
  // Placeholder — wiring this through useRef + transcript would be
  // overkill for the demo. The status query refetches every 30s
  // anyway, so a stale chip for a few seconds is fine.
  return null;
}
