"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { IconArrowLeft, IconArrowRight, IconLockSquare } from "@tabler/icons-react";
import { motion } from "motion/react";
import { toast } from "sonner";

import { useAuth } from "@/app/borrower/AuthProvider";

import { ProgressDots, checkCompleteness } from "./_components";
import { EMPTY, type FormState } from "./_shared";
import { StepAbout } from "./steps/StepAbout";
import { StepClass } from "./steps/StepClass";
import { StepFinances } from "./steps/StepFinances";
import { StepGuarantor } from "./steps/StepGuarantor";
import { StepLoan } from "./steps/StepLoan";
import { StepReview } from "./steps/StepReview";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * /apply — borrower-facing loan-application wizard.
 *
 * This file is the orchestrator only. Each step's body lives in
 * ``./steps/StepX.tsx`` so adding / editing a step is one focused
 * file rather than scrolling through 1300 lines of mixed setup,
 * validation, and per-step rendering. Shared types + form state +
 * the EMPTY initializer live in ``./_shared``; shared sub-components
 * (SectionCard, Field, LoanTypePicker, ProgressDots, ReviewSummary,
 * Checklist, checkCompleteness) live in ``./_components``.
 *
 * Wizard shape:
 *
 *   STEP 1 (class)     — pick personal vs business
 *   STEP 2 (about)     — borrower identity + password (new applicants)
 *   STEP 3 (loan)      — loan type, amount, property facts, purpose
 *   STEP 4a (finances) — personal only — income / debt / FICO / employer
 *   STEP 4b (guarantor) — business only — optional multi-row guarantor
 *   STEP 5 (review)    — read-only recap + checklist before Submit
 *
 * The submit mutation handles both new-account creation (atomically
 * via ``borrower_password``) and existing-account attach (via the
 * session cookie). 409 = email exists → toast routes to /login.
 */

/**
 * Marker error thrown by the apply mutation when the backend returns
 * 409 (email already on file). The onError handler matches on this
 * to route the user to /login instead of showing a generic error.
 */
class ApplyConflictError extends Error {
  constructor() {
    super("An account with that email already exists");
  }
}

export default function ApplyPage() {
  const router = useRouter();
  const auth = useAuth();
  // Seed the form with the signed-in user's name + email *on first
  // paint*. Without this initialiser, an already-authed user lands
  // on the page with empty fields and the Next button stuck disabled
  // because the validator sees an empty email. The setState-on-render
  // block below handles the mid-session "user signed in while page
  // was open" edge case.
  const [form, setForm] = useState<FormState>(() => {
    if (auth.status === "authed" && auth.user) {
      return {
        ...EMPTY,
        borrower_email: auth.user.email,
        borrower_name: auth.user.name,
      };
    }
    return EMPTY;
  });
  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((f) => ({ ...f, [k]: v }));

  // Pre-fill name/email when a signed-in borrower lands on /apply.
  // React-19 "set state during render with a guard" — no effect, so
  // no cascading-render warning, and the user sees their pre-filled
  // form on the first paint instead of a flicker.
  const [seenAuthUser, setSeenAuthUser] = useState(
    auth.status === "authed" ? auth.user : null,
  );
  const currentAuthUser = auth.status === "authed" ? auth.user : null;
  if (currentAuthUser && currentAuthUser !== seenAuthUser) {
    setSeenAuthUser(currentAuthUser);
    setForm((f) => ({
      ...f,
      borrower_email: f.borrower_email || currentAuthUser.email,
      borrower_name: f.borrower_name || currentAuthUser.name,
      // Already-authed users don't set a password here.
      borrower_password: "",
    }));
  }

  // Live completeness assessment. Mirrors what the intake agent will
  // do server-side — gives the borrower a sense of "I'm 60% there"
  // without any LLM call, and surfaces missing pieces immediately.
  const isAuthed = auth.status === "authed";
  const checklist = useMemo(
    () => checkCompleteness(form, isAuthed),
    [form, isAuthed],
  );

  const submit = useMutation({
    mutationFn: async () => {
      const body = {
        loan_class: form.loan_class,
        loan_type: form.loan_type,
        amount: Number(form.amount),
        purpose: form.purpose || null,
        // Personal-loan extras get sent on every payload — the backend
        // ignores them when class=business, and treats them as starter
        // values the intake agent can email-chase the rest of when
        // class=personal.
        annual_income: form.annual_income ? Number(form.annual_income) : null,
        employer: form.employer || null,
        credit_score: form.credit_score ? Number(form.credit_score) : null,
        monthly_debt_payments: form.monthly_debt_payments
          ? Number(form.monthly_debt_payments)
          : null,
        years_employment: form.years_employment
          ? Number(form.years_employment)
          : null,
        borrower: {
          name: form.borrower_name,
          party_type:
            form.loan_class === "personal" ? "person" : form.borrower_type,
          email: form.borrower_email || null,
        },
        // Backend creates the borrower account atomically with the
        // loan when this is set. Skipped for already-signed-in
        // users (they get the 409 path, handled in onError).
        borrower_password: form.borrower_password || null,
        // Only ship rows that are fully populated. Empty rows are
        // valid in the UI (they're how the user "discards" one),
        // but we don't want to round-trip them to the backend as
        // Party stubs.
        guarantors: form.guarantors
          .filter((g) => g.name.trim() && g.email.trim())
          .map((g) => ({
            name: g.name.trim(),
            party_type: "person" as const,
            email: g.email.trim(),
          })),
        property_address: form.property_address || null,
        property_type: form.property_type || null,
      };
      const res = await fetch(`${API_URL}/api/v1/borrower-portal/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        // Cookie ships along — needed so signed-in users get their
        // session refreshed (the apply endpoint sets a fresh cookie
        // for new borrowers and ignores it for existing ones).
        credentials: "include",
      });
      if (!res.ok) {
        // 409 = email already has an account. Surface a clear
        // "please sign in first" toast in onError below.
        if (res.status === 409) {
          throw new ApplyConflictError();
        }
        throw new Error(`Application failed (${res.status}): ${await res.text()}`);
      }
      return (await res.json()) as {
        loan_id: string;
        reference: string;
        stage: string;
        message: string;
      };
    },
    onSuccess: (result) => {
      // The backend set the session cookie for a new borrower; flag
      // the auth provider so the next /me-driven view shows them
      // signed in without a roundtrip.
      void auth.refresh();
      toast.success(`Application submitted — ${result.reference}`);
      router.push(`/apply/${result.loan_id}`);
    },
    onError: (e) => {
      if (e instanceof ApplyConflictError) {
        // 409: email exists. The right next step is "sign in, then
        // come back" — we route them to /login with the application's
        // email prefilled and ?next=/apply so they land back here.
        toast.error("That email already has an account", {
          description:
            "Sign in first, then we'll attach this application to your account.",
          action: {
            label: "Sign in",
            onClick: () =>
              router.push(
                `/login?next=${encodeURIComponent("/apply")}&email=${encodeURIComponent(form.borrower_email)}`,
              ),
          },
        });
        return;
      }
      toast.error("Couldn't submit application", {
        description: e instanceof Error ? e.message : String(e),
      });
    },
  });

  const ready = checklist.filter((c) => c.required).every((c) => c.satisfied);

  // ---- Wizard state -----------------------------------------------------
  // Steps run 1..N where N depends on loan_class. The first step picks
  // the class, so it shows up in both flows; from there the path forks.
  //
  // Why a wizard rather than a single-page form: applying for a loan
  // is consequential, and the form has 15+ fields. Asking the borrower
  // to fill all of them on one scroll page felt like homework.
  // Stepping it surfaces the structure ("first about you, then the
  // loan, then…") and lets us guide them with per-step validation
  // rather than a giant checklist at the bottom.
  const [step, setStep] = useState(1);
  const steps: { id: number; label: string; key: string }[] =
    form.loan_class === "personal"
      ? [
          { id: 1, label: "Loan type", key: "class" },
          { id: 2, label: "About you", key: "about" },
          { id: 3, label: "The loan", key: "loan" },
          { id: 4, label: "Finances", key: "finances" },
          { id: 5, label: "Review", key: "review" },
        ]
      : [
          { id: 1, label: "Loan type", key: "class" },
          { id: 2, label: "Business", key: "about" },
          { id: 3, label: "The loan", key: "loan" },
          { id: 4, label: "Guarantor", key: "guarantor" },
          { id: 5, label: "Review", key: "review" },
        ];
  const totalSteps = steps.length;
  const currentKey = steps.find((s) => s.id === step)?.key ?? "class";

  /** Per-step gating: can the user click Next? Returns either ``true``
   *  or a string explaining why not. The validation here is the
   *  required subset of ``checkCompleteness``; optional fields don't
   *  block progression. */
  const stepValid = ((): true | string => {
    if (currentKey === "class") return true; // default is set
    if (currentKey === "about") {
      if (form.borrower_name.trim().length < 2)
        return form.loan_class === "personal"
          ? "Add your full name."
          : "Add the entity name.";
      if (!/^[^@]+@[^@]+\.[^@]+$/.test(form.borrower_email))
        return "Add a valid contact email.";
      if (!isAuthed && form.borrower_password.length < 8)
        return "Create a password (8+ characters).";
      return true;
    }
    if (currentKey === "loan") {
      if (!(Number(form.amount) > 0)) return "Enter a loan amount.";
      return true;
    }
    if (currentKey === "finances") {
      if (!(Number(form.annual_income) > 0))
        return "Enter your annual income (gross).";
      return true;
    }
    if (currentKey === "guarantor") {
      // The whole step is optional — zero guarantors is a fine
      // commercial application. But if a row has been started,
      // both name + email must be filled. Half-filled rows would
      // otherwise round-trip as Party rows with no contact info,
      // which the case-file timeline can't email.
      const half = form.guarantors.find(
        (g) =>
          (g.name.trim() && !/^[^@]+@[^@]+\.[^@]+$/.test(g.email)) ||
          (!g.name.trim() && g.email.trim()),
      );
      if (half) {
        return "Each guarantor needs both a name and a valid email — or remove the row.";
      }
      return true;
    }
    return true;
  })();

  const canProceed = stepValid === true;
  const isLastStep = step === totalSteps;

  return (
    <div className="flex flex-col gap-6">
      {/* Signed-in banner. Shows up only for borrowers who already
          have an account and arrived at /apply for a second loan
          or by deep-linking. Quick way for them to sign out and
          start a different account if they really meant to. */}
      {auth.status === "authed" && auth.user && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-[var(--color-background-info)] px-3 py-2 text-[12px] text-[var(--color-text-info)]">
          <span>
            Signed in as <strong>{auth.user.email}</strong> — this
            application will attach to your account.
          </span>
          <button
            type="button"
            onClick={() => {
              void auth.logout();
            }}
            className="text-[11.5px] underline opacity-80 hover:opacity-100"
          >
            Sign out and start fresh
          </button>
        </div>
      )}

      {/* Wizard header — title + progress. */}
      <div className="flex flex-col gap-3 rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-5 py-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-[16px] font-medium tracking-tight">
            Apply for a loan
          </p>
          <p className="text-[11.5px] text-[var(--color-text-tertiary)]">
            Step {step} of {totalSteps} ·{" "}
            {steps.find((s) => s.id === step)?.label}
          </p>
        </div>
        <ProgressDots steps={steps} current={step} />
        {!isAuthed && step === 1 && (
          <p className="text-[12px] text-[var(--color-text-secondary)]">
            Already have an account?{" "}
            <Link
              href={`/login?next=${encodeURIComponent("/apply")}`}
              className="font-medium text-[var(--color-text-info)] hover:underline"
            >
              Sign in
            </Link>{" "}
            instead.
          </p>
        )}
        {step === 1 && (
          <p className="flex items-center gap-1.5 text-[11px] text-[var(--color-text-tertiary)]">
            <IconLockSquare size={11} />
            Your information is stored privately. You can come back to this
            link any time to check status or attach more documents.
          </p>
        )}
      </div>

      {/* Per-step bodies — orchestration only; rendering lives in
          ./steps/Step*.tsx. */}
      {currentKey === "class" && <StepClass form={form} setForm={setForm} />}
      {currentKey === "about" && (
        <StepAbout form={form} update={update} isAuthed={isAuthed} />
      )}
      {currentKey === "loan" && <StepLoan form={form} update={update} />}
      {currentKey === "finances" && form.loan_class === "personal" && (
        <StepFinances form={form} update={update} />
      )}
      {currentKey === "guarantor" && (
        <StepGuarantor form={form} update={update} />
      )}
      {currentKey === "review" && (
        <StepReview form={form} checklist={checklist} />
      )}

      {/* Wizard nav (Back / Next / Submit) */}
      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={() => setStep((s) => Math.max(1, s - 1))}
          disabled={step === 1}
          className="inline-flex items-center gap-1.5 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3 py-1.5 text-[12.5px] text-[var(--color-text-primary)] hover:bg-[var(--color-background-secondary)] disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <IconArrowLeft size={13} />
          Back
        </button>

        {/* Inline gating message — appears next to the Next button so the
            borrower understands why they're stuck. Empty when the step
            is valid; otherwise shows the first reason from stepValid. */}
        {typeof stepValid === "string" && (
          <p className="text-[11.5px] text-[var(--color-text-tertiary)]">
            {stepValid}
          </p>
        )}

        {!isLastStep ? (
          <motion.button
            whileTap={{ scale: 0.98 }}
            type="button"
            onClick={() => setStep((s) => Math.min(totalSteps, s + 1))}
            disabled={!canProceed}
            className="inline-flex items-center gap-1.5 rounded-md px-4 py-2 text-[13px] font-medium disabled:opacity-45"
            style={{
              background: "var(--color-brand)",
              color: "var(--color-brand-light)",
            }}
          >
            Next
            <IconArrowRight size={14} />
          </motion.button>
        ) : (
          <motion.button
            whileTap={{ scale: 0.98 }}
            onClick={() => submit.mutate()}
            disabled={!ready || submit.isPending}
            className="inline-flex items-center gap-1.5 rounded-md px-4 py-2 text-[13px] font-medium disabled:opacity-45"
            style={{
              background: "var(--color-brand)",
              color: "var(--color-brand-light)",
            }}
          >
            {submit.isPending ? "Submitting…" : "Submit application"}
            <IconArrowRight size={14} />
          </motion.button>
        )}
      </div>
    </div>
  );
}
