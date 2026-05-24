"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import {
  IconAlertCircle,
  IconArrowLeft,
  IconArrowRight,
  IconLockSquare,
} from "@tabler/icons-react";
import { motion } from "motion/react";
import { toast } from "sonner";

import { borrowerAuthApi, type ApiError } from "@/lib/borrowerApi";
import { useAuth } from "@/app/borrower/AuthProvider";
import { PrimaryButton } from "@/app/components/PrimaryButton";

/**
 * Reset-password page.
 *
 * Lands here from the magic-link in the password-reset email. The
 * token comes in as ``?token=<...>``; we POST it together with the
 * new password to ``/borrower-auth/password-reset/confirm`` which
 * burns the token + sets the cookie. On success the user is signed
 * in and lands on /account.
 *
 * Token missing or invalid: the backend returns 401 and we surface a
 * "ask for a new link" affordance rather than silently failing.
 */
export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordInner />
    </Suspense>
  );
}

function ResetPasswordInner() {
  const router = useRouter();
  const params = useSearchParams();
  const auth = useAuth();
  const token = params.get("token") ?? "";

  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const confirm = useMutation({
    mutationFn: () =>
      borrowerAuthApi.confirmPasswordReset({
        token,
        new_password: newPassword,
      }),
    onSuccess: async (user) => {
      auth.setUser(user);
      toast.success("Password set. You're signed in.");
      router.replace("/account");
    },
    onError: (e) => {
      const err = e as unknown as ApiError;
      const msg =
        err.status === 401
          ? "This reset link has expired or already been used. Request a new one."
          : err.message || "Something went wrong. Try again.";
      setError(msg);
    },
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (newPassword.length < 8) {
      setError("Use at least 8 characters.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("The two passwords don't match.");
      return;
    }
    if (!token) {
      setError("Missing reset token. Open the link from your email again.");
      return;
    }
    confirm.mutate();
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-[var(--color-background-secondary)] px-4 py-10">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.2 }}
        className="w-full max-w-md rounded-xl border border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-7 py-8 shadow-sm"
      >
        <Link
          href="/login"
          className="mb-4 inline-flex items-center gap-1.5 text-[12px] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
        >
          <IconArrowLeft size={12} /> Back to sign in
        </Link>
        <h1 className="text-[18px] font-semibold">Set a new password</h1>
        <p className="mt-1 text-[13px] text-[var(--color-text-secondary)]">
          Choose a new password for your account. We'll sign you in
          automatically.
        </p>

        {!token && (
          <div className="mt-5 flex items-start gap-2 rounded-md border border-[var(--color-border-tertiary)] bg-[var(--color-background-danger)] px-3 py-3 text-[12px]">
            <IconAlertCircle
              size={14}
              className="mt-[2px] shrink-0 text-[var(--color-text-danger)]"
            />
            <p>
              No reset token in the URL. Open the link from your email — or{" "}
              <Link href="/forgot-password" className="underline">
                request a new one
              </Link>
              .
            </p>
          </div>
        )}

        <form className="mt-5 flex flex-col gap-3" onSubmit={submit}>
          <label className="flex flex-col gap-1">
            <span className="text-[12px] font-medium text-[var(--color-text-secondary)]">
              New password
            </span>
            <span className="flex items-center gap-2 rounded-md border border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3 focus-within:border-[var(--color-text-primary)]">
              <IconLockSquare
                size={14}
                className="text-[var(--color-text-tertiary)]"
              />
              <input
                type="password"
                required
                autoComplete="new-password"
                minLength={8}
                disabled={!token}
                autoFocus
                className="h-9 w-full bg-transparent text-[13px] outline-none placeholder:text-[var(--color-text-tertiary)] disabled:opacity-50"
                placeholder="At least 8 characters"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
              />
            </span>
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-[12px] font-medium text-[var(--color-text-secondary)]">
              Confirm new password
            </span>
            <span className="flex items-center gap-2 rounded-md border border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3 focus-within:border-[var(--color-text-primary)]">
              <IconLockSquare
                size={14}
                className="text-[var(--color-text-tertiary)]"
              />
              <input
                type="password"
                required
                autoComplete="new-password"
                minLength={8}
                disabled={!token}
                className="h-9 w-full bg-transparent text-[13px] outline-none placeholder:text-[var(--color-text-tertiary)] disabled:opacity-50"
                placeholder="Type it again"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
              />
            </span>
          </label>

          {error && (
            <div className="flex items-start gap-2 rounded-md border border-[var(--color-border-tertiary)] bg-[var(--color-background-danger)] px-3 py-2 text-[12px] text-[var(--color-text-danger)]">
              <IconAlertCircle size={14} className="mt-[2px] shrink-0" />
              <p>{error}</p>
            </div>
          )}

          <PrimaryButton
            type="submit"
            disabled={!token || confirm.isPending}
            className="mt-1"
          >
            {confirm.isPending ? "Setting password…" : "Set password & sign in"}
            <IconArrowRight size={14} />
          </PrimaryButton>
        </form>
      </motion.div>
    </main>
  );
}
