"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { IconAlertTriangle, IconLockSquare, IconX } from "@tabler/icons-react";
import { motion } from "motion/react";

import { borrowerAuthApi, type ApiError } from "@/lib/borrowerApi";
import { PrimaryButton } from "@/app/components/PrimaryButton";
import { SecondaryButton } from "@/app/components/SecondaryButton";

/**
 * Re-auth prompt for irreversible actions (withdraw + erasure).
 *
 * Why this exists (#169): a stolen session cookie alone can't trigger
 * a destructive action — the user must also prove they know their
 * current password. The modal mints a one-shot challenge token and
 * hands it back to the parent, which forwards it to the actual
 * destructive endpoint.
 *
 * The challenge expires after 5 minutes; if the parent's destructive
 * call returns 403, the user has to re-enter their password. The modal
 * doesn't try to handle that retry — it's a tight enough window that
 * "click the action again" is the simplest recovery.
 */
interface Props {
  /** Headline shown above the password field. e.g. "Withdraw your
   *  application?", "Erase your account?". */
  title: string;
  /** One- or two-sentence summary of what's about to happen. Keep it
   *  short — the user already read the longer copy on the underlying
   *  card. */
  description: string;
  /** Confirm-button label. Often the same as the destructive verb so
   *  the user understands exactly which action they're authorising. */
  confirmLabel: string;
  /** Renders the primary button as red rather than brand-green. Use
   *  for the most loaded actions (erasure especially). Defaults to
   *  ``danger`` because that's the only context this modal is used in. */
  variant?: "danger" | "primary";
  /** Called with the minted challenge token after a successful
   *  password check. The parent does the actual destructive request. */
  onConfirm: (challengeToken: string) => Promise<void> | void;
  /** Called when the user clicks Cancel, presses Esc, or clicks the
   *  backdrop. The parent unmounts the modal. */
  onClose: () => void;
}

export function ReauthPromptModal({
  title,
  description,
  confirmLabel,
  variant = "danger",
  onConfirm,
  onClose,
}: Props) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const mint = useMutation({
    mutationFn: () => borrowerAuthApi.mintChallenge(password),
    onSuccess: async (res) => {
      try {
        await onConfirm(res.token);
        // Parent is responsible for closing on its own success;
        // most likely it'll unmount us via state.
      } catch (e) {
        const err = e as unknown as ApiError;
        setError(err.message || "Couldn't complete the action.");
      }
    },
    onError: (e) => {
      const err = e as unknown as ApiError;
      setError(
        err.status === 401
          ? "Password incorrect."
          : err.status === 400
            ? err.message ||
              "Set a password on your account first, then try again."
            : err.message || "Couldn't verify your password.",
      );
    },
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!password) return;
    mint.mutate();
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "var(--color-overlay-strong)" }}
      onClick={onClose}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.15 }}
        className="relative w-full max-w-sm rounded-xl border border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <span
              className="inline-flex h-7 w-7 items-center justify-center rounded-md"
              style={{
                background: "var(--color-background-warning)",
                color: "var(--color-text-warning)",
              }}
            >
              <IconAlertTriangle size={14} />
            </span>
            <h2 className="text-[14px] font-semibold">{title}</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-6 w-6 items-center justify-center rounded text-[var(--color-text-secondary)] hover:bg-[var(--color-background-secondary)]"
            title="Cancel"
          >
            <IconX size={12} />
          </button>
        </div>

        <p className="mb-4 text-[12.5px] leading-relaxed text-[var(--color-text-secondary)]">
          {description}
        </p>

        <form className="flex flex-col gap-3" onSubmit={submit}>
          <label className="flex flex-col gap-1">
            <span className="text-[11.5px] font-medium text-[var(--color-text-secondary)]">
              Confirm your password
            </span>
            <span className="flex items-center gap-2 rounded-md border border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3 focus-within:border-[var(--color-text-primary)]">
              <IconLockSquare
                size={13}
                className="text-[var(--color-text-tertiary)]"
              />
              <input
                type="password"
                required
                autoComplete="current-password"
                autoFocus
                className="h-9 w-full bg-transparent text-[13px] outline-none placeholder:text-[var(--color-text-tertiary)]"
                placeholder="Your account password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </span>
          </label>

          {error && (
            <p className="rounded-md border border-[var(--color-border-tertiary)] bg-[var(--color-background-danger)] px-2.5 py-2 text-[11.5px] text-[var(--color-text-danger)]">
              {error}
            </p>
          )}

          <div className="mt-1 flex justify-end gap-2">
            <SecondaryButton onClick={onClose} type="button">
              Cancel
            </SecondaryButton>
            {variant === "danger" ? (
              <button
                type="submit"
                disabled={!password || mint.isPending}
                className="flex items-center gap-1 rounded-md px-3 py-1.5 text-xs font-medium disabled:opacity-50"
                style={{
                  background: "var(--color-text-danger)",
                  color: "var(--color-background-danger)",
                }}
              >
                {mint.isPending ? "Verifying…" : confirmLabel}
              </button>
            ) : (
              <PrimaryButton
                type="submit"
                disabled={!password || mint.isPending}
              >
                {mint.isPending ? "Verifying…" : confirmLabel}
              </PrimaryButton>
            )}
          </div>
        </form>
      </motion.div>
    </div>
  );
}
