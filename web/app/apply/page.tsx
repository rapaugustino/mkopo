"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import {
  IconArrowLeft,
  IconArrowRight,
  IconBuilding,
  IconCheck,
  IconCircleDashed,
  IconLockSquare,
  IconUserCircle,
} from "@tabler/icons-react";
import { motion } from "motion/react";
import { toast } from "sonner";

import { useAuth } from "@/app/borrower/AuthProvider";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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

type LoanType = "bridge" | "permanent" | "construction" | "refinance";
type LoanClass = "business" | "personal";

interface FormState {
  loan_class: LoanClass;
  loan_type: LoanType;
  amount: string;
  purpose: string;
  borrower_name: string;
  borrower_email: string;
  // Password for new applicants — backend creates the borrower
  // account atomically with the loan. Empty string for already-
  // signed-in users (we pre-fill name+email and hide the password
  // field) and for "passwordless / magic-link only" intent.
  borrower_password: string;
  borrower_type: "entity" | "person";
  guarantor_name: string;
  guarantor_email: string;
  property_address: string;
  property_type: string;
  // Personal-loan-only inputs. Optional even when class is personal
  // — the intake agent will email-chase any missing items.
  annual_income: string;
  employer: string;
  credit_score: string;
  monthly_debt_payments: string;
  years_employment: string;
}

const EMPTY: FormState = {
  loan_class: "business",
  loan_type: "bridge",
  amount: "",
  purpose: "",
  borrower_name: "",
  borrower_email: "",
  borrower_password: "",
  borrower_type: "entity",
  guarantor_name: "",
  guarantor_email: "",
  property_address: "",
  property_type: "",
  annual_income: "",
  employer: "",
  credit_score: "",
  monthly_debt_payments: "",
  years_employment: "",
};

const PROPERTY_TYPES = [
  "Multifamily",
  "Office",
  "Retail",
  "Industrial",
  "Hotel",
  "Mixed-use",
  "Self-storage",
  "Other",
];

// Per-class loan-type pickers. The wire enum stays the same four
// values (avoids a schema migration), but the labels and hints
// reframe each option for the audience: a small-business owner
// reading "Bridge — interest-only, 12-36 months" does not need the
// same copy as a homeowner picking "Short-term" for a kitchen
// renovation. Personal loans don't have a construction equivalent so
// that option is omitted.
const LOAN_TYPE_OPTIONS_BUSINESS: { value: LoanType; label: string; hint: string }[] = [
  {
    value: "bridge",
    label: "Bridge",
    hint: "Short-term, interest-only, 12-36 months.",
  },
  {
    value: "permanent",
    label: "Permanent",
    hint: "Long-term financing for a stabilized asset.",
  },
  {
    value: "construction",
    label: "Construction",
    hint: "Draw facility for ground-up build or major capex.",
  },
  {
    value: "refinance",
    label: "Refinance",
    hint: "Replace existing debt.",
  },
];

const LOAN_TYPE_OPTIONS_PERSONAL: { value: LoanType; label: string; hint: string }[] = [
  {
    value: "bridge",
    label: "Short-term",
    hint: "Pay off within 12-24 months. Interest-only options available.",
  },
  {
    value: "permanent",
    label: "Long-term",
    hint: "3-7 year term with fixed monthly payments.",
  },
  {
    value: "refinance",
    label: "Refinance",
    hint: "Roll an existing personal loan into better terms.",
  },
];

export default function ApplyPage() {
  const router = useRouter();
  const auth = useAuth();
  const [form, setForm] = useState<FormState>(EMPTY);
  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((f) => ({ ...f, [k]: v }));

  // If the user is already signed in, prefill their email + name
  // and hide the password field. Submitting the form for a logged-
  // in user would 409 on the backend (email exists) — we don't
  // surface that path here; we just route them through /signup
  // which already exists. Actually, simpler: a signed-in user
  // clicking /apply should be making a *second* application —
  // their email is already on file, so the application has to
  // attach to them somehow. For now we route them to the status
  // page (they have at most one in-flight application in the
  // demo's data model) and let Phase 2's "My applications"
  // dashboard handle multi-application UX.
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
  const checklist = useMemo(() => checkCompleteness(form, auth.status === "authed"), [form, auth.status]);

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
        guarantors: form.guarantor_name
          ? [
              {
                name: form.guarantor_name,
                party_type: "person",
                email: form.guarantor_email || null,
              },
            ]
          : [],
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
        // 409: email exists. The right next step is "sign in,
        // then come back" — we route them to /login with the
        // application's email prefilled and ?next=/apply so they
        // land back here.
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
  // Why a wizard rather than the previous single-page form: applying
  // for a loan is consequential, and the form has 15+ fields. Asking
  // the borrower to fill all of them on one scroll page felt like
  // homework. Stepping it surfaces the structure ("first about you,
  // then the loan, then…") and lets us guide them with per-step
  // validation rather than a giant checklist at the bottom.
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
      if (auth.status !== "authed" && form.borrower_password.length < 8)
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
    if (currentKey === "guarantor") return true; // optional throughout
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

      {/* Wizard header — title + progress. The hero copy moved here
          and shrank because the wizard scaffold itself signals the
          shape of the flow (step N of M) without prose. */}
      <div className="flex flex-col gap-3 rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-5 py-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-[16px] font-medium tracking-tight">
            Apply for a loan
          </p>
          <p className="text-[11.5px] text-[var(--color-text-tertiary)]">
            Step {step} of {totalSteps} · {steps.find((s) => s.id === step)?.label}
          </p>
        </div>
        <ProgressDots steps={steps} current={step} />
        {auth.status !== "authed" && step === 1 && (
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

      {/* ---- STEP 1: Loan class --------------------------------------- */}
      {currentKey === "class" && (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-5 py-4">
        <p className="mb-2.5 text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
          What kind of loan?
        </p>
        <div className="grid grid-cols-2 gap-2">
          {(
            [
              {
                value: "business" as const,
                title: "Business / commercial",
                hint: "Backed by a property or business asset. DSCR-driven underwriting.",
              },
              {
                value: "personal" as const,
                title: "Personal",
                hint: "An individual borrower. Underwriting reviews income, credit, and existing debt.",
              },
            ] satisfies { value: LoanClass; title: string; hint: string }[]
          ).map((opt) => {
            const active = form.loan_class === opt.value;
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => {
                  setForm((f) => ({
                    ...f,
                    loan_class: opt.value,
                    // "construction" only applies to commercial loans —
                    // reset it if the user pivots to personal so the
                    // submitted loan_type is one a personal lender
                    // would actually offer. Also wipe property fields
                    // because the form hides them for personal.
                    loan_type:
                      opt.value === "personal" && f.loan_type === "construction"
                        ? "bridge"
                        : f.loan_type,
                    property_type: opt.value === "personal" ? "" : f.property_type,
                    property_address:
                      opt.value === "personal" ? "" : f.property_address,
                    borrower_type:
                      opt.value === "personal" ? "person" : f.borrower_type,
                  }));
                }}
                className="rounded-md px-3 py-3 text-left transition-colors"
                style={{
                  background: active
                    ? "var(--color-background-success)"
                    : "var(--color-background-primary)",
                  borderColor: active
                    ? "var(--color-brand)"
                    : "var(--color-border-tertiary)",
                  borderWidth: active ? 1 : 0.5,
                  borderStyle: "solid",
                }}
              >
                <p
                  className="text-[13.5px] font-medium"
                  style={{
                    color: active
                      ? "var(--color-brand)"
                      : "var(--color-text-primary)",
                  }}
                >
                  {opt.title}
                </p>
                <p className="mt-0.5 text-[11.5px] text-[var(--color-text-tertiary)]">
                  {opt.hint}
                </p>
              </button>
            );
          })}
        </div>
      </div>
      )}

      {/* ---- STEP 2: About you / Your business ------------------------ */}
      {currentKey === "about" && (
      <SectionCard
        icon={form.loan_class === "personal" ? IconUserCircle : IconBuilding}
        title={form.loan_class === "personal" ? "About you" : "Your business"}
        description={
          form.loan_class === "personal"
            ? "Your name and the email we should use to reach you."
            : "The legal entity applying for the loan."
        }
      >
        <div className="grid grid-cols-2 gap-3">
          <Field
            label={form.loan_class === "personal" ? "Full name" : "Entity name"}
            hint={
              form.loan_class === "personal"
                ? "As it appears on a government-issued ID"
                : "LLC, Inc, partnership, or individual"
            }
          >
            <input
              type="text"
              value={form.borrower_name}
              onChange={(e) => update("borrower_name", e.target.value)}
              placeholder={
                form.loan_class === "personal"
                  ? "Maya Patel"
                  : "Atlas Holdings LLC"
              }
              className="form-input"
            />
          </Field>
          {/* Entity type only matters for business loans — a personal
              application is always a single individual. Hide rather
              than disable so the field can't drift out of sync. */}
          {form.loan_class === "business" && (
            <Field label="Entity type">
              <select
                value={form.borrower_type}
                onChange={(e) =>
                  update("borrower_type", e.target.value as "entity" | "person")
                }
                className="form-input"
              >
                <option value="entity">Business entity (LLC, Inc, LP)</option>
                <option value="person">Individual</option>
              </select>
            </Field>
          )}
          <Field
            label="Contact email"
            hint="We'll use this address if we need to follow up"
          >
            <input
              type="email"
              value={form.borrower_email}
              onChange={(e) => update("borrower_email", e.target.value)}
              placeholder={
                form.loan_class === "personal"
                  ? "you@example.com"
                  : "contact@atlasholdings.example"
              }
              className="form-input"
              disabled={auth.status === "authed"}
            />
          </Field>
          {/* Password field — only for new applicants. Already-
              signed-in users skip it; the backend won't 409 on
              their own email because their loan attaches via the
              session cookie (Phase 2 dashboard work makes the
              multi-loan UX nicer; for now the page just routes
              them to the new loan's status). */}
          {auth.status !== "authed" && (
            <Field
              label="Create a password"
              hint="At least 8 characters. You'll use this to sign back in."
            >
              <input
                type="password"
                value={form.borrower_password}
                onChange={(e) => update("borrower_password", e.target.value)}
                placeholder="••••••••"
                className="form-input"
                autoComplete="new-password"
                minLength={8}
              />
            </Field>
          )}
        </div>
      </SectionCard>
      )}

      {/* ---- STEP 3: The loan ----------------------------------------- */}
      {currentKey === "loan" && (
      <SectionCard
        icon={IconArrowRight}
        title="The loan"
        description={
          form.loan_class === "personal"
            ? "How much you need and what it's for."
            : "Tell us what you're looking to finance."
        }
      >
        <div className="flex flex-col gap-3">
          <LoanTypePicker
            value={form.loan_type}
            onChange={(v) => update("loan_type", v)}
            options={
              form.loan_class === "personal"
                ? LOAN_TYPE_OPTIONS_PERSONAL
                : LOAN_TYPE_OPTIONS_BUSINESS
            }
          />
          <div className="grid grid-cols-2 gap-3">
            <Field label="Loan amount (USD)">
              <input
                type="number"
                min={1}
                step={form.loan_class === "personal" ? 100 : 1000}
                value={form.amount}
                onChange={(e) => update("amount", e.target.value)}
                placeholder={
                  form.loan_class === "personal" ? "25,000" : "2,400,000"
                }
                className="form-input"
              />
            </Field>
            {/* Property type is meaningless for an unsecured personal
                loan — there's no collateral. We hide the field
                entirely rather than offer it greyed-out so the
                borrower can't accidentally interact with it. */}
            {form.loan_class === "business" && (
              <Field label="Property type">
                <select
                  value={form.property_type}
                  onChange={(e) => update("property_type", e.target.value)}
                  className="form-input"
                >
                  <option value="">Select…</option>
                  {PROPERTY_TYPES.map((t) => (
                    <option key={t} value={t.toLowerCase().replace(/-/g, "_")}>
                      {t}
                    </option>
                  ))}
                </select>
              </Field>
            )}
          </div>
          {form.loan_class === "business" && (
            <Field
              label="Property address"
              hint="Street, city, state. We use this for collateral and concentration checks."
            >
              <input
                type="text"
                value={form.property_address}
                onChange={(e) => update("property_address", e.target.value)}
                placeholder="1842 South Tacoma Way, Tacoma, WA 98409"
                className="form-input"
              />
            </Field>
          )}
          <Field
            label="Purpose"
            hint={
              form.loan_class === "personal"
                ? "A line or two on what you'll use the loan for."
                : "A line or two on what the loan is for."
            }
          >
            <textarea
              value={form.purpose}
              onChange={(e) => update("purpose", e.target.value)}
              placeholder={
                form.loan_class === "personal"
                  ? "Consolidating high-interest credit-card balances at a lower fixed rate."
                  : "Acquisition financing for a 12-unit multifamily acquisition."
              }
              rows={2}
              className="form-input"
            />
          </Field>
        </div>
      </SectionCard>
      )}

      {/* ---- STEP 4 (personal): Your finances ------------------------- */}
      {currentKey === "finances" && form.loan_class === "personal" && (
        <SectionCard
          icon={IconUserCircle}
          title="Your finances"
          description="Underwriting will verify these against documents you upload, so rough numbers are fine."
        >
          <div className="grid grid-cols-2 gap-3">
            <Field label="Annual income (USD)" hint="Gross, before tax">
              <input
                type="number"
                min={0}
                step={1000}
                value={form.annual_income}
                onChange={(e) => update("annual_income", e.target.value)}
                placeholder="120000"
                className="form-input"
              />
            </Field>
            <Field
              label="Monthly debt payments (USD)"
              hint="Rent / mortgage + car + student loans + cards. Feeds DTI."
            >
              <input
                type="number"
                min={0}
                step={50}
                value={form.monthly_debt_payments}
                onChange={(e) =>
                  update("monthly_debt_payments", e.target.value)
                }
                placeholder="1800"
                className="form-input"
              />
            </Field>
            <Field label="Credit score" hint="FICO, if you know it">
              <input
                type="number"
                min={300}
                max={850}
                value={form.credit_score}
                onChange={(e) => update("credit_score", e.target.value)}
                placeholder="720"
                className="form-input"
              />
            </Field>
            <Field
              label="Years at current employer"
              hint="Self-employed? Enter years operating."
            >
              <input
                type="number"
                min={0}
                max={80}
                step={0.5}
                value={form.years_employment}
                onChange={(e) => update("years_employment", e.target.value)}
                placeholder="3.5"
                className="form-input"
              />
            </Field>
            <div className="col-span-2">
              <Field label="Employer">
                <input
                  type="text"
                  value={form.employer}
                  onChange={(e) => update("employer", e.target.value)}
                  placeholder="Acme Corp"
                  className="form-input"
                />
              </Field>
            </div>
          </div>
        </SectionCard>
      )}

      {/* ---- STEP 4 (business): Guarantor (optional) ------------------ */}
      {currentKey === "guarantor" && (
      <SectionCard
        icon={IconUserCircle}
        title="Guarantor"
        description="Optional. If a person other than the borrower will personally guarantee, list them here."
      >
        <div className="grid grid-cols-2 gap-3">
          <Field label="Guarantor name">
            <input
              type="text"
              value={form.guarantor_name}
              onChange={(e) => update("guarantor_name", e.target.value)}
              placeholder="Matthew Chen"
              className="form-input"
            />
          </Field>
          <Field label="Guarantor email">
            <input
              type="email"
              value={form.guarantor_email}
              onChange={(e) => update("guarantor_email", e.target.value)}
              placeholder="matthew@…"
              className="form-input"
            />
          </Field>
        </div>
      </SectionCard>
      )}

      {/* ---- STEP 5: Review & submit ---------------------------------- */}
      {currentKey === "review" && (
        <SectionCard
          icon={IconCircleDashed}
          title="Ready to submit?"
          description="Required items must be filled. Optional items can be added later. You can come back and attach documents after submission."
        >
          <ReviewSummary form={form} />
          <div className="mt-4">
            <p className="mb-2 text-[10.5px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
              Checklist
            </p>
            <Checklist items={checklist} />
          </div>
          <p className="mt-4 text-[11.5px] text-[var(--color-text-tertiary)]">
            By submitting you agree to our terms. We&apos;ll send confirmation
            to {form.borrower_email || "your email"}.
          </p>
        </SectionCard>
      )}

      {/* ---- Wizard nav (Back / Next / Submit) ------------------------ */}
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

/** Step progress dots. Numeric step indicator (1 of 5) lives in the
 *  header text; this is the visual companion. Active step is filled
 *  brand-green; completed steps are filled neutral; upcoming steps
 *  are outlined. Clicking a previous step goes back to it. */
function ProgressDots({
  steps,
  current,
}: {
  steps: { id: number; label: string; key: string }[];
  current: number;
}) {
  return (
    <ol className="flex items-center gap-1.5">
      {steps.map((s, idx) => {
        const state =
          s.id === current ? "active" : s.id < current ? "done" : "todo";
        return (
          <li key={s.id} className="flex items-center gap-1.5">
            <span
              className="inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1 text-[10.5px] font-semibold"
              style={{
                background:
                  state === "active"
                    ? "var(--color-brand)"
                    : state === "done"
                      ? "var(--color-background-success)"
                      : "var(--color-background-primary)",
                color:
                  state === "active"
                    ? "var(--color-brand-light)"
                    : state === "done"
                      ? "var(--color-brand)"
                      : "var(--color-text-tertiary)",
                border:
                  state === "todo"
                    ? "0.5px solid var(--color-border-tertiary)"
                    : "none",
              }}
            >
              {state === "done" ? <IconCheck size={10} /> : s.id}
            </span>
            <span
              className="text-[11.5px]"
              style={{
                color:
                  state === "active"
                    ? "var(--color-text-primary)"
                    : "var(--color-text-tertiary)",
                fontWeight: state === "active" ? 500 : 400,
              }}
            >
              {s.label}
            </span>
            {idx < steps.length - 1 && (
              <span
                aria-hidden
                className="mx-1 h-px w-3"
                style={{ background: "var(--color-border-tertiary)" }}
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}

/** Read-only summary card shown on the Review step. Mirrors the form
 *  state in human-readable form so the borrower can sanity-check
 *  before clicking Submit. */
function ReviewSummary({ form }: { form: FormState }) {
  const rows: { label: string; value: string }[] = [
    {
      label: "Loan type",
      value:
        form.loan_class === "personal"
          ? `Personal · ${form.loan_type}`
          : `Business · ${form.loan_type}`,
    },
    {
      label: form.loan_class === "personal" ? "Your name" : "Entity name",
      value: form.borrower_name || "—",
    },
    { label: "Contact email", value: form.borrower_email || "—" },
    {
      label: "Loan amount",
      value: form.amount
        ? `$${Number(form.amount).toLocaleString()}`
        : "—",
    },
    { label: "Purpose", value: form.purpose || "—" },
  ];
  if (form.loan_class === "business") {
    rows.push(
      { label: "Property type", value: form.property_type || "—" },
      { label: "Property address", value: form.property_address || "—" },
      {
        label: "Guarantor",
        value: form.guarantor_name
          ? `${form.guarantor_name}${form.guarantor_email ? ` · ${form.guarantor_email}` : ""}`
          : "—",
      },
    );
  } else {
    rows.push(
      {
        label: "Annual income",
        value: form.annual_income
          ? `$${Number(form.annual_income).toLocaleString()}`
          : "—",
      },
      {
        label: "Monthly debt",
        value: form.monthly_debt_payments
          ? `$${Number(form.monthly_debt_payments).toLocaleString()}`
          : "—",
      },
      { label: "Credit score", value: form.credit_score || "—" },
      { label: "Employer", value: form.employer || "—" },
    );
  }
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-2 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-secondary)] px-4 py-3">
      {rows.map((r) => (
        <div key={r.label} className="flex flex-col">
          <dt className="text-[10.5px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
            {r.label}
          </dt>
          <dd className="text-[12.5px] text-[var(--color-text-primary)]">
            {r.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

// ---- helpers --------------------------------------------------------------

function SectionCard({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: React.ComponentType<{ size?: number }>;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-5 py-4">
      <header className="mb-4 flex items-center gap-2.5">
        <span
          className="inline-flex h-7 w-7 items-center justify-center rounded-md"
          style={{
            background: "var(--color-background-success)",
            color: "var(--color-brand)",
          }}
        >
          <Icon size={14} />
        </span>
        <div className="flex flex-col leading-tight">
          <p className="text-[13.5px] font-medium">{title}</p>
          <p className="text-[11.5px] text-[var(--color-text-tertiary)]">
            {description}
          </p>
        </div>
      </header>
      {children}
    </section>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10.5px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
        {label}
      </span>
      {children}
      {hint && (
        <span className="text-[10.5px] text-[var(--color-text-tertiary)]">
          {hint}
        </span>
      )}
    </label>
  );
}

function LoanTypePicker({
  value,
  onChange,
  options,
}: {
  value: LoanType;
  onChange: (v: LoanType) => void;
  options: { value: LoanType; label: string; hint: string }[];
}) {
  return (
    <div
      className={`grid gap-2 ${
        options.length === 3 ? "grid-cols-3" : "grid-cols-2"
      }`}
    >
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            className="rounded-md border px-3 py-2 text-left transition-colors"
            style={{
              background: active
                ? "var(--color-background-success)"
                : "var(--color-background-primary)",
              borderColor: active
                ? "var(--color-brand)"
                : "var(--color-border-tertiary)",
              borderWidth: active ? 1 : 0.5,
            }}
          >
            <p
              className="text-[13px] font-medium"
              style={{
                color: active
                  ? "var(--color-brand)"
                  : "var(--color-text-primary)",
              }}
            >
              {opt.label}
            </p>
            <p className="text-[11px] text-[var(--color-text-tertiary)]">
              {opt.hint}
            </p>
          </button>
        );
      })}
    </div>
  );
}

interface ChecklistItem {
  label: string;
  satisfied: boolean;
  required: boolean;
}

/** Compute the live completeness state of the application form.
 *  Mirrors what the intake agent's missing-fields detector would
 *  flag once the application lands internally. The personal and
 *  business paths share three required items (name, email, amount)
 *  and then diverge: a business needs property facts; a personal
 *  needs the income / credit / employment fields the rules engine
 *  uses for DTI, LTI, and FICO.
 */
function checkCompleteness(form: FormState, isAuthed: boolean): ChecklistItem[] {
  const shared: ChecklistItem[] = [
    {
      label:
        form.loan_class === "personal" ? "Your full name" : "Borrower entity name",
      satisfied: form.borrower_name.trim().length > 1,
      required: true,
    },
    {
      label: "Valid contact email",
      satisfied: /^[^@]+@[^@]+\.[^@]+$/.test(form.borrower_email),
      required: true,
    },
    // Password is required for new applicants (we're creating an
    // account atomically); skipped for already-signed-in users.
    ...(isAuthed
      ? []
      : [
          {
            label: "Password (8+ characters)",
            satisfied: form.borrower_password.length >= 8,
            required: true,
          },
        ]),
    {
      label: "Loan amount > $0",
      satisfied: Number(form.amount) > 0,
      required: true,
    },
  ];
  if (form.loan_class === "personal") {
    return [
      ...shared,
      {
        label: "Annual income",
        satisfied: Number(form.annual_income) > 0,
        required: true,
      },
      {
        label: "Credit score (FICO 300-850)",
        satisfied:
          Number(form.credit_score) >= 300 && Number(form.credit_score) <= 850,
        required: false,
      },
      {
        label: "Monthly debt payments (for DTI calculation)",
        satisfied: Number(form.monthly_debt_payments) > 0,
        required: false,
      },
      {
        label: "Employer",
        satisfied: form.employer.trim().length > 1,
        required: false,
      },
    ];
  }
  return [
    ...shared,
    {
      label: "Property address",
      satisfied: form.property_address.trim().length > 5,
      required: false,
    },
    {
      label: "Property type",
      satisfied: form.property_type.length > 0,
      required: false,
    },
    {
      label: "Guarantor (if individual is signing)",
      satisfied: form.guarantor_name.trim().length > 1,
      required: false,
    },
  ];
}

function Checklist({ items }: { items: ChecklistItem[] }) {
  return (
    <ul className="flex flex-col gap-1">
      {items.map((c) => (
        <li
          key={c.label}
          className="flex items-center gap-2 text-[12.5px]"
          style={{
            color: c.satisfied
              ? "var(--color-text-primary)"
              : c.required
                ? "var(--color-text-warning)"
                : "var(--color-text-tertiary)",
          }}
        >
          <span
            className="inline-flex h-4 w-4 items-center justify-center rounded-full"
            style={{
              background: c.satisfied
                ? "var(--color-background-success)"
                : "var(--color-background-secondary)",
              color: c.satisfied
                ? "var(--color-brand)"
                : c.required
                  ? "var(--color-text-warning)"
                  : "var(--color-text-tertiary)",
            }}
          >
            {c.satisfied ? (
              <IconCheck size={10} />
            ) : (
              <IconCircleDashed size={10} />
            )}
          </span>
          {c.label}
          {!c.required && (
            <span className="text-[10.5px] text-[var(--color-text-tertiary)]">
              (optional)
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
