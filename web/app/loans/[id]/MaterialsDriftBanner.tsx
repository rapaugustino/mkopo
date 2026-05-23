"use client";

import { useQuery } from "@tanstack/react-query";
import { IconAlertTriangle } from "@tabler/icons-react";
import { motion } from "motion/react";
import { api, type LoanStage, type MaterialsStatus } from "@/lib/api";

interface Props {
  loanId: string;
  /** Banner only matters once the loan has reached the decision
   *  stage or later — pre-decision loans churn freely. We accept the
   *  current stage so the component can self-hide. */
  stage: LoanStage;
}

/** Stages at which materials drift is meaningful — i.e. a decision
 *  has been made and any change to feeding inputs invalidates it.
 *  Earlier stages naturally churn (extraction overrides, document
 *  uploads) so a banner there would be noise. */
const DRIFT_RELEVANT_STAGES: LoanStage[] = [
  "decision",
  "conditions",
  "closing",
  "approved",
];

/**
 * Loud banner that fires when the materials hash has drifted since
 * the last decision agent run.
 *
 * What "drifted" means: the inputs that fed the decision — accepted
 * extractions, document content hashes, borrower-supplied meta
 * (income / credit score / etc.), guarantor list — are no longer
 * the same set. Could be a borrower edit, an underwriter override,
 * a re-upload that changed the bytes. Whatever the cause, the
 * decision was made against the old materials and is stale.
 *
 * The system already refuses forward stage transitions on drift
 * (see services/loans.py); this banner is the *visible* half —
 * makes sure no one is staring at a disabled "advance" button
 * wondering why it won't click.
 *
 * Polls every 15s so the banner appears within a tab refresh of
 * any material change.
 */
export function MaterialsDriftBanner({ loanId, stage }: Props) {
  // Skip the network call entirely when the loan isn't in a stage
  // where drift could matter.
  const relevant = DRIFT_RELEVANT_STAGES.includes(stage);

  const query = useQuery<MaterialsStatus, Error>({
    queryKey: ["materials-status", loanId],
    queryFn: () => api.getMaterialsStatus(loanId),
    enabled: relevant,
    refetchInterval: relevant ? 15_000 : false,
  });

  if (!relevant || !query.data?.drifted) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: -4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18, ease: "easeOut" }}
      role="alert"
      className="flex items-start gap-3 rounded-lg border-[0.5px] border-[var(--color-text-danger)] bg-[var(--color-background-danger)] px-4 py-3"
    >
      <span
        className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full"
        style={{
          background: "var(--color-background-primary)",
          color: "var(--color-text-danger)",
        }}
      >
        <IconAlertTriangle size={15} />
      </span>
      <div className="flex-1">
        <p className="text-[13px] font-medium text-[var(--color-text-danger)]">
          Materials have changed since the decision was made.
        </p>
        <p className="mt-1 text-[12px] leading-relaxed text-[var(--color-text-danger)] opacity-90">
          An extraction, document, or borrower-supplied field has been updated
          since the decision agent last ran. Forward stage transitions are
          blocked until the decision agent is re-run against the current loan
          packet — that way the recommendation reflects the materials, not a
          stale snapshot.
        </p>
        <p
          className="mt-2 font-mono text-[10.5px] tracking-tight opacity-70"
          title="current hash · decision hash"
        >
          {query.data.current_hash.slice(0, 16)}… ≠{" "}
          {query.data.decision_hash?.slice(0, 16) ?? "(none)"}…
        </p>
      </div>
    </motion.div>
  );
}
