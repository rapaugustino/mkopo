"use client";

import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  IconAlertTriangle,
  IconCheck,
  IconShieldLock,
  IconX,
} from "@tabler/icons-react";
import { PrimaryButton } from "@/app/components/PrimaryButton";
import { SecondaryButton } from "@/app/components/SecondaryButton";
import { Pill } from "@/app/components/Pill";

interface Props {
  open: boolean;
  /** Title shown in the modal header. e.g. "Send to borrower" /
   *  "Send adverse action letter" / "Confirm: send a message". */
  title: string;
  /** Optional one-line description directly under the title. */
  description?: string;
  /** Initial subject (optional — some surfaces don't have a subject
   *  separate from the body). */
  initialSubject?: string;
  /** Initial body. The user edits this; submit returns whatever they
   *  ended up with. */
  initialBody: string;
  /** When true, render the modal with the danger-styled chrome and
   *  show the Reg B preservation hint. Used for the adverse-action
   *  letter flow. */
  variant?: "default" | "danger";
  /** ECOA Reg B principal reasons that MUST appear in the body for
   *  the adverse-action letter to be compliant. We don't enforce —
   *  the staff member is the final arbiter — but we do surface them
   *  as pills + warn if the edited body drops one. */
  principalReasons?: string[];
  /** Label for the confirm button. Defaults to "Send". */
  confirmLabel?: string;
  /** Called with the user's edited text when they hit confirm. */
  onConfirm: (edited: { subject?: string; body: string }) => void;
  onClose: () => void;
  /** When true, the confirm button shows a spinner / disables. */
  isSubmitting?: boolean;
}


/**
 * Generic "preview the AI's draft, edit if needed, then send"
 * modal. Used by the decision panel (send-to-borrower / adverse-
 * action letter) and the staff-chat confirmation flow for
 * ``send_borrower_message`` tool calls.
 *
 * Design notes:
 *
 * - The modal is the *first* surface where staff sees the actual
 *   text the borrower will receive. Before this existed, the
 *   decision panel auto-composed and sent in one click — there was
 *   no last-mile review. The whole point of this component is to
 *   make every borrower-facing message land in someone's editor
 *   before it lands in the borrower's inbox.
 *
 * - For the adverse-action letter (Reg B), we lint the edited body
 *   against the principal_reasons list. Removing one is allowed
 *   (the staff member may have a reason) but we make the change
 *   loud — the pills show "missing from body" status so it's a
 *   deliberate choice rather than an accident.
 *
 * - No backend changes — the consumer takes the returned text and
 *   posts it through whatever endpoint they already had. This
 *   modal is pure UX.
 */
export function BorrowerMessagePreviewModal({
  open,
  title,
  description,
  initialSubject,
  initialBody,
  variant = "default",
  principalReasons,
  confirmLabel = "Send",
  onConfirm,
  onClose,
  isSubmitting = false,
}: Props) {
  const [subject, setSubject] = useState(initialSubject ?? "");
  const [body, setBody] = useState(initialBody);

  // Re-seed on open with the latest initial values. The pattern from
  // /prompts: set state during render with a guard so the seed
  // commits in a single paint when the modal opens, without an
  // effect that would briefly flash the old/empty value.
  const [seedKey, setSeedKey] = useState<string>("");
  const incomingKey = `${initialSubject ?? ""}::${initialBody}`;
  if (open && seedKey !== incomingKey) {
    setSeedKey(incomingKey);
    setSubject(initialSubject ?? "");
    setBody(initialBody);
  }

  // For the adverse-action letter: which principal reasons are
  // currently mentioned in the body. Reason strings are short rule
  // ids (e.g. "dscr_above_floor") so substring search is the right
  // shape; the LLM is instructed to reference each by name in the
  // prose so the staff member is choosing to remove if the body
  // doesn't contain it.
  const reasonStatus = useMemo(() => {
    if (!principalReasons) return [];
    const lowerBody = body.toLowerCase();
    return principalReasons.map((r) => {
      const friendly = r.replace(/_/g, " ").toLowerCase();
      return {
        reason: r,
        // Match either the raw rule id or a humanised variant.
        present:
          lowerBody.includes(r.toLowerCase()) ||
          lowerBody.includes(friendly),
      };
    });
  }, [principalReasons, body]);
  const missingReasons = reasonStatus.filter((r) => !r.present);

  const headerBorder =
    variant === "danger"
      ? "var(--color-text-danger)"
      : "var(--color-border-tertiary)";
  const headerColor =
    variant === "danger"
      ? "var(--color-text-danger)"
      : "var(--color-text-primary)";

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.14 }}
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: "var(--color-overlay-medium)" }}
          role="dialog"
          aria-modal="true"
          aria-label={title}
        >
          <motion.div
            initial={{ opacity: 0, y: 8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 4, scale: 0.99 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
            className="w-full max-w-2xl overflow-hidden rounded-lg bg-[var(--color-background-primary)] shadow-xl"
            style={{ border: `0.5px solid ${headerBorder}` }}
          >
            <header
              className="px-5 py-4"
              style={{
                borderBottom: `0.5px solid var(--color-border-tertiary)`,
              }}
            >
              <p
                className="flex items-center gap-2 text-[13.5px] font-medium"
                style={{ color: headerColor }}
              >
                {variant === "danger" && <IconAlertTriangle size={14} />}
                {title}
              </p>
              {description && (
                <p className="mt-1 text-[12px] text-[var(--color-text-secondary)]">
                  {description}
                </p>
              )}
            </header>

            <div className="flex flex-col gap-3 px-5 py-4">
              {initialSubject !== undefined && (
                <label className="flex flex-col gap-1">
                  <span className="text-[10.5px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
                    Subject
                  </span>
                  <input
                    type="text"
                    value={subject}
                    onChange={(e) => setSubject(e.target.value)}
                    disabled={isSubmitting}
                    maxLength={200}
                    className="form-input"
                  />
                </label>
              )}

              <label className="flex flex-col gap-1">
                <span className="flex items-center gap-2 text-[10.5px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
                  Body
                  <span className="text-[var(--color-text-tertiary)] normal-case tracking-normal">
                    · {body.length.toLocaleString()} chars
                  </span>
                </span>
                <textarea
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                  disabled={isSubmitting}
                  rows={Math.max(8, Math.min(20, body.split("\n").length + 2))}
                  className="form-input text-[12.5px] leading-relaxed"
                  style={{ minHeight: 180 }}
                />
              </label>

              {/* Reg B reasons — only rendered for the decline flow. */}
              {principalReasons && principalReasons.length > 0 && (
                <div className="rounded-md bg-[var(--color-background-secondary)] px-3 py-2.5">
                  <p className="flex items-center gap-1.5 text-[10.5px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
                    <IconShieldLock size={11} />
                    Reg B principal reasons
                  </p>
                  <p className="mt-1 text-[11px] text-[var(--color-text-secondary)]">
                    ECOA requires each reason to appear in the letter body.
                    Pills go red if the current body doesn&apos;t mention them
                    — adjust the body or accept the change deliberately.
                  </p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {reasonStatus.map((r) => (
                      <Pill
                        key={r.reason}
                        variant={r.present ? "success" : "danger"}
                      >
                        {r.reason.replace(/_/g, " ")}
                        {!r.present && " · missing"}
                      </Pill>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <footer
              className="flex items-center justify-end gap-2 px-5 py-3"
              style={{
                borderTop: `0.5px solid var(--color-border-tertiary)`,
                background: "var(--color-background-secondary)",
              }}
            >
              {missingReasons.length > 0 && (
                <span className="mr-auto text-[11px] text-[var(--color-text-warning)]">
                  {missingReasons.length} reason
                  {missingReasons.length === 1 ? " is" : "s are"} no longer
                  cited in the body.
                </span>
              )}
              <SecondaryButton
                Icon={IconX}
                onClick={onClose}
                disabled={isSubmitting}
              >
                Cancel
              </SecondaryButton>
              <PrimaryButton
                Icon={IconCheck}
                onClick={() =>
                  onConfirm({
                    subject:
                      initialSubject !== undefined ? subject : undefined,
                    body,
                  })
                }
                disabled={isSubmitting || body.trim().length === 0}
              >
                {isSubmitting ? "Sending…" : confirmLabel}
              </PrimaryButton>
            </footer>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
