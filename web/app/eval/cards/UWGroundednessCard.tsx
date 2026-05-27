"use client";

/**
 * UW-summary groundedness eval card — RAGAS-style faithfulness.
 *
 * Backs ``evals/tasks/uw_groundedness.py``. The card answers two
 * questions a regulator (and an on-call engineer) actually asks:
 *
 * 1. **Does the judge work?** Pinned-model factuality judges are
 *    only useful if they correctly discriminate clean summaries
 *    from hallucinated ones. The headline pill ("judge accuracy")
 *    is the fraction of fixtures the judge classified into the
 *    expected band. ≥ 80% means the discrimination signal holds.
 *
 * 2. **What grounding do we hit on clean inputs?** ``avg_grounded
 *    _clean`` is the mean groundedness ratio on the high-band
 *    fixtures. Industry production targets are ~0.90+. The
 *    hallucinated bar shows the contrast — it should sit well
 *    below clean.
 *
 * Per-example rows surface which fixture failed (judge missed the
 * hallucination, or rejected a fair paraphrase as unsupported) so
 * an engineer can act on the specific case without scrolling
 * through the raw JSON.
 *
 * Reference: RAGAS Faithfulness — Es et al. 2024, arXiv:2309.15217.
 */

import { useQuery } from "@tanstack/react-query";
import { IconBlockquote } from "@tabler/icons-react";
import {
  api,
  type TaskDetail,
  type UWGroundednessDetails,
} from "@/lib/api";
import { Pill } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";
import { Skeleton } from "@/app/components/Skeleton";
import { Tooltip } from "@/app/components/Tooltip";

const PCT = (v: number, digits = 0) =>
  v == null ? "—" : `${(v * 100).toFixed(digits)}%`;

export function UWGroundednessCard() {
  const query = useQuery<TaskDetail, Error>({
    queryKey: ["eval-task-detail", "uw_groundedness"],
    queryFn: () => api.getTaskDetail("uw_groundedness"),
    refetchInterval: 60_000,
  });

  if (query.isLoading) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <Skeleton className="mb-2 h-3 w-40" />
        <Skeleton className="h-[160px]" />
      </div>
    );
  }
  if (!query.data?.found || !query.data.details) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <SectionLabel Icon={IconBlockquote}>
          UW summary groundedness (RAGAS)
        </SectionLabel>
        <p className="mt-2 text-[12px] text-[var(--color-text-tertiary)]">
          No run yet. Run{" "}
          <code>cd api && uv run python -m evals.runner</code> or wait
          for the 4 AM UTC sweep.
        </p>
      </div>
    );
  }

  const d = query.data.details as unknown as UWGroundednessDetails;
  const ranAt = query.data.ran_at
    ? new Date(query.data.ran_at).toLocaleString()
    : "—";

  // The contrast between clean + hallucinated bars is the actual
  // story this card tells. A small gap = judge is rejecting fair
  // paraphrases or accepting hallucinations.
  const contrast = d.avg_grounded_clean - d.avg_grounded_hallucinated;

  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <SectionLabel Icon={IconBlockquote} dense>
          UW summary groundedness (RAGAS)
        </SectionLabel>
        <span className="flex items-center gap-2 text-[11px] text-[var(--color-text-tertiary)]">
          <Tooltip
            content="Judge accuracy — fraction of fixtures classified into the expected band (clean ≥ 0.85, hallucinated < 0.85). This is what the eval gate measures; a regression here means the factuality judge itself is the bug, not the summarizer."
            underline
          >
            judge accuracy
          </Tooltip>
          <Pill variant={d.judge_accuracy >= 0.8 ? "success" : "warn"}>
            {PCT(d.judge_accuracy, 1)}
          </Pill>
          <span>·</span>
          <span>n={query.data.n}</span>
          <span>·</span>
          <span>{ranAt}</span>
        </span>
      </div>

      <div className="flex flex-col gap-3 lg:flex-row lg:items-start">
        {/* Left: the discrimination bars. Clean (top) should be
            high; hallucinated (bottom) should be low. The gap
            between them is the signal. */}
        <div className="flex flex-1 flex-col gap-2">
          <DiscriminationBar
            label="Clean fixtures"
            rate={d.avg_grounded_clean}
            n={d.n_clean}
            tooltip="Mean groundedness on summaries with no planted hallucinations. The judge should mark almost every claim supported. Target ≥ 0.90 in production."
            tone="good"
          />
          <DiscriminationBar
            label="Hallucinated fixtures"
            rate={d.avg_grounded_hallucinated}
            n={d.n_hallucinated}
            tooltip="Mean groundedness on summaries with deliberately planted unsupported claims. The judge should flag the bad claims; this number should sit well below the clean bar."
            tone="bad"
          />
          <div className="mt-1 flex items-center justify-between text-[10.5px] text-[var(--color-text-tertiary)]">
            <span>
              <Tooltip
                content="Mean clean grounding minus mean hallucinated grounding. The signal that says the judge can tell the difference between a faithful summary and a tampered one. Even a planted-hallucination fixture has many *true* claims around the bad ones (e.g. 14 of 17), so a 0.10–0.20 gap is healthy in practice. Below ~0.05 means the judge is either nodding through hallucinations or rejecting fair paraphrases — investigate."
                underline
              >
                discrimination gap
              </Tooltip>
            </span>
            <span
              className="tabular-value font-medium"
              style={{
                color:
                  contrast >= 0.1
                    ? "var(--color-text-success)"
                    : contrast >= 0.05
                      ? "var(--color-text-warning)"
                      : "var(--color-text-danger)",
              }}
            >
              {PCT(contrast, 1)}
            </span>
          </div>
          <p className="text-[10.5px] text-[var(--color-text-tertiary)]">
            {d.supported_claims} of {d.total_claims} claims supported
            across the suite. RAGAS — Es et al. 2024,
            arXiv:2309.15217.
          </p>
        </div>

        {/* Right: per-fixture detail rows. */}
        <div className="flex flex-col gap-1 lg:w-[260px]">
          <p className="text-[10px] font-medium uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
            Per-fixture
          </p>
          {d.per_example.map((row) => (
            <div
              key={row.id}
              className="flex items-center justify-between gap-2 text-[11px]"
            >
              <span className="truncate text-[var(--color-text-secondary)]">
                <span
                  className={
                    row.passed
                      ? "mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle"
                      : "mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle"
                  }
                  style={{
                    background: row.passed
                      ? "var(--color-text-success)"
                      : "var(--color-text-danger)",
                  }}
                />
                {row.id}
              </span>
              <span className="flex items-center gap-1.5 tabular-value">
                <span className="text-[var(--color-text-primary)]">
                  {PCT(row.score, 0)}
                </span>
                <span className="text-[10px] text-[var(--color-text-tertiary)]">
                  ({row.supported_claims}/{row.total_claims})
                </span>
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function DiscriminationBar({
  label,
  rate,
  n,
  tooltip,
  tone,
}: {
  label: string;
  rate: number;
  n: number;
  tooltip: string;
  tone: "good" | "bad";
}) {
  // "good" bars (clean fixtures) want high grounding → success when
  // high; "bad" bars (hallucinated fixtures) want low grounding →
  // success when LOW. Colours invert on the bad bar so the visual
  // reads "high green = good" regardless of which row you're on.
  let colour: string;
  if (tone === "good") {
    colour =
      rate >= 0.9
        ? "var(--color-text-success)"
        : rate >= 0.7
          ? "var(--color-text-warning)"
          : "var(--color-text-danger)";
  } else {
    colour =
      rate <= 0.4
        ? "var(--color-text-success)"
        : rate <= 0.7
          ? "var(--color-text-warning)"
          : "var(--color-text-danger)";
  }
  const width = Math.max(2, Math.round(rate * 100));
  return (
    <div className="flex items-center gap-3 text-[11.5px]">
      <Tooltip content={tooltip} underline maxWidth={320}>
        <span className="w-[160px] truncate text-[var(--color-text-secondary)]">
          {label}
        </span>
      </Tooltip>
      <div className="h-2 flex-1 overflow-hidden rounded bg-[var(--color-background-secondary)]">
        <div
          className="h-full rounded"
          style={{ width: `${width}%`, background: colour }}
        />
      </div>
      <span className="w-[48px] text-right tabular-value font-medium">
        {PCT(rate)}
      </span>
      <span className="w-[40px] text-right text-[11px] text-[var(--color-text-tertiary)] tabular-value">
        n={n}
      </span>
    </div>
  );
}
