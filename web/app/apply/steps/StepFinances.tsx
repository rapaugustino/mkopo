"use client";

import { IconUserCircle } from "@tabler/icons-react";

import { Field, SectionCard } from "../_components";
import type { FormState, FormUpdater } from "../_shared";

/**
 * Step 4 for personal loans: income / debt / credit / employment.
 *
 * These feed the personal rule pack server-side (DTI, LTI, FICO
 * floor, employment-tenure minimum). Optional even when the class
 * is personal — the intake agent will email-chase missing items
 * later. Annual income is the only hard-required field here so the
 * rules engine has at least one signal to grade against.
 */
export function StepFinances({
  form,
  update,
}: {
  form: FormState;
  update: FormUpdater;
}) {
  return (
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
  );
}
