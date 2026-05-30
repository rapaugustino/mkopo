"use client";

import { IconUserCircle } from "@tabler/icons-react";

import { Field, SectionCard } from "../_components";
import type { FormState, FormUpdater } from "../_shared";

/**
 * Step 4 for business loans: optional multi-row guarantor list.
 *
 * Zero guarantors is a valid (non-recourse) commercial application,
 * so the whole step is optional at the gating layer. Half-filled
 * rows are flagged by ``stepValid`` in the parent because they'd
 * otherwise round-trip as Party stubs with no contact info.
 */
export function StepGuarantor({
  form,
  update,
}: {
  form: FormState;
  update: FormUpdater;
}) {
  return (
    <SectionCard
      icon={IconUserCircle}
      title="Guarantors"
      description="Optional. Add a row for each person who'll personally guarantee. Both name and a valid email are required per row — we route updates to them by email."
    >
      {form.guarantors.length === 0 ? (
        <p className="text-[12px] text-[var(--color-text-secondary)]">
          No guarantors yet. Click <strong>Add guarantor</strong> below to
          list one — or leave this step empty if the loan is non-recourse.
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          {form.guarantors.map((g, idx) => (
            <div
              key={idx}
              className="grid grid-cols-[1fr_1fr_auto] items-end gap-3"
            >
              <Field label={`Guarantor ${idx + 1} — name`}>
                <input
                  type="text"
                  value={g.name}
                  onChange={(e) => {
                    const next = [...form.guarantors];
                    next[idx] = { ...next[idx], name: e.target.value };
                    update("guarantors", next);
                  }}
                  placeholder="Matthew Chen"
                  className="form-input"
                />
              </Field>
              <Field label="Email">
                <input
                  type="email"
                  value={g.email}
                  onChange={(e) => {
                    const next = [...form.guarantors];
                    next[idx] = { ...next[idx], email: e.target.value };
                    update("guarantors", next);
                  }}
                  placeholder="matthew@…"
                  className="form-input"
                />
              </Field>
              <button
                type="button"
                onClick={() => {
                  update(
                    "guarantors",
                    form.guarantors.filter((_, i) => i !== idx),
                  );
                }}
                className="inline-flex h-9 items-center gap-1 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2.5 text-[11.5px] text-[var(--color-text-secondary)] hover:bg-[var(--color-background-secondary)] hover:text-[var(--color-text-danger)]"
                aria-label={`Remove guarantor ${idx + 1}`}
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      )}
      <button
        type="button"
        onClick={() =>
          update("guarantors", [
            ...form.guarantors,
            { name: "", email: "" },
          ])
        }
        className="mt-3 inline-flex items-center gap-1 rounded-md border-[0.5px] border-dashed border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3 py-1.5 text-[11.5px] font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-background-secondary)]"
      >
        + Add guarantor
      </button>
    </SectionCard>
  );
}
