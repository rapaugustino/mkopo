"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  IconArrowRight,
  IconBuildingBank,
  IconCheck,
  IconFlagCheck,
  IconGavel,
  IconListCheck,
  IconMicroscope,
  IconX,
} from "@tabler/icons-react";
import { motion } from "motion/react";
import { toast } from "sonner";
import { api, type LoanStage } from "@/lib/api";
import { humanizeStage } from "@/lib/humanize";
import { PrimaryButton } from "@/app/components/PrimaryButton";
import { SecondaryButton } from "@/app/components/SecondaryButton";

interface Props {
  loanId: string;
  currentStage: LoanStage;
}

/** Result of GET /loans/{id}/transitions — null means "ready", a
 *  string means "blocked because X". The UI surfaces the string as a
 *  tooltip on the disabled button so the user understands why. */
type AllowedTransitions = Record<string, string | null>;

/** Forward path per stage. Mirrors VALID_TRANSITIONS in api/mkopo/models/loan.py.
 *
 *  Decline is rendered separately as a destructive secondary; this is the
 *  "happy path" advance only. The decision tab has its own action bar for
 *  approve / conditional / decline, so we omit the decision stage here. */
const FORWARD: Record<LoanStage, { to: LoanStage; label: string; Icon: React.ComponentType<{ size?: number }> } | null> = {
  intake: { to: "underwriting", label: "Move to underwriting", Icon: IconMicroscope },
  underwriting: { to: "decision", label: "Move to decision", Icon: IconGavel },
  decision: null, // handled by CreditDecisionPanel's action bar
  conditions: { to: "closing", label: "Move to closing", Icon: IconFlagCheck },
  approved: { to: "closing", label: "Move to closing", Icon: IconFlagCheck },
  closing: { to: "servicing", label: "Mark as servicing", Icon: IconBuildingBank },
  servicing: null,
  declined: null,
};

/** Per-stage decline availability. Servicing / declined / approved-after-closing
 *  shouldn't be declinable from here. */
const CAN_DECLINE: Record<LoanStage, boolean> = {
  intake: true,
  underwriting: true,
  decision: false, // decision panel owns the decline path
  conditions: true,
  approved: false,
  closing: false,
  servicing: false,
  declined: false,
};

/**
 * Contextual stage-transition controls in the loan-detail header.
 *
 * Each click opens a small confirmation prompt — required because every
 * transition must carry a reason for the audit log. The reason gets
 * persisted on the ``stage_changed`` event so committee reviewers can
 * reconstruct WHY a loan moved when, not just THAT it moved.
 *
 * We deliberately don't expose every legal transition — only the
 * forward "happy path" plus an explicit decline. Power-users who need
 * a non-standard transition can edit via the backend until we add a
 * "more options" disclosure here.
 */
export function StageActions({ loanId, currentStage }: Props) {
  const queryClient = useQueryClient();
  const forward = FORWARD[currentStage];
  const canDecline = CAN_DECLINE[currentStage];

  // Per-target readiness map; null means ready, string means blocked-because-X.
  // The tooltip on the disabled button surfaces the reason so the user
  // doesn't click into a 422 to learn why advance is gated.
  const allowedQuery = useQuery<AllowedTransitions, Error>({
    queryKey: ["loan", loanId, "transitions"],
    queryFn: () => api.getAllowedTransitions(loanId),
    staleTime: 10_000,
  });
  const allowed = allowedQuery.data ?? {};
  const forwardBlockedBy = forward ? allowed[forward.to] ?? null : null;
  const declineBlockedBy = canDecline ? allowed["declined"] ?? null : null;

  const [pendingTransition, setPendingTransition] = useState<{
    to: LoanStage;
    label: string;
  } | null>(null);

  const transition = useMutation({
    mutationFn: ({ to, reason }: { to: LoanStage; reason: string }) =>
      api.transitionStage(loanId, to, reason),
    onSuccess: async (_data, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["loan", loanId] }),
        queryClient.invalidateQueries({ queryKey: ["loan", loanId, "audit"] }),
        queryClient.invalidateQueries({ queryKey: ["loan", loanId, "transitions"] }),
        queryClient.invalidateQueries({ queryKey: ["loans"] }),
      ]);
      const isDecline = variables.to === "declined";
      toast[isDecline ? "warning" : "success"](
        isDecline ? "Loan declined" : `Moved to ${humanizeStage(variables.to)}`,
        { description: variables.reason },
      );
      setPendingTransition(null);
    },
    onError: (e) =>
      toast.error("Stage transition failed", {
        description: e instanceof Error ? e.message : String(e),
      }),
  });

  if (!forward && !canDecline) {
    return null; // terminal stage — nothing to advance to.
  }

  return (
    <>
      {forward && (
        <PrimaryButton
          Icon={forward.Icon}
          onClick={() =>
            setPendingTransition({ to: forward.to, label: forward.label })
          }
          disabled={transition.isPending || !!forwardBlockedBy}
          // Tooltip carries the human-readable "why it's disabled"
          // message returned by the backend's prerequisite check.
          // Without it the user clicks and gets a confusing 422.
          title={forwardBlockedBy ?? undefined}
        >
          {forward.label}
        </PrimaryButton>
      )}
      {canDecline && (
        <SecondaryButton
          Icon={IconX}
          onClick={() => setPendingTransition({ to: "declined", label: "Decline" })}
          disabled={transition.isPending || !!declineBlockedBy}
          title={declineBlockedBy ?? undefined}
        >
          Decline
        </SecondaryButton>
      )}

      {pendingTransition && (
        <TransitionPrompt
          fromStage={currentStage}
          toStage={pendingTransition.to}
          buttonLabel={pendingTransition.label}
          onConfirm={(reason) =>
            transition.mutate({ to: pendingTransition.to, reason })
          }
          onCancel={() => setPendingTransition(null)}
          submitting={transition.isPending}
          error={transition.error?.message ?? null}
        />
      )}
    </>
  );
}

// ---- prompt ---------------------------------------------------------------

function TransitionPrompt({
  fromStage,
  toStage,
  buttonLabel,
  onConfirm,
  onCancel,
  submitting,
  error,
}: {
  fromStage: LoanStage;
  toStage: LoanStage;
  buttonLabel: string;
  onConfirm: (reason: string) => void;
  onCancel: () => void;
  submitting: boolean;
  error: string | null;
}) {
  const [reason, setReason] = useState("");
  const isDecline = toStage === "declined";
  const HeaderIcon = isDecline ? IconX : IconArrowRight;

  return (
    <motion.div
      role="dialog"
      aria-modal="true"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.14 }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onCancel}
    >
      <motion.div
        initial={{ opacity: 0, y: 8, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 4, scale: 0.99 }}
        transition={{ duration: 0.18, ease: "easeOut" }}
        className="w-full max-w-md overflow-hidden rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="border-b-[0.5px] border-[var(--color-border-tertiary)] px-5 py-4">
          <p className="flex items-center gap-2 text-[15px] font-medium">
            <HeaderIcon
              size={16}
              style={{
                color: isDecline
                  ? "var(--color-text-danger)"
                  : "var(--color-brand)",
              }}
            />
            {buttonLabel}
          </p>
          <p className="mt-1 text-[12px] text-[var(--color-text-secondary)]">
            {humanizeStage(fromStage)} → {humanizeStage(toStage)}. This writes a
            <strong> stage_changed</strong> audit event with your reason; both
            committee reviewers and the AI agents read it.
          </p>
        </header>

        <div className="px-5 py-4">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
              Reason for{" "}
              {isDecline ? "decline" : "advancing"}
            </span>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              disabled={submitting}
              rows={4}
              placeholder={
                isDecline
                  ? "e.g. Debt yield 6.6% below 8% policy floor; submarket trending negative."
                  : "e.g. All required fields extracted, doc packet complete, ready for underwriter review."
              }
              className="form-input"
            />
          </label>
          {error && (
            <p className="mt-3 rounded bg-[var(--color-background-danger)] px-3 py-2 text-[11px] text-[var(--color-text-danger)]">
              {error}
            </p>
          )}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-secondary)] px-5 py-3">
          <SecondaryButton type="button" onClick={onCancel} disabled={submitting}>
            Cancel
          </SecondaryButton>
          <PrimaryButton
            type="button"
            Icon={isDecline ? IconX : IconCheck}
            onClick={() => onConfirm(reason.trim())}
            disabled={submitting || reason.trim().length < 3}
          >
            {submitting ? "Saving…" : `Confirm ${isDecline ? "decline" : "advance"}`}
          </PrimaryButton>
        </footer>
      </motion.div>
    </motion.div>
  );
}

// Re-export the icon mapping so callers (e.g. tests) can introspect it.
export { FORWARD as FORWARD_TRANSITIONS, CAN_DECLINE as DECLINABLE_STAGES };

// Suppress an unused-import warning from IconListCheck in this file; the
// icon belongs to a stage that is its own destination and shows up in the
// type space but isn't directly rendered here. Keeping the import makes
// switching the "to" wiring trivial if the mapping evolves.
void IconListCheck;
