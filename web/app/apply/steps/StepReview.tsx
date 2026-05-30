"use client";

import { IconCircleDashed } from "@tabler/icons-react";

import {
  Checklist,
  ReviewSummary,
  SectionCard,
  type ChecklistItem,
} from "../_components";
import type { FormState } from "../_shared";

/**
 * Step 5: read-only review of the form state + the live checklist.
 *
 * Required items must be filled before Submit is enabled (gated in
 * the parent via ``ready``); optional items can be added later by
 * uploading documents the intake agent will extract from.
 */
export function StepReview({
  form,
  checklist,
}: {
  form: FormState;
  checklist: ChecklistItem[];
}) {
  return (
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
  );
}
