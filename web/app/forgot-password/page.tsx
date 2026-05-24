"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import {
  IconArrowLeft,
  IconArrowRight,
  IconMail,
  IconMailCheck,
} from "@tabler/icons-react";
import { motion } from "motion/react";

import { borrowerAuthApi, type ApiError } from "@/lib/borrowerApi";
import { PrimaryButton } from "@/app/components/PrimaryButton";

/**
 * Forgot-password page.
 *
 * Anti-enumeration semantics: the backend ALWAYS returns 200 for
 * ``POST /borrower-auth/password-reset/request`` regardless of
 * whether the email is on file. The UI mirrors that — we never tell
 * the visitor whether the email matched an account, just that "if
 * an account exists, a link is on its way". A stranger probing
 * email addresses learns nothing.
 *
 * In dev mode the backend echoes the magic-link URL in the response
 * body so reset can be tested without an inbox; we surface it as a
 * dev-only "Open the link directly" affordance.
 */
export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [devLinkUrl, setDevLinkUrl] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  const request = useMutation({
    mutationFn: () =>
      borrowerAuthApi.requestPasswordReset(email.trim().toLowerCase()),
    onSuccess: (res) => {
      // Dev-only URL — present only when RESEND_API_KEY is unset and
      // the server returns ``magic_link_url`` for testing convenience.
      setDevLinkUrl(res.magic_link_url ?? null);
      setSubmitted(true);
    },
    onError: () => {
      // Even on a transient error we land on the success screen — we
      // never want to leak whether the email exists. The user can
      // try again from there.
      setSubmitted(true);
    },
  });

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
        <h1 className="text-[18px] font-semibold">Forgot your password?</h1>
        <p className="mt-1 text-[13px] text-[var(--color-text-secondary)]">
          Enter your email and we'll send a one-time link to set a new one.
        </p>

        {!submitted ? (
          <form
            className="mt-5 flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (email.trim()) request.mutate();
            }}
          >
            <label className="flex flex-col gap-1">
              <span className="text-[12px] font-medium text-[var(--color-text-secondary)]">
                Email
              </span>
              <span className="flex items-center gap-2 rounded-md border border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3 focus-within:border-[var(--color-text-primary)]">
                <IconMail size={14} className="text-[var(--color-text-tertiary)]" />
                <input
                  type="email"
                  required
                  autoComplete="email"
                  autoFocus
                  className="h-9 w-full bg-transparent text-[13px] outline-none placeholder:text-[var(--color-text-tertiary)]"
                  placeholder="you@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </span>
            </label>

            <PrimaryButton
              type="submit"
              disabled={!email.trim() || request.isPending}
              className="mt-1"
            >
              {request.isPending ? "Sending…" : "Send reset link"}
              <IconArrowRight size={14} />
            </PrimaryButton>
          </form>
        ) : (
          <SuccessPanel email={email} devLinkUrl={devLinkUrl} />
        )}
      </motion.div>
    </main>
  );
}

/** Confirmation screen. Deliberately doesn't say "we sent an email"
 *  — say "if an account exists, a link is on the way" so we don't
 *  leak account existence to a probing visitor. */
function SuccessPanel({
  email,
  devLinkUrl,
}: {
  email: string;
  devLinkUrl: string | null;
}) {
  return (
    <div className="mt-6 flex flex-col gap-4">
      <div className="flex items-start gap-3 rounded-md border border-[var(--color-border-tertiary)] bg-[var(--color-background-secondary)] px-3 py-3">
        <IconMailCheck
          size={18}
          className="mt-[2px] shrink-0 text-[var(--color-brand)]"
        />
        <div className="text-[12.5px] leading-relaxed">
          <p className="font-medium">Check your inbox.</p>
          <p className="mt-0.5 text-[var(--color-text-secondary)]">
            If an account exists for{" "}
            <span className="font-medium text-[var(--color-text-primary)]">
              {email}
            </span>
            , a reset link is on its way. It expires in 15 minutes and can only
            be used once.
          </p>
        </div>
      </div>

      {devLinkUrl && (
        <div className="rounded-md border border-dashed border-[var(--color-border-tertiary)] bg-[var(--color-background-secondary)] px-3 py-3 text-[11px]">
          <p className="mb-1 font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
            Dev mode
          </p>
          <p className="mb-2 text-[var(--color-text-secondary)]">
            Email isn't configured. Open the reset link directly:
          </p>
          <Link
            href={devLinkUrl}
            className="inline-flex break-all rounded bg-[var(--color-background-primary)] px-2 py-1 font-mono text-[11px] text-[var(--color-brand)] hover:underline"
          >
            {devLinkUrl}
          </Link>
        </div>
      )}

      <p className="text-[12px] text-[var(--color-text-tertiary)]">
        Didn't get an email? Check your spam folder, or{" "}
        <Link
          href="/forgot-password"
          className="text-[var(--color-text-primary)] underline"
          onClick={(e) => {
            // Force a fresh form by reloading — simpler than wiring
            // state back to the parent.
            e.preventDefault();
            window.location.reload();
          }}
        >
          try a different email
        </Link>
        .
      </p>
    </div>
  );
}
