"use client";

import { IconArrowRight } from "@tabler/icons-react";

import { Field, LoanTypePicker, SectionCard } from "../_components";
import {
  LOAN_TYPE_OPTIONS_BUSINESS,
  LOAN_TYPE_OPTIONS_PERSONAL,
  PROPERTY_TYPES,
  type FormState,
  type FormUpdater,
} from "../_shared";

/**
 * Step 3: loan type + amount + property facts (business only) +
 * free-form purpose.
 */
export function StepLoan({
  form,
  update,
}: {
  form: FormState;
  update: FormUpdater;
}) {
  return (
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
  );
}
