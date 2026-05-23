"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { IconArrowRight, IconLockSquare, IconMail } from "@tabler/icons-react";
import { motion } from "motion/react";
import { toast } from "sonner";

import { borrowerAuthApi, type ApiError } from "@/lib/borrowerApi";
import { useAuth } from "@/app/borrower/AuthProvider";
import { PrimaryButton } from "@/app/components/PrimaryButton";
import { SecondaryButton } from "@/app/components/SecondaryButton";

/**
 * Borrower login page.
 *
 * Two modes, switchable inline without a route change:
 *  - **Password** — email + password → ``POST /borrower-auth/login``.
 *  - **Magic link** — email → ``POST /borrower-auth/magic-link/request``;
 *    the user clicks the link in their inbox.
 *
 * After successful login we read ``?next=…`` from the search params
 * to honour a "deep-link interrupted by auth" flow — typing
 * ``/apply/<id>`` while logged out lands here with the original
 * URL preserved.
 */
export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginInner />
    </Suspense>
  );
}

function LoginInner() {
  const router = useRouter();
  const params = useSearchParams();
  // Default post-login destination is the dashboard. The deep-link
  // case (``?next=/apply/abc-123``) preserves where the user
  // originally tried to go before getting bounced to login.
  const nextUrl = params.get("next") || "/account";
  const auth = useAuth();

  const [mode, setMode] = useState<"password" | "magic">("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const passwordLogin = useMutation({
    mutationFn: () => borrowerAuthApi.login({ email, password }),
    onSuccess: (user) => {
      auth.setUser(user);
      toast.success(`Welcome back, ${user.name || user.email}`);
      router.push(nextUrl);
    },
    onError: (e) => {
      const err = e as unknown as ApiError;
      toast.error(err.message || "Login failed");
    },
  });

  const magicLink = useMutation({
    mutationFn: () => borrowerAuthApi.requestMagicLink(email),
    onSuccess: (res) => {
      // In dev the URL ships in the response so testing doesn't
      // need a real inbox. In production it'd only land via email.
      if (res.magic_link_url) {
        toast.success("Magic link ready (dev mode)", {
          description: "Click the toast to open it.",
          action: {
            label: "Open link",
            onClick: () => {
              window.location.href = res.magic_link_url!;
            },
          },
          duration: 30_000,
        });
      } else {
        toast.success("Check your inbox", {
          description: `If an account exists for ${email}, we sent a sign-in link.`,
        });
      }
    },
    onError: (e) => {
      const err = e as unknown as ApiError;
      toast.error(err.message || "Couldn't send magic link");
    },
  });

  // Already signed in? Bounce out — they don't need to log in again.
  if (auth.status === "authed") {
    router.replace(nextUrl);
    return null;
  }

  const busy = passwordLogin.isPending || magicLink.isPending;

  return (
    <div className="mx-auto flex max-w-md flex-col gap-5 py-12">
      <header className="text-center">
        <p className="font-editorial text-[28px] tracking-tight">
          Sign in to Mkopo
        </p>
        <p className="mt-1.5 text-[13px] text-[var(--color-text-secondary)]">
          Borrowers, sign in to check your application status, upload
          documents, and message your underwriter.
        </p>
      </header>

      <motion.div
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.18 }}
        className="flex flex-col gap-4 rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-5 py-5"
      >
        {/* Mode picker — segmented control. Two paths the borrower
            cares about: password and magic-link. */}
        <div className="grid grid-cols-2 gap-1 rounded-md bg-[var(--color-background-secondary)] p-0.5">
          {(
            [
              { value: "password", label: "Password" },
              { value: "magic", label: "Email me a link" },
            ] as const
          ).map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setMode(opt.value)}
              className="rounded-[5px] py-1.5 text-[12px] font-medium transition-colors"
              style={{
                background:
                  mode === opt.value
                    ? "var(--color-background-primary)"
                    : "transparent",
                color:
                  mode === opt.value
                    ? "var(--color-text-primary)"
                    : "var(--color-text-secondary)",
              }}
            >
              {opt.label}
            </button>
          ))}
        </div>

        <label className="flex flex-col gap-1">
          <span className="text-[10.5px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
            <IconMail size={11} className="mr-1 inline-block" />
            Email
          </span>
          <input
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={busy}
            placeholder="you@example.com"
            className="form-input"
          />
        </label>

        {mode === "password" && (
          <label className="flex flex-col gap-1">
            <span className="text-[10.5px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
              <IconLockSquare size={11} className="mr-1 inline-block" />
              Password
            </span>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={busy}
              placeholder="••••••••"
              className="form-input"
            />
            <Link
              href="/forgot-password"
              className="mt-1 self-end text-[10.5px] text-[var(--color-text-info)] hover:underline"
            >
              Forgot password?
            </Link>
          </label>
        )}

        <PrimaryButton
          type="button"
          Icon={IconArrowRight}
          onClick={() =>
            mode === "password" ? passwordLogin.mutate() : magicLink.mutate()
          }
          disabled={
            busy ||
            !email.trim() ||
            (mode === "password" && !password.trim())
          }
        >
          {busy
            ? "Working…"
            : mode === "password"
              ? "Sign in"
              : "Send sign-in link"}
        </PrimaryButton>
      </motion.div>

      <div className="text-center text-[12.5px] text-[var(--color-text-secondary)]">
        New here?{" "}
        <Link
          href={`/signup?next=${encodeURIComponent(nextUrl)}`}
          className="font-medium text-[var(--color-text-info)] hover:underline"
        >
          Create an account
        </Link>{" "}
        or{" "}
        <Link
          href="/apply"
          className="font-medium text-[var(--color-text-info)] hover:underline"
        >
          start a new application
        </Link>
        .
      </div>

      <SecondaryButton
        type="button"
        onClick={() => router.push("/")}
        className="self-center"
      >
        ← Back to home
      </SecondaryButton>
    </div>
  );
}
