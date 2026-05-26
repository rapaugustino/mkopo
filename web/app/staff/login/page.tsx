"use client";

/**
 * Staff console login page.
 *
 * Distinct from the borrower login at ``/login`` — this is for
 * internal users (underwriters + admins). The auth path lives at
 * ``POST /api/v1/staff/auth/login`` and sets a separate cookie
 * (``mkopo_staff_session``) with its own JWT audience, so the two
 * surfaces are isolated even when both cookies coexist on the
 * same domain.
 *
 * UX: centred card, brand-prominent, minimal surface area. ``?next=``
 * comes from the API helper when a 401 fires elsewhere — we bounce
 * back to the originally-intended URL on success.
 *
 * Dev seed credentials are surfaced in the form's footer when
 * ``NEXT_PUBLIC_ENVIRONMENT !== "production"``. They're explicit on
 * purpose — the alternative is operators re-creating accounts every
 * fresh clone, which is friction without security gain in dev.
 */

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { IconLogin, IconShieldLock } from "@tabler/icons-react";
import { api } from "@/lib/api";

function StaffLoginInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const next = searchParams.get("next") || "/";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // If the user is already signed in, bounce — saves them from
  // staring at a login form they don't need.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await api.getStaffMe();
        if (!cancelled) router.replace(next);
      } catch {
        /* not signed in — stay on the form */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [next, router]);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await api.staffLogin(email, password);
      router.replace(next);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(
        msg.includes("401") || msg.includes("Invalid")
          ? "Invalid email or password."
          : msg.includes("429")
            ? "Too many attempts. Wait a few minutes and try again."
            : "Sign-in failed. Try again in a moment.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main
      className="flex min-h-screen items-center justify-center px-4"
      style={{ background: "var(--color-background-secondary)" }}
    >
      <div
        className="w-full max-w-[400px] rounded-lg border-[0.5px] p-7 shadow-sm"
        style={{
          background: "var(--color-background-primary)",
          borderColor: "var(--color-border-tertiary)",
        }}
      >
        <div className="mb-5 flex items-center gap-2.5">
          <span
            className="flex h-9 w-9 items-center justify-center rounded-md"
            style={{ background: "var(--color-brand)", color: "white" }}
          >
            <IconShieldLock size={18} />
          </span>
          <div>
            <p
              className="text-[16px] font-semibold"
              style={{ color: "var(--color-text-primary)" }}
            >
              Mkopo
            </p>
            <p
              className="text-[12px]"
              style={{ color: "var(--color-text-tertiary)" }}
            >
              Sign in to the staff console
            </p>
          </div>
        </div>

        <form onSubmit={onSubmit} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1">
            <span
              className="text-[11px] font-medium uppercase tracking-[0.04em]"
              style={{ color: "var(--color-text-secondary)" }}
            >
              Email
            </span>
            <input
              type="email"
              required
              autoFocus
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="form-input"
              placeholder="you@yourlender.com"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span
              className="text-[11px] font-medium uppercase tracking-[0.04em]"
              style={{ color: "var(--color-text-secondary)" }}
            >
              Password
            </span>
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="form-input"
              placeholder="Enter your password"
            />
          </label>

          {error && (
            <p
              className="rounded-md px-3 py-2 text-[12px]"
              style={{
                background: "var(--color-background-danger)",
                color: "var(--color-text-danger)",
              }}
              role="alert"
            >
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={submitting || !email || !password}
            className="mt-1 flex items-center justify-center gap-1.5 rounded-md px-3 py-2 text-[13px] font-medium transition-opacity disabled:cursor-not-allowed disabled:opacity-60"
            style={{ background: "var(--color-brand)", color: "white" }}
          >
            <IconLogin size={14} />
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>

        {process.env.NEXT_PUBLIC_ENVIRONMENT !== "production" && (
          <div
            className="mt-5 rounded-md border-[0.5px] p-2.5 text-[11px]"
            style={{
              borderColor: "var(--color-border-tertiary)",
              color: "var(--color-text-tertiary)",
            }}
          >
            <p
              className="mb-1 font-medium"
              style={{ color: "var(--color-text-secondary)" }}
            >
              Dev seed accounts
            </p>
            <p>
              <code>j.davis@mkopo.dev</code> / <code>password123</code>{" "}
              (underwriter)
            </p>
            <p>
              <code>admin@mkopo.dev</code> / <code>password123</code> (admin)
            </p>
          </div>
        )}
      </div>
    </main>
  );
}

export default function StaffLoginPage() {
  // useSearchParams requires Suspense in app-router pages.
  return (
    <Suspense fallback={null}>
      <StaffLoginInner />
    </Suspense>
  );
}
