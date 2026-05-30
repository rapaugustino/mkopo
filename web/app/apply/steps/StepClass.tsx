"use client";

import type { FormState, LoanClass } from "../_shared";

/**
 * Step 1: pick personal vs business.
 *
 * Takes the full setForm rather than the single-field ``update`` because
 * flipping the class needs to reset multiple dependent fields in one
 * transaction (loan_type, property_*, borrower_type). Doing it as
 * separate update() calls would leave the form in an inconsistent
 * intermediate state for one render.
 */
export function StepClass({
  form,
  setForm,
}: {
  form: FormState;
  setForm: React.Dispatch<React.SetStateAction<FormState>>;
}) {
  return (
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
  );
}
