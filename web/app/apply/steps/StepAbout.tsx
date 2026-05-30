"use client";

import { IconBuilding, IconUserCircle } from "@tabler/icons-react";

import { Field, SectionCard } from "../_components";
import type { FormState, FormUpdater } from "../_shared";

/**
 * Step 2: borrower identity + (if new) password.
 *
 * Already-signed-in users see their email pre-filled and disabled,
 * and the password field is hidden — the apply mutation attaches
 * the loan to the existing account via the session cookie.
 */
export function StepAbout({
  form,
  update,
  isAuthed,
}: {
  form: FormState;
  update: FormUpdater;
  isAuthed: boolean;
}) {
  return (
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
            disabled={isAuthed}
          />
        </Field>
        {/* Password field — only for new applicants. Already-
            signed-in users skip it; the backend won't 409 on
            their own email because their loan attaches via the
            session cookie. */}
        {!isAuthed && (
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
  );
}
