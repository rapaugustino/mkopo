"use client";

/**
 * Intake-email eval card — per-criterion bars + per-class split for
 * the borrower doc-request email drafter.
 *
 * Backs ``evals/tasks/intake_email.py``. Four binary criteria, each
 * regulator-relevant for borrower communications:
 *
 * - ``addressed_by_name`` — the email greets the actual borrower
 *   (no "Dear customer" / placeholder name).
 * - ``no_markdown`` — body is plain text. Markdown shows up as
 *   literal ``**`` in the inbox.
 * - ``doc_asks_match_class`` — personal-loan emails ask for pay
 *   stubs / W-2 / tax returns; commercial emails ask for
 *   appraisals / rent rolls / PFS. Cross-class doc asks are the
 *   "asked a business borrower for pay stubs" failure mode.
 * - ``within_word_limit`` — body ≤ 130 words (the prompt asks for
 *   120; we tolerate +10 for long officer/institution sign-offs).
 *
 * The per-class breakdown sits in the side panel so a regression
 * limited to one prompt (e.g. the personal-loan variant) is visible
 * even when the overall pass-rate looks fine because the other
 * prompt's fixtures balance it out.
 */

import { useQuery } from "@tanstack/react-query";
import { IconMail } from "@tabler/icons-react";
import { api, type IntakeEmailDetails, type TaskDetail } from "@/lib/api";
import { Pill } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";
import { Skeleton } from "@/app/components/Skeleton";
import { Tooltip } from "@/app/components/Tooltip";

const PCT = (v: number, digits = 0) => `${(v * 100).toFixed(digits)}%`;

const CRITERION_META: Record<
  string,
  { label: string; tooltip: string }
> = {
  addressed_by_name: {
    label: "Addressed by name",
    tooltip:
      "The borrower's name appears in the body. The prompt explicitly asks for it; a 'Dear customer' / placeholder draft is the most common cold-LLM failure on this task.",
  },
  no_markdown: {
    label: "Plain text (no markdown)",
    tooltip:
      "Body contains no `**bold**`, `# heading`, or `1. numbered list` syntax. Outbound goes through Resend / SMTP and renders as plain text — markdown appears in the inbox as literal asterisks.",
  },
  doc_asks_match_class: {
    label: "Doc asks match loan class",
    tooltip:
      "Personal-loan emails mention pay stubs / W-2 / tax return / bank statement / 1099; commercial-loan emails mention appraisal / rent roll / operating statement / PFS / business tax return. Cross-class asks (e.g. 'send your pay stubs' on a CRE deal) are the textbook prompt-drift failure.",
  },
  within_word_limit: {
    label: "Within word limit",
    tooltip:
      "Body ≤ 130 words. Prompt asks for ≤ 120; we tolerate +10 for long officer + institution sign-offs. Longer emails meaningfully reduce reply rate.",
  },
};

const CRITERION_ORDER = [
  "addressed_by_name",
  "no_markdown",
  "doc_asks_match_class",
  "within_word_limit",
];

interface BarProps {
  criterion: string;
  passed: number;
  n: number;
  rate: number;
}

function CriterionBar({ criterion, passed, n, rate }: BarProps) {
  const meta = CRITERION_META[criterion] ?? {
    label: criterion,
    tooltip: "No description available.",
  };
  const colour =
    rate >= 0.92
      ? "var(--color-text-success)"
      : rate >= 0.75
        ? "var(--color-text-warning)"
        : "var(--color-text-danger)";
  const width = Math.max(2, Math.round(rate * 100));
  return (
    <div className="flex items-center gap-3 text-[11.5px]">
      <Tooltip content={meta.tooltip} underline maxWidth={320}>
        <span className="w-[200px] truncate text-[var(--color-text-secondary)]">
          {meta.label}
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
      <span className="w-[64px] text-right text-[11px] text-[var(--color-text-tertiary)] tabular-value">
        {passed}/{n}
      </span>
    </div>
  );
}

export function IntakeEmailCard() {
  const query = useQuery<TaskDetail, Error>({
    queryKey: ["eval-task-detail", "intake_email"],
    queryFn: () => api.getTaskDetail("intake_email"),
    refetchInterval: 60_000,
  });

  if (query.isLoading) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <Skeleton className="mb-2 h-3 w-32" />
        <Skeleton className="h-[140px]" />
      </div>
    );
  }
  if (!query.data?.found || !query.data.details) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <SectionLabel Icon={IconMail}>
          Intake email compliance
        </SectionLabel>
        <p className="mt-2 text-[12px] text-[var(--color-text-tertiary)]">
          No run yet. Run{" "}
          <code>cd api && uv run python -m evals.runner</code> or wait
          for the 4 AM UTC sweep.
        </p>
      </div>
    );
  }

  const d = query.data.details as unknown as IntakeEmailDetails;
  const ranAt = query.data.ran_at
    ? new Date(query.data.ran_at).toLocaleString()
    : "—";
  const overallAcc = query.data.accuracy ?? 0;

  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
      <div className="mb-2.5 flex items-center justify-between gap-2">
        <SectionLabel Icon={IconMail} dense>
          Intake email compliance
        </SectionLabel>
        <span className="flex items-center gap-2 text-[11px] text-[var(--color-text-tertiary)]">
          <Tooltip
            content="Overall pass-rate — fraction of golden-set fixtures where all four criteria passed (strict AND). The per-criterion bars below decompose this so you can see which property is failing when overall < 100%."
            underline
          >
            overall
          </Tooltip>
          <Pill variant={overallAcc >= 0.85 ? "success" : "warn"}>
            {PCT(overallAcc, 1)}
          </Pill>
          <span>·</span>
          <span>n={query.data.n}</span>
          <span>·</span>
          <span>{ranAt}</span>
        </span>
      </div>

      <div className="flex flex-col gap-3 lg:flex-row lg:items-start">
        <div className="flex flex-1 flex-col gap-2">
          {CRITERION_ORDER.map((criterion) => {
            const stats = d.per_criterion[criterion];
            if (!stats) return null;
            return (
              <CriterionBar
                key={criterion}
                criterion={criterion}
                passed={stats.passed}
                n={stats.n}
                rate={stats.rate}
              />
            );
          })}
        </div>

        {/* Per-class split — surfaces drift on personal-only or
            business-only prompts. Both prompts use the same
            scoring criteria, so this is the cleanest way to spot a
            class-specific regression. */}
        <div className="flex flex-col gap-1.5 rounded-md bg-[var(--color-background-secondary)] px-3 py-2 text-[11px] lg:w-[180px]">
          <p className="text-[10px] font-medium uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
            By loan class
          </p>
          {Object.entries(d.by_class).map(([cls, stats]) => (
            <div key={cls} className="flex items-center justify-between">
              <span className="text-[var(--color-text-secondary)] capitalize">
                {cls}
              </span>
              <span className="flex items-center gap-1.5">
                <span
                  className="tabular-value font-medium"
                  style={{
                    color:
                      stats.rate >= 0.92
                        ? "var(--color-text-success)"
                        : stats.rate >= 0.75
                          ? "var(--color-text-warning)"
                          : "var(--color-text-danger)",
                  }}
                >
                  {PCT(stats.rate)}
                </span>
                <span className="text-[10px] text-[var(--color-text-tertiary)] tabular-value">
                  ({stats.passed}/{stats.n})
                </span>
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
