"use client";

import { useEffect, useState } from "react";
import { IconSparkles } from "@tabler/icons-react";
import type { IntakeInterrupt } from "@/lib/api";
import { humanizeField } from "@/lib/humanize";
import { Pill } from "@/app/components/Pill";
import { PrimaryButton } from "@/app/components/PrimaryButton";
import { SecondaryButton } from "@/app/components/SecondaryButton";

interface Props {
  interrupt: IntakeInterrupt;
  onSend: (subject: string, bodyText: string) => Promise<void>;
  onCancel: () => Promise<void>;
  onClose: () => void;
}

/**
 * Modal shown when the intake agent pauses for human approval.
 *
 * Why a modal rather than inline: the borrower email is a one-shot
 * commit — once sent we can't unsend. A modal forces the underwriter to
 * read it, and the missing-fields chip strip makes it obvious what the
 * email is asking for before they ever look at the body. The "AI ·
 * drafted" pill in the header is the same brand-green chip used in the
 * timeline so the provenance signal is consistent everywhere AI work
 * surfaces.
 */
export function IntakeApprovalModal({ interrupt, onSend, onCancel, onClose }: Props) {
  const [subject, setSubject] = useState(interrupt.draft.subject);
  const [bodyText, setBodyText] = useState(interrupt.draft.body_text);
  const [submitting, setSubmitting] = useState<null | "send" | "cancel">(null);
  const [error, setError] = useState<string | null>(null);

  // Reset edits if the modal is reopened with a fresh draft
  useEffect(() => {
    setSubject(interrupt.draft.subject);
    setBodyText(interrupt.draft.body_text);
    setError(null);
  }, [interrupt]);

  const handle = async (action: "send" | "cancel") => {
    setError(null);
    setSubmitting(action);
    try {
      if (action === "send") {
        await onSend(subject, bodyText);
      } else {
        await onCancel();
      }
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setSubmitting(null);
    }
  };

  const edited =
    subject !== interrupt.draft.subject || bodyText !== interrupt.draft.body_text;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="approval-title"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl overflow-hidden rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="border-b-[0.5px] border-[var(--color-border-tertiary)] px-5 py-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p id="approval-title" className="text-[15px] font-medium tracking-tight">
                Review draft email
              </p>
              <p className="mt-0.5 text-[12px] text-[var(--color-text-secondary)]">
                The intake agent drafted this. Edit if needed, then send or cancel.
              </p>
            </div>
            <Pill variant="ai" leading={<IconSparkles size={11} />}>
              AI · drafted
            </Pill>
          </div>

          {interrupt.missing_fields.length > 0 && (
            <div className="mt-3 rounded-md bg-[var(--color-background-secondary)] px-3 py-2 text-[12px]">
              <p className="font-medium text-[var(--color-text-secondary)]">
                Requesting {interrupt.missing_fields.length}{" "}
                {interrupt.missing_fields.length === 1 ? "item" : "items"}:
              </p>
              <p className="mt-0.5 flex flex-wrap gap-1.5">
                {interrupt.missing_fields.map((f) => (
                  <Pill key={f} variant="warn" size="xs">
                    {humanizeField(f)}
                  </Pill>
                ))}
              </p>
            </div>
          )}
        </header>

        <div className="flex flex-col gap-4 px-5 py-4">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
              Subject
            </span>
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              disabled={submitting !== null}
              className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-white px-3 py-2 text-[13px] focus:border-[var(--color-brand)] focus:outline-none disabled:opacity-50"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
              Body
            </span>
            <textarea
              value={bodyText}
              onChange={(e) => setBodyText(e.target.value)}
              disabled={submitting !== null}
              rows={10}
              className="resize-y rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-white px-3 py-2 font-mono text-[12px] leading-relaxed focus:border-[var(--color-brand)] focus:outline-none disabled:opacity-50"
            />
          </label>
          {edited && (
            <p className="text-[11px] text-[var(--color-text-secondary)]">
              You&apos;ve edited the draft. The audit log will record your final
              version, not the AI&apos;s original.
            </p>
          )}
          {error && (
            <p className="rounded bg-[var(--color-background-danger)] px-3 py-2 text-[11px] text-[var(--color-text-danger)]">
              {error}
            </p>
          )}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-secondary)] px-5 py-3">
          <SecondaryButton
            type="button"
            onClick={() => handle("cancel")}
            disabled={submitting !== null}
          >
            {submitting === "cancel" ? "Cancelling…" : "Cancel"}
          </SecondaryButton>
          <PrimaryButton
            type="button"
            onClick={() => handle("send")}
            disabled={submitting !== null || !subject.trim() || !bodyText.trim()}
          >
            {submitting === "send" ? "Sending…" : "Send"}
          </PrimaryButton>
        </footer>
      </div>
    </div>
  );
}
