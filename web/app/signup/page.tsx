"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { IconArrowRight, IconLockSquare, IconMail, IconUser } from "@tabler/icons-react";
import { motion } from "motion/react";
import { toast } from "sonner";

import { borrowerAuthApi, type ApiError } from "@/lib/borrowerApi";
import { useAuth } from "@/app/borrower/AuthProvider";
import { PrimaryButton } from "@/app/components/PrimaryButton";

/**
 * Borrower signup. Creates the account + signs the user in via
 * the session cookie set by ``POST /borrower-auth/signup``.
 *
 * UX choices:
 *  - One screen, three fields (name, email, password). Anything
 *    more (DOB, address, etc.) belongs on the application form
 *    itself, not in auth.
 *  - Password ≥ 8 chars, enforced both client-side (button stays
 *    disabled) and server-side (Pydantic min_length).
 *  - 409 from the backend → toast + link to /login. We *do* leak
 *    "email already exists" here because the UX otherwise breaks;
 *    rate-limiting is the right mitigation.
 */
export default function SignupPage() {
  return (
    <Suspense fallback={null}>
      <SignupInner />
    </Suspense>
  );
}

function SignupInner() {
  const router = useRouter();
  const params = useSearchParams();
  // Default post-signup destination is the dashboard, where they'll
  // see "No applications yet" + a "Start a new application" CTA.
  const nextUrl = params.get("next") || "/account";
  const auth = useAuth();

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const signup = useMutation({
    mutationFn: () => borrowerAuthApi.signup({ email, password, name }),
    onSuccess: (user) => {
      auth.setUser(user);
      toast.success(`Welcome, ${user.name || user.email}`);
      router.push(nextUrl);
    },
    onError: (e) => {
      const err = e as unknown as ApiError;
      if (err.status === 409) {
        toast.error("An account with that email already exists", {
          description: "Try signing in instead.",
          action: {
            label: "Sign in",
            onClick: () =>
              router.push(
                `/login?next=${encodeURIComponent(nextUrl)}&email=${encodeURIComponent(email)}`,
              ),
          },
        });
      } else {
        toast.error(err.message || "Signup failed");
      }
    },
  });

  if (auth.status === "authed") {
    router.replace(nextUrl);
    return null;
  }

  const pwOk = password.length >= 8;
  const emailOk = /^[^@]+@[^@]+\.[^@]+$/.test(email);
  const ready = emailOk && pwOk && !signup.isPending;

  return (
    <div className="mx-auto flex max-w-md flex-col gap-5 py-12">
      <header className="text-center">
        <p className="font-editorial text-[28px] tracking-tight">
          Create your Mkopo account
        </p>
        <p className="mt-1.5 text-[13px] text-[var(--color-text-secondary)]">
          Just a name, email, and password. Your loan-application details
          come next.
        </p>
      </header>

      <motion.div
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.18 }}
        className="flex flex-col gap-4 rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-5 py-5"
      >
        <label className="flex flex-col gap-1">
          <span className="text-[10.5px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
            <IconUser size={11} className="mr-1 inline-block" />
            Your name
          </span>
          <input
            type="text"
            autoComplete="name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={signup.isPending}
            placeholder="Maya Patel"
            className="form-input"
          />
        </label>

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
            disabled={signup.isPending}
            placeholder="you@example.com"
            className="form-input"
          />
        </label>

        <label className="flex flex-col gap-1">
          <span className="text-[10.5px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
            <IconLockSquare size={11} className="mr-1 inline-block" />
            Password
          </span>
          <input
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={signup.isPending}
            placeholder="At least 8 characters"
            className="form-input"
            minLength={8}
          />
          <span
            className="mt-0.5 text-[10.5px]"
            style={{
              color: password.length === 0
                ? "var(--color-text-tertiary)"
                : pwOk
                  ? "var(--color-text-success)"
                  : "var(--color-text-warning)",
            }}
          >
            {password.length === 0
              ? "Use 8+ characters. A passphrase is great."
              : pwOk
                ? "Good."
                : `${8 - password.length} more character${8 - password.length === 1 ? "" : "s"} to go.`}
          </span>
        </label>

        <PrimaryButton
          type="button"
          Icon={IconArrowRight}
          onClick={() => signup.mutate()}
          disabled={!ready}
        >
          {signup.isPending ? "Creating account…" : "Create account"}
        </PrimaryButton>
      </motion.div>

      <div className="text-center text-[12.5px] text-[var(--color-text-secondary)]">
        Already have an account?{" "}
        <Link
          href={`/login?next=${encodeURIComponent(nextUrl)}`}
          className="font-medium text-[var(--color-text-info)] hover:underline"
        >
          Sign in
        </Link>
      </div>
    </div>
  );
}
