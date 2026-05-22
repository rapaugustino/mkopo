"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { IconBolt, IconHandStop } from "@tabler/icons-react";
import { motion } from "motion/react";
import { toast } from "sonner";
import { api, type AutonomyLevel, type Loan } from "@/lib/api";
import { PrimaryButton } from "@/app/components/PrimaryButton";
import { SecondaryButton } from "@/app/components/SecondaryButton";

interface Props {
  loan: Loan;
}

/**
 * Header pill that surfaces the loan's autonomy mode and lets the
 * underwriter flip it. Switching modes writes an
 * ``autonomy_changed`` audit event with the typed reason, so the
 * decision to fast-track (or hold back) a deal is itself part of
 * the case file.
 *
 * The two modes have very different behavioural implications, so the
 * pill is colour-coded to make the active mode unambiguous:
 *
 * - ``assisted`` → neutral grey + "Hand-off" icon. The system asks
 *   the human at every gate.
 * - ``autonomous`` → brand-green + "Bolt" icon. The orchestrator
 *   chains agents end-to-end and only pauses at irreversible HITL
 *   gates (borrower email, decision package send).
 */
export function AutonomyToggle({ loan }: Props) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const isAutonomous = loan.autonomy_level === "autonomous";

  const mutation = useMutation({
    mutationFn: ({ level, reason }: { level: AutonomyLevel; reason: string }) =>
      api.setAutonomy(loan.id, level, reason),
    onSuccess: async (_, vars) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["loan", loan.id] }),
        queryClient.invalidateQueries({ queryKey: ["loan", loan.id, "audit"] }),
        queryClient.invalidateQueries({ queryKey: ["loans"] }),
      ]);
      toast.success(
        vars.level === "autonomous"
          ? "Autonomous mode on"
          : "Switched to assisted mode",
        {
          description:
            vars.level === "autonomous"
              ? "The orchestrator will chain agents through the workflow, pausing only at human-required gates."
              : "Every step from here on requires explicit human approval.",
        },
      );
      setOpen(false);
    },
    onError: (e) =>
      toast.error("Couldn't update autonomy", {
        description: e instanceof Error ? e.message : String(e),
      }),
  });

  const Icon = isAutonomous ? IconBolt : IconHandStop;
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        title={
          isAutonomous
            ? "Autonomous: agents chain end-to-end. Click to switch to assisted."
            : "Assisted: every step requires human approval. Click to switch to autonomous."
        }
        className="inline-flex items-center gap-1.5 rounded-md border-[0.5px] px-2 py-1 text-[11px] font-medium transition-colors"
        style={{
          background: isAutonomous
            ? "var(--color-background-success)"
            : "var(--color-background-secondary)",
          color: isAutonomous
            ? "var(--color-brand)"
            : "var(--color-text-secondary)",
          borderColor: isAutonomous
            ? "var(--color-brand)"
            : "var(--color-border-tertiary)",
        }}
      >
        <Icon size={11} />
        {isAutonomous ? "Autonomous" : "Assisted"}
      </button>

      {open && (
        <AutonomyDialog
          currentLevel={loan.autonomy_level}
          submitting={mutation.isPending}
          onConfirm={(level, reason) => mutation.mutate({ level, reason })}
          onCancel={() => setOpen(false)}
        />
      )}
    </>
  );
}

function AutonomyDialog({
  currentLevel,
  submitting,
  onConfirm,
  onCancel,
}: {
  currentLevel: AutonomyLevel;
  submitting: boolean;
  onConfirm: (level: AutonomyLevel, reason: string) => void;
  onCancel: () => void;
}) {
  const targetLevel: AutonomyLevel =
    currentLevel === "autonomous" ? "assisted" : "autonomous";
  const [reason, setReason] = useState("");

  const goingAutonomous = targetLevel === "autonomous";

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.14 }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onCancel}
      role="dialog"
      aria-modal="true"
    >
      <motion.div
        initial={{ opacity: 0, y: 8, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.18, ease: "easeOut" }}
        className="w-full max-w-md overflow-hidden rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="border-b-[0.5px] border-[var(--color-border-tertiary)] px-5 py-4">
          <p className="text-[15px] font-medium tracking-tight">
            {goingAutonomous ? "Enable autonomous mode" : "Switch to assisted mode"}
          </p>
          <p className="mt-1 text-[12px] text-[var(--color-text-secondary)]">
            {goingAutonomous ? (
              <>
                Agents will run the loan end-to-end: intake → underwriting
                → decision. The orchestrator stops only at irreversible
                gates — sending the borrower email and transmitting the
                decision package — which stay human-only.
              </>
            ) : (
              <>
                The orchestrator will stop chaining. Every step from here
                will wait for explicit underwriter approval.
              </>
            )}
          </p>
        </header>

        <div className="px-5 py-4">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
              Reason
            </span>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              disabled={submitting}
              rows={3}
              placeholder={
                goingAutonomous
                  ? "e.g. Sponsor pre-approved; this matches existing portfolio; clean prior history."
                  : "e.g. Holding for committee review; want manual control through decision."
              }
              className="form-input"
            />
          </label>
        </div>

        <footer className="flex items-center justify-end gap-2 border-t-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-secondary)] px-5 py-3">
          <SecondaryButton type="button" onClick={onCancel} disabled={submitting}>
            Cancel
          </SecondaryButton>
          <PrimaryButton
            type="button"
            Icon={goingAutonomous ? IconBolt : IconHandStop}
            onClick={() => onConfirm(targetLevel, reason.trim())}
            disabled={submitting || reason.trim().length < 3}
          >
            {submitting
              ? "Saving…"
              : goingAutonomous
                ? "Enable autonomous"
                : "Switch to assisted"}
          </PrimaryButton>
        </footer>
      </motion.div>
    </motion.div>
  );
}
