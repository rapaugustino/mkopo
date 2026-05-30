"use client";

/**
 * Shared sub-components for the /apply wizard.
 *
 * Lives next to the page rather than in the global ``components/``
 * directory because none of these are used elsewhere. Keeping them
 * scoped to the apply flow avoids the global namespace bloat that
 * comes from promoting every one-off layout helper.
 */

import { IconCheck, IconCircleDashed } from "@tabler/icons-react";

import { humanizeLoanType } from "@/lib/humanize";

import type { FormState, LoanType } from "./_shared";

// ---- ProgressDots --------------------------------------------------------

/** Step progress dots. Numeric step indicator (1 of 5) lives in the
 *  header text; this is the visual companion. Active step is filled
 *  brand-green; completed steps are filled neutral; upcoming steps
 *  are outlined. Clicking a previous step goes back to it. */
export function ProgressDots({
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

// ---- SectionCard + Field + LoanTypePicker -------------------------------

export function SectionCard({
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

export function Field({
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

export function LoanTypePicker({
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

// ---- ReviewSummary -------------------------------------------------------

/** Read-only summary card shown on the Review step. Mirrors the form
 *  state in human-readable form so the borrower can sanity-check
 *  before clicking Submit. */
export function ReviewSummary({ form }: { form: FormState }) {
  const rows: { label: string; value: string }[] = [
    {
      label: "Loan type",
      // Both halves of the review summary humanized — raw enum
      // values like ``bridge`` or ``permanent`` shouldn't read in
      // a borrower-facing recap.
      value:
        form.loan_class === "personal"
          ? `Personal · ${humanizeLoanType(form.loan_type)}`
          : `Business · ${humanizeLoanType(form.loan_type)}`,
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
    // Only fully-populated rows count toward the summary — half-typed
    // ones are dropped at submit too.
    const validGuarantors = form.guarantors.filter(
      (g) => g.name.trim() && g.email.trim(),
    );
    rows.push(
      { label: "Property type", value: form.property_type || "—" },
      { label: "Property address", value: form.property_address || "—" },
      {
        label:
          validGuarantors.length > 1 ? "Guarantors" : "Guarantor",
        value:
          validGuarantors.length === 0
            ? "—"
            : validGuarantors
                .map((g) => `${g.name} · ${g.email}`)
                .join("; "),
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

// ---- Checklist + checkCompleteness --------------------------------------

export interface ChecklistItem {
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
export function checkCompleteness(
  form: FormState,
  isAuthed: boolean,
): ChecklistItem[] {
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
      satisfied: form.guarantors.some(
        (g) => g.name.trim().length > 1 && /^[^@]+@[^@]+\.[^@]+$/.test(g.email),
      ),
      required: false,
    },
  ];
}

export function Checklist({ items }: { items: ChecklistItem[] }) {
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
