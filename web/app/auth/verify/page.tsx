"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { IconAlertCircle, IconLoader2 } from "@tabler/icons-react";
import { motion } from "motion/react";
import { toast } from "sonner";

import { borrowerAuthApi, type ApiError } from "@/lib/borrowerApi";
import { useAuth } from "@/app/borrower/AuthProvider";
import { PrimaryButton } from "@/app/components/PrimaryButton";

/**
 * Magic-link dispatcher.
 *
 * Every outbound magic-link email points here:
 * ``/auth/verify?purpose=<login|email_verify|password_reset|set_password>&token=<...>``.
 * This page reads ``purpose`` and either:
 *
 *   - **login** ⟶ POST to ``magic-link/consume`` and land on /account
 *     (or wherever ``?next=`` says).
 *   - **email_verify** ⟶ same as login: it sets the cookie and stamps
 *     ``email_verified_at``. The user lands on /account.
 *   - **password_reset** / **set_password** ⟶ redirect to
 *     /reset-password?token=... where the new-password form lives.
 *
 * Token is consumed exactly once; subsequent loads of this URL after
 * a successful consume will land on the error state.
 */
export default function VerifyPage() {
  return (
    <Suspense fallback={null}>
      <VerifyInner />
    </Suspense>
  );
}

function VerifyInner() {
  const router = useRouter();
  const params = useSearchParams();
  const auth = useAuth();
  const token = params.get("token") ?? "";
  const purpose = params.get("purpose") ?? "";
  // ``loan_id`` is sent by the staff-initiated loan-invite email so
  // we can drop the borrower directly onto their case page rather
  // than a generic /account list. Optional — falls back to ``next``
  // for other purposes.
  const loanId = params.get("loan_id");
  const nextUrl =
    purpose === "loan_invite" && loanId
      ? `/apply/${loanId}`
      : params.get("next") || "/account";

  // Derive initial state from query params at mount. Avoids the
  // synchronous setState-in-effect that the lint rule (rightly)
  // flags — the only state changes inside the effect are on async
  // resolution, which the rule allows.
  const linkValid = Boolean(token && purpose);
  const isPasswordReset =
    purpose === "password_reset" || purpose === "set_password";
  const [error, setError] = useState<string | null>(
    linkValid
      ? null
      : "This link is missing required information. Try again from your email.",
  );
  // ``working`` is true only while we're actively consuming a
  // login/email-verify token. Password-reset purposes do nothing
  // here except redirect, so we don't claim to be working.
  const [working, setWorking] = useState(linkValid && !isPasswordReset);

  useEffect(() => {
    if (!linkValid) return;

    // Password-reset purposes don't consume the token here — the
    // /reset-password page does that after the user picks a new
    // password. We just forward.
    if (isPasswordReset) {
      router.replace(
        `/reset-password?token=${encodeURIComponent(token)}&purpose=${purpose}`,
      );
      return;
    }

    // Login / email-verify both go through the same POST. The backend
    // figures out which one this token was minted for; we don't have
    // to.
    let cancelled = false;
    (async () => {
      try {
        const user = await borrowerAuthApi.consumeMagicLink(token);
        if (cancelled) return;
        auth.setUser(user);
        toast.success(
          purpose === "email_verify"
            ? "Email confirmed."
            : purpose === "loan_invite"
              ? "Welcome — opening your application."
              : "Signed in.",
        );
        router.replace(nextUrl);
      } catch (e) {
        if (cancelled) return;
        const err = e as unknown as ApiError;
        setError(
          err.status === 401
            ? "This link has expired or has already been used. Request a new one."
            : err.message || "Couldn't verify this link.",
        );
        setWorking(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [linkValid, isPasswordReset, token, purpose, nextUrl, router, auth]);

  return (
    <main className="flex min-h-screen items-center justify-center bg-[var(--color-background-secondary)] px-4 py-10">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.2 }}
        className="w-full max-w-md rounded-xl border border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-7 py-8 shadow-sm"
      >
        {working && !error && (
          <div className="flex items-center gap-2 text-[13px] text-[var(--color-text-secondary)]">
            <IconLoader2 size={16} className="animate-spin" />
            <span>Verifying your link…</span>
          </div>
        )}

        {error && (
          <div className="flex flex-col gap-4">
            <div className="flex items-start gap-2 rounded-md border border-[var(--color-border-tertiary)] bg-[var(--color-background-danger)] px-3 py-3 text-[12.5px]">
              <IconAlertCircle
                size={16}
                className="mt-[2px] shrink-0 text-[var(--color-text-danger)]"
              />
              <p>{error}</p>
            </div>
            <div className="flex flex-col gap-2">
              <Link href="/login">
                <PrimaryButton className="w-full">Back to sign in</PrimaryButton>
              </Link>
              <Link
                href="/forgot-password"
                className="text-center text-[12px] text-[var(--color-text-secondary)] hover:underline"
              >
                Request a new password reset link →
              </Link>
            </div>
          </div>
        )}
      </motion.div>
    </main>
  );
}
