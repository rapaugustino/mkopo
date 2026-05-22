"use client";

import { useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  IconAlertTriangle,
  IconFileText,
  IconSparkles,
  IconCircleCheck,
  IconCircle,
  IconCircleX,
} from "@tabler/icons-react";
import { AskTheFile } from "./AskTheFile";
import {
  api,
  type Extraction,
  type RiskFlag,
  type RiskSeverity,
  type RulesPreview,
  type UnderwritingKPIs,
  type UnderwritingRecommendation,
  type UnderwritingResult,
  type UnderwritingSection,
} from "@/lib/api";
import { toast } from "sonner";
import { humanizeField, humanizePropertyType } from "@/lib/humanize";
import { useAgentRun } from "@/lib/useAgentRun";
import { AgentProgress } from "@/app/components/AgentProgress";
import { PrimaryButton } from "@/app/components/PrimaryButton";
import { SectionLabel } from "@/app/components/SectionLabel";

interface Props {
  loanId: string;
}

const SEVERITY_STYLE: Record<
  RiskSeverity,
  { bg: string; fg: string; Icon: React.ComponentType<{ size?: number }> }
> = {
  block: {
    bg: "var(--color-background-danger)",
    fg: "var(--color-text-danger)",
    Icon: IconCircleX,
  },
  warn: {
    bg: "var(--color-background-warning)",
    fg: "var(--color-text-warning)",
    Icon: IconAlertTriangle,
  },
  info: {
    bg: "var(--color-background-info)",
    fg: "var(--color-text-info)",
    Icon: IconCircle,
  },
};

const RECOMMENDATION_COPY: Record<
  UnderwritingRecommendation,
  { label: string; bg: string; fg: string; description: string }
> = {
  proceed_to_decision: {
    label: "Proceed to decision",
    bg: "var(--color-background-success)",
    fg: "var(--color-text-success)",
    description: "Rules pass. Route to a decision-stage reviewer.",
  },
  request_more_info: {
    label: "Request more info",
    bg: "var(--color-background-warning)",
    fg: "var(--color-text-warning)",
    description: "Missing or low-confidence data. Re-engage the borrower before deciding.",
  },
  decline: {
    label: "Decline",
    bg: "var(--color-background-danger)",
    fg: "var(--color-text-danger)",
    description: "At least one blocking rule failed. Decline as packeted.",
  },
};

function formatMoney(s: string | number): string {
  const n = typeof s === "string" ? Number(s) : s;
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

function KpiTile({
  label,
  value,
  trend,
}: {
  label: string;
  value: string;
  trend?: string;
}) {
  return (
    <div className="rounded-md bg-[var(--color-background-secondary)] px-3 py-2.5">
      <p className="text-[12px] text-[var(--color-text-secondary)]">{label}</p>
      <p className="tabular-value mt-1.5 text-[18px] font-medium">{value}</p>
      {trend && <p className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">{trend}</p>}
    </div>
  );
}

function KpiStrip({ kpis }: { kpis: UnderwritingKPIs }) {
  // Branch on which side of the KPI block is populated. Personal loans
  // get DTI/LTI/FICO tiles; business loans keep LTV/DSCR. The schema
  // leaves the other side null, so "do we have a credit score?" is a
  // reliable discriminator — no need to thread loan_class through.
  //
  // ``!= null`` (loose) — not ``!== null`` — because the field may be
  // absent entirely (``undefined``) on cached responses written before
  // the personal-KPI fields shipped, or when the rules-preview endpoint
  // omits fields it doesn't compute. ``foo != null`` matches both
  // ``null`` and ``undefined``; the strict variant only matches
  // ``null`` and crashed on the optional-chaining-less access below.
  const isPersonal =
    kpis.credit_score != null || kpis.dti != null || kpis.lti != null;

  if (isPersonal) {
    return (
      <div className="grid grid-cols-4 gap-2">
        <KpiTile label="Loan amount" value={formatMoney(kpis.loan_amount)} />
        <KpiTile
          label="DTI"
          value={kpis.dti != null ? `${Math.round(kpis.dti * 100)}%` : "—"}
          trend={kpis.lti != null ? `LTI ${Math.round(kpis.lti * 100)}%` : undefined}
        />
        <KpiTile
          label="FICO"
          value={kpis.credit_score != null ? String(kpis.credit_score) : "—"}
          trend={kpis.credit_band ?? undefined}
        />
        <KpiTile
          label="Employment"
          value={
            kpis.years_employment != null
              ? `${kpis.years_employment.toFixed(1)} yrs`
              : "—"
          }
          trend={
            kpis.doc_confidence != null
              ? `Docs ${Math.round(kpis.doc_confidence * 100)}%`
              : undefined
          }
        />
      </div>
    );
  }

  return (
    <div className="grid grid-cols-4 gap-2">
      <KpiTile label="Loan amount" value={formatMoney(kpis.loan_amount)} />
      <KpiTile
        label="LTV"
        value={kpis.ltv != null ? `${Math.round(kpis.ltv * 100)}%` : "—"}
      />
      <KpiTile
        label="DSCR"
        value={kpis.dscr != null ? kpis.dscr.toFixed(2) : "—"}
      />
      <KpiTile
        label="Doc confidence"
        value={
          kpis.doc_confidence != null
            ? `${Math.round(kpis.doc_confidence * 100)}%`
            : "—"
        }
      />
    </div>
  );
}

function ExtractionsList({
  extractionByField,
  citedFields,
}: {
  extractionByField: Map<string, Extraction>;
  citedFields: Set<string>;
}) {
  const fields = Array.from(extractionByField.keys()).sort();
  if (fields.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-tertiary)]">
        No accepted extractions yet. Run the intake agent first.
      </p>
    );
  }
  return (
    <div className="flex flex-col">
      {fields.map((field, idx) => {
        const ex = extractionByField.get(field)!;
        const confidence = ex.confidence;
        const dot =
          confidence >= 0.9
            ? "var(--color-text-success)"
            : confidence >= 0.75
              ? "var(--color-text-warning)"
              : "var(--color-text-danger)";
        const cited = citedFields.has(field);
        return (
          <div
            key={field}
            className={`flex items-center justify-between py-1.5 ${
              idx > 0 ? "border-t-[0.5px] border-[var(--color-border-tertiary)]" : ""
            }`}
          >
            <span className="flex items-center gap-2 text-[13px] text-[var(--color-text-secondary)]">
              <span
                className="inline-block h-1.5 w-1.5 rounded-full"
                style={{ background: dot }}
              />
              {humanizeField(field)}
              {cited && (
                <span
                  className="rounded px-1 text-[9px] uppercase tracking-wider"
                  style={{
                    background: "var(--color-background-info)",
                    color: "var(--color-text-info)",
                  }}
                >
                  cited
                </span>
              )}
            </span>
            <span
              className="max-w-[55%] truncate text-right text-[13px] font-medium"
              title={ex.value}
            >
              {ex.value}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function RiskSignals({ flags }: { flags: RiskFlag[] }) {
  // Sort: failing blocks first, then failing warns, then everything else
  const ordered = [...flags].sort((a, b) => {
    const score = (f: RiskFlag) =>
      !f.passed && f.severity === "block"
        ? 0
        : !f.passed && f.severity === "warn"
          ? 1
          : f.passed
            ? 3
            : 2;
    return score(a) - score(b);
  });
  return (
    <div className="flex flex-col gap-1.5">
      {ordered.map((f) => {
        const style = SEVERITY_STYLE[f.severity];
        const Icon = f.passed ? IconCircleCheck : style.Icon;
        return (
          <div
            key={f.rule_id}
            className="flex gap-2 rounded-md px-2.5 py-2 text-[12.5px] leading-snug"
            style={{
              background: f.passed ? "var(--color-background-secondary)" : style.bg,
              color: f.passed ? "var(--color-text-secondary)" : style.fg,
            }}
          >
            <Icon size={15} />
            <div>{f.message}</div>
          </div>
        );
      })}
    </div>
  );
}

function CitedSection({
  section,
  index,
  extractionByField,
}: {
  section: UnderwritingSection;
  index: number;
  extractionByField: Map<string, Extraction>;
}) {
  return (
    <div>
      <SectionLabel dense>{section.title}</SectionLabel>
      <p className="text-[13px] leading-relaxed">
        {section.body}
        {section.citations.length > 0 && (
          <>
            {" "}
            {section.citations.map((field, ci) => {
              const ex = extractionByField.get(field);
              const number = index * 10 + ci + 1;
              const title = ex
                ? `${humanizeField(field)}: ${ex.value} — ${Math.round(ex.confidence * 100)}% confident${
                    ex.source_span?.quote ? `\n\n"${ex.source_span.quote}"` : ""
                  }`
                : `${humanizeField(field)} — no extraction found`;
              return (
                <sup
                  key={`${section.title}-${field}`}
                  title={title}
                  className="ml-0.5 cursor-help rounded px-1 text-[10px] font-medium"
                  style={{
                    background: "var(--color-background-info)",
                    color: "var(--color-text-info)",
                  }}
                >
                  {number}
                </sup>
              );
            })}
          </>
        )}
      </p>
    </div>
  );
}

export function UnderwritingWorkspace({ loanId }: Props) {
  const queryClient = useQueryClient();

  const extractionsQuery = useQuery<Extraction[], Error>({
    queryKey: ["loan", loanId, "extractions"],
    queryFn: () => api.getExtractions(loanId),
  });

  // Deterministic rules + KPIs — runs without the LLM, so the
  // workspace can render extractions, KPIs, and risk signals
  // immediately on tab entry. Re-fetched whenever the LLM-driven
  // underwriting agent completes (the agent may write new
  // extractions which shift the inputs).
  const rulesQuery = useQuery<RulesPreview, Error>({
    queryKey: ["loan", loanId, "rules"],
    queryFn: () => api.getRulesPreview(loanId),
    staleTime: 30_000,
  });

  const underwritingQuery = useQuery<UnderwritingResult | null, Error>({
    queryKey: ["loan", loanId, "underwriting"],
    queryFn: async () => null,
    staleTime: Infinity,
  });

  // Underwriting streams its three nodes (fetch_and_evaluate →
  // draft_summary → persist) over SSE. The final ``done`` event carries
  // the full UnderwritingResult, which we drop into the query cache so
  // the rest of the page can keep reading from React Query.
  const agentRun = useAgentRun();
  const runUnderwriting = () =>
    agentRun.run({
      path: `/loans/${loanId}/agents/underwriting/run`,
      onDone: async (ev) => {
        const result = ev.result as UnderwritingResult | null;
        if (result) {
          queryClient.setQueryData(["loan", loanId, "underwriting"], result);
        }
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["loan", loanId] }),
          queryClient.invalidateQueries({ queryKey: ["loan", loanId, "audit"] }),
          queryClient.invalidateQueries({ queryKey: ["loan", loanId, "extractions"] }),
          queryClient.invalidateQueries({ queryKey: ["loan", loanId, "rules"] }),
          queryClient.invalidateQueries({ queryKey: ["loans"] }),
        ]);
        // Pre-flight gate fired — surface the friendly reason. The
        // banner inside AgentProgress shows the long-form copy; the
        // toast gives it a chance even if the workspace tab isn't
        // scrolled into view.
        if (ev.skip_reason) {
          toast.message("Underwriting didn't run", {
            description: ev.skip_reason,
          });
        } else if (result) {
          const failing = result.risk_flags.filter((f) => !f.passed).length;
          // Use the shared RECOMMENDATION_COPY map so the toast reads
          // "Proceed to decision · 1 rule failing" instead of leaking
          // the raw enum ("proceed_to_decision").
          const label =
            RECOMMENDATION_COPY[result.recommendation]?.label ??
            result.recommendation;
          toast.success("Underwriting complete", {
            description: `${label} · ${failing} rule${
              failing === 1 ? "" : "s"
            } failing`,
          });
        }
      },
    });

  const extractionByField = useMemo(() => {
    const map = new Map<string, Extraction>();
    for (const ex of extractionsQuery.data ?? []) {
      const existing = map.get(ex.field_name);
      if (!existing || ex.confidence > existing.confidence) {
        map.set(ex.field_name, ex);
      }
    }
    return map;
  }, [extractionsQuery.data]);

  const result = underwritingQuery.data;
  const rules = rulesQuery.data;
  // KPIs + risk flags: prefer the agent's result (which carries its own
  // captured snapshot) when available; otherwise fall back to the live
  // deterministic preview. Both shapes are the same so the renderer
  // doesn't branch.
  const liveKpis = result?.kpis ?? rules?.kpis ?? null;
  const liveFlags = result?.risk_flags ?? rules?.risk_flags ?? [];
  const citedFields = useMemo(() => {
    const s = new Set<string>();
    for (const section of result?.sections ?? []) {
      for (const c of section.citations) s.add(c);
    }
    return s;
  }, [result]);

  return (
    <div className="flex flex-col gap-3">
      {/* Header strip */}
      <div className="flex items-center justify-between rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <div>
          <p className="text-[13px] font-medium">Underwriting workspace</p>
          <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
            Runs the rules engine over accepted extractions and drafts a cited
            committee summary. Rerunning replaces the previous draft.
          </p>
        </div>
        <PrimaryButton
          Icon={IconSparkles}
          onClick={runUnderwriting}
          disabled={agentRun.isRunning}
        >
          {agentRun.isRunning
            ? "Running…"
            : result
              ? "Re-run agent"
              : "Run underwriting agent"}
        </PrimaryButton>
      </div>

      {agentRun.nodes.length > 0 && (
        <AgentProgress
          title="Underwriting agent"
          nodes={agentRun.nodes}
          error={agentRun.error}
          errorDetail={agentRun.errorDetail}
          skipReason={agentRun.skipReason}
        />
      )}

      {/* KPI strip — always rendered when we have rules data, even
          before the agent has been clicked. Reflects the live deterministic
          state of the loan, so the workspace is never empty. */}
      {liveKpis && <KpiStrip kpis={liveKpis} />}

      {/* AI recommendation banner — only when the LLM-driven agent has
          produced one. This is the "AI work" surface; everything else
          on the page is the deterministic underlay. */}
      {result &&
        (() => {
          const r = RECOMMENDATION_COPY[result.recommendation];
          return (
            <div
              className="rounded-lg border-[0.5px] p-4"
              style={{
                background: r.bg,
                borderColor: "var(--color-border-tertiary)",
              }}
            >
              <div className="flex items-baseline justify-between gap-3">
                <p className="text-[13px] font-medium" style={{ color: r.fg }}>
                  {r.label}
                </p>
                <p
                  className="text-[10px] uppercase tracking-wider"
                  style={{ color: r.fg }}
                >
                  AI recommendation · {humanizePropertyType(result.kpis.property_type)}
                </p>
              </div>
              <p className="mt-1 text-xs leading-relaxed" style={{ color: r.fg }}>
                {r.description}
              </p>
              <p className="mt-2 text-[13px] leading-relaxed" style={{ color: r.fg }}>
                {result.rationale}
              </p>
            </div>
          );
        })()}

      {/* Extractions + Risk signals — always-on. The right column
          shows whatever the rules engine evaluates to right now; the
          left column shows the per-field extractions with confidence
          dots. Both update when the agent run completes, but neither
          requires it. */}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
          <SectionLabel Icon={IconSparkles}>Extracted from packet</SectionLabel>
          <ExtractionsList
            extractionByField={extractionByField}
            citedFields={citedFields}
          />
        </div>
        <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
          <SectionLabel Icon={IconAlertTriangle}>
            Risk signals
            {!result && liveFlags.length > 0 && (
              <span className="ml-1.5 text-[10px] font-normal text-[var(--color-text-tertiary)]">
                · rules only · run agent for cited summary
              </span>
            )}
          </SectionLabel>
          {liveFlags.length > 0 ? (
            <RiskSignals flags={liveFlags} />
          ) : (
            <p className="text-[12px] text-[var(--color-text-tertiary)]">
              No risk flags yet — upload a document and run intake to populate
              the rule inputs.
            </p>
          )}
        </div>
      </div>

      {/* The LLM-drafted cited summary lives below the deterministic
          panels. This is the only block that requires Run. */}
      {result && (
        <>
          {/* Cited summary */}
          <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
            <SectionLabel
              Icon={IconFileText}
              trailing={
                <>
                  Generated {new Date(result.generated_at).toLocaleString()} ·
                  cited from {citedFields.size} field
                  {citedFields.size === 1 ? "" : "s"}
                </>
              }
            >
              Underwriting summary
            </SectionLabel>
            <div className="flex flex-col gap-3">
              {result.sections.map((s, i) => (
                <CitedSection
                  key={s.title}
                  section={s}
                  index={i}
                  extractionByField={extractionByField}
                />
              ))}
            </div>
          </div>

          <p className="text-[10px] text-[var(--color-text-tertiary)]">
            agent_run {result.agent_run_id.slice(0, 8)} · cached on this view
          </p>
        </>
      )}

      {/* AskTheFile lives outside the LLM-output gate — it operates
          purely on the document RAG store, so it's useful even before
          underwriting has been run. */}
      <AskTheFile loanId={loanId} />
    </div>
  );
}
