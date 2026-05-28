"use client";

/**
 * Adversarial-injection eval card — per-pattern coverage of the
 * input-layer prompt-injection detector.
 *
 * Backs ``evals/tasks/adversarial_injection.py``. Each row is one
 * documented attack class (``metadata.pattern`` on the fixture): how
 * many examples we have, how many the detector caught, and the
 * resulting pass rate.
 *
 * Threshold is 100% — any pattern slipping past the detector trips
 * the eval gate. The card surfaces sub-100% rates per pattern even
 * when the overall headline is still green so a partial regression
 * (one pattern class slipping while the others hold) is visible.
 *
 * Why this matters: OWASP LLM01 (prompt injection) is the top of the
 * GenAI risk surface. NIST AI 600-1 calls out "manipulated inputs"
 * as a control objective; SR 11-7 §VI requires direction-of-error
 * breakdown — same logic applies here, just for adversarial coverage
 * instead of classification.
 */

import { useQuery } from "@tanstack/react-query";
import { IconShieldX } from "@tabler/icons-react";
import {
  api,
  type AdversarialInjectionDetails,
  type TaskDetail,
} from "@/lib/api";
import { Pill } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";
import { Skeleton } from "@/app/components/Skeleton";
import { Tooltip } from "@/app/components/Tooltip";
import { NISTBadge } from "./NISTBadge";

const PCT = (v: number, digits = 0) => `${(v * 100).toFixed(digits)}%`;

/** Human-readable label + description per known attack pattern. Keep
 *  this map in sync with ``metadata.pattern`` on the fixtures under
 *  ``api/evals/golden_sets/adversarial_injection/``. New patterns
 *  fall back to the raw key + a generic tooltip. */
const PATTERN_META: Record<string, { label: string; tooltip: string }> = {
  direct_instruction_override: {
    label: "Direct instruction override",
    tooltip:
      "Classic 'ignore previous instructions' family — the attacker text directly addresses the LLM and tells it to drop its prompt. Caught by the regex catalog before any LLM call.",
  },
  rule_bypass_via_document: {
    label: "Rule bypass via document",
    tooltip:
      "Indirect injection: a 'SYSTEM OVERRIDE' notice planted inside a document (appraisal report, financial statement) telling the LLM to waive a specific rule. Defense in depth: detector blocks at input; rules engine overrides any LLM compliance at decision time; constitutional judge flags the verdict.",
  },
  jailbreak_persona: {
    label: "Jailbreak via persona",
    tooltip:
      "Role-play prompt asking the model to assume an unconstrained persona. Detector matches the canonical 'DAN'/'jailbroken'/role-flip phrasing.",
  },
  data_exfiltration: {
    label: "Data exfiltration prompt",
    tooltip:
      "Asks the model to leak system prompt, secrets, or other applicants' data. Caught by the exfiltration catalog and by output filters downstream.",
  },
};

interface RowProps {
  pattern: string;
  passed: number;
  n: number;
  rate: number;
}

function PatternRow({ pattern, passed, n, rate }: RowProps) {
  const meta = PATTERN_META[pattern] ?? {
    label: pattern.replace(/_/g, " "),
    tooltip:
      "Attack pattern not yet documented in the dashboard's PATTERN_META map. The fixture's metadata.pattern is the source of truth.",
  };
  // 100% threshold for this task — anything below is a regression.
  const colour =
    rate >= 1.0
      ? "var(--color-text-success)"
      : rate >= 0.8
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
      <span className="w-[46px] text-right tabular-value font-medium">
        {PCT(rate)}
      </span>
      <span className="w-[60px] text-right text-[11px] text-[var(--color-text-tertiary)] tabular-value">
        {passed}/{n}
      </span>
    </div>
  );
}

export function AdversarialInjectionCard() {
  const query = useQuery<TaskDetail, Error>({
    queryKey: ["eval-task-detail", "adversarial_injection"],
    queryFn: () => api.getTaskDetail("adversarial_injection"),
    refetchInterval: 60_000,
  });

  if (query.isLoading) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <Skeleton className="mb-2 h-3 w-32" />
        <Skeleton className="h-[120px]" />
      </div>
    );
  }
  if (!query.data?.found || !query.data.details) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <SectionLabel Icon={IconShieldX}>
          Adversarial injection coverage
        </SectionLabel>
        <p className="mt-2 text-[12px] text-[var(--color-text-tertiary)]">
          No run yet. Run{" "}
          <code>cd api && uv run python -m evals.runner</code> or wait
          for the 4 AM UTC sweep.
        </p>
      </div>
    );
  }

  const d = query.data.details as unknown as AdversarialInjectionDetails;
  const ranAt = query.data.ran_at
    ? new Date(query.data.ran_at).toLocaleString()
    : "—";
  const overallAcc = query.data.accuracy ?? 0;
  // Sort patterns by rate ascending so any regression floats to the
  // top — operator sees what to fix first without scrolling.
  const sortedPatterns = Object.entries(d.by_pattern).sort(
    ([, a], [, b]) => a.rate - b.rate,
  );

  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
      <div className="mb-2.5 flex items-center justify-between gap-2">
        <SectionLabel Icon={IconShieldX} dense>
          Adversarial injection coverage
        </SectionLabel>
        <span className="flex items-center gap-2 text-[11px] text-[var(--color-text-tertiary)]">
          <Tooltip
            content="Overall pass-rate against the documented attack catalog. Threshold is 100% — any pattern slipping past the detector trips the CI gate. Per-pattern bars below show where (if anywhere) the regression is."
            underline
          >
            overall
          </Tooltip>
          <Pill variant={overallAcc >= 1.0 ? "success" : "danger"}>
            {PCT(overallAcc, 1)}
          </Pill>
          <span>·</span>
          <span>n={query.data.n}</span>
          <span>·</span>
          <span>{ranAt}</span>
        </span>
      </div>

      <div className="flex flex-col gap-2">
        {sortedPatterns.map(([pattern, stats]) => (
          <PatternRow
            key={pattern}
            pattern={pattern}
            passed={stats.passed}
            n={stats.n}
            rate={stats.rate}
          />
        ))}
      </div>

      <p className="mt-3 flex flex-wrap items-center gap-1.5 text-[10.5px] text-[var(--color-text-tertiary)]">
        <span>
          Threshold = 100%. OWASP LLM01. The detector is a regex
          catalog with a Haiku escalation path; regressions usually
          mean a new attack class needs a new signature.
        </span>
        <NISTBadge category="info_security" />
      </p>
    </div>
  );
}
