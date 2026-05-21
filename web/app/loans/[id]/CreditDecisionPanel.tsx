"use client";

import { useState, useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  IconAlertTriangle,
  IconCheck,
  IconFileCheck,
  IconFileText,
  IconListCheck,
  IconPencil,
  IconSparkles,
  IconX,
} from "@tabler/icons-react";
import {
  api,
  type Condition,
  type DecisionPath,
  type DecisionResult,
} from "@/lib/api";
import { humanizeStatus } from "@/lib/humanize";
import { useAgentRun } from "@/lib/useAgentRun";
import { AgentProgress } from "@/app/components/AgentProgress";
import { Pill } from "@/app/components/Pill";
import { PrimaryButton } from "@/app/components/PrimaryButton";
import { QuoteBlock } from "@/app/components/QuoteBlock";
import { SecondaryButton } from "@/app/components/SecondaryButton";
import { SectionLabel } from "@/app/components/SectionLabel";

interface Props {
  loanId: string;
}

const PATH_META: Record<
  DecisionPath,
  {
    label: string;
    Icon: React.ComponentType<{ size?: number }>;
    bg: string;
    fg: string;
    sub: string;
  }
> = {
  approve: {
    label: "Approve",
    Icon: IconCheck,
    bg: "var(--color-background-success)",
    fg: "var(--color-text-success)",
    sub: "Standard terms, no conditions",
  },
  conditional: {
    label: "Conditional",
    Icon: IconListCheck,
    bg: "var(--color-background-warning)",
    fg: "var(--color-text-warning)",
    sub: "Conditions to close required",
  },
  decline: {
    label: "Decline",
    Icon: IconX,
    bg: "var(--color-background-danger)",
    fg: "var(--color-text-danger)",
    sub: "Drafts an ECOA adverse-action letter",
  },
};

function PathCard({
  path,
  selected,
}: {
  path: DecisionPath;
  selected: boolean;
}) {
  const meta = PATH_META[path];
  const Icon = meta.Icon;
  return (
    <div
      className="flex-1 rounded-md border-[0.5px] bg-[var(--color-background-primary)] px-3 py-2.5"
      style={{
        borderColor: selected ? "var(--color-brand)" : "var(--color-border-tertiary)",
        borderWidth: selected ? 2 : undefined,
        padding: selected ? "calc(0.625rem - 1.5px) calc(0.75rem - 1.5px)" : undefined,
      }}
    >
      <p className="flex items-center gap-1.5 text-[13px] font-medium">
        <Icon size={14} />
        {meta.label}
      </p>
      <p className="mt-1 text-[11px] text-[var(--color-text-secondary)]">
        {meta.sub}
      </p>
    </div>
  );
}

function TermSheetView({ ts }: { ts: NonNullable<DecisionResult["term_sheet"]> }) {
  const rows: [string, React.ReactNode][] = [
    ["Principal", `$${Number(ts.principal).toLocaleString()}`],
    ["Rate", `${ts.rate_pct.toFixed(2)}% (${ts.rate_basis})`],
    ["Term", `${ts.term_months} months, ${ts.amortization}`],
    ["Origination fee", `${ts.origination_fee_pct.toFixed(2)}%`],
    ["Prepay", ts.prepay_terms],
  ];
  return (
    <div className="rounded-md bg-[var(--color-background-secondary)] px-3 py-2.5">
      <table className="w-full text-[12.5px]">
        <tbody>
          {rows.map(([k, v]) => (
            <tr
              key={k}
              className="border-b-[0.5px] border-[var(--color-border-tertiary)] last:border-b-0"
            >
              <td className="py-1.5 text-[var(--color-text-secondary)]">{k}</td>
              <td className="py-1.5 text-right font-medium">{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {ts.notes && (
        <p className="mt-2 text-[11px] italic text-[var(--color-text-tertiary)]">
          {ts.notes}
        </p>
      )}
    </div>
  );
}

function AdverseActionLetterView({
  letter,
}: {
  letter: NonNullable<DecisionResult["adverse_action_letter"]>;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div>
        <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
          Principal reasons (ECOA Reg B)
        </p>
        <div className="mt-1 flex flex-wrap gap-1">
          {letter.principal_reasons.map((r) => (
            <Pill key={r} variant="danger">
              {r}
            </Pill>
          ))}
        </div>
      </div>
      <QuoteBlock caption={`Subject: ${letter.subject}`}>
        {letter.body_text}
      </QuoteBlock>
    </div>
  );
}

/**
 * Heuristic title for a condition row — the mockup shows a short name +
 * a longer description, but our backend only stores `description`. We
 * lift the first sentence as a title; if the description is one short
 * sentence it just renders alone.
 */
function splitCondition(text: string): { title: string; detail: string | null } {
  const trimmed = text.trim();
  const m = trimmed.match(/^([^.\n]{6,140}[.\n])(.*)$/s);
  if (!m) return { title: trimmed, detail: null };
  const rest = m[2].trim();
  return { title: m[1].trim(), detail: rest || null };
}

function ConditionsList({
  conditions,
  isLoading,
}: {
  conditions: Condition[];
  isLoading: boolean;
}) {
  if (isLoading) {
    return <p className="text-xs text-[var(--color-text-tertiary)]">Loading…</p>;
  }
  if (conditions.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-tertiary)]">
        No conditions yet.
      </p>
    );
  }
  return (
    <div className="flex flex-col">
      {conditions.map((c, i) => {
        const { title, detail } = splitCondition(c.description);
        return (
          <div
            key={c.id}
            className={`grid grid-cols-[18px_1fr] gap-2.5 py-2.5 ${
              i > 0 ? "border-t-[0.5px] border-[var(--color-border-tertiary)]" : ""
            }`}
          >
            <IconFileCheck
              size={14}
              className="mt-0.5 shrink-0 text-[var(--color-text-tertiary)]"
            />
            <div className="min-w-0 flex-1">
              <p className="text-[13px] font-medium">{title}</p>
              {detail && (
                <p className="mt-0.5 text-[12px] leading-relaxed text-[var(--color-text-secondary)]">
                  {detail}
                </p>
              )}
              <p className="mt-0.5 text-[10px] text-[var(--color-text-tertiary)]">
                {c.drafted_by_agent ? "AI-drafted · " : ""}
                Status: {humanizeStatus(c.status)}
              </p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Decision action buttons — stubs in this phase.
 *
 * Each click writes an audit_event so the timeline reflects the user's
 * intent (e.g. "Sent term sheet to borrower"). Real builds would also:
 *   - send the term sheet / decline letter via Resend
 *   - transition the loan stage via transition_stage (decision → conditions
 *     for conditional approve / approved for full approve / declined for AAL)
 *
 * We intentionally don't transition the stage here — that's a deliberate
 * downstream action keyed to "the underwriter clicked Send", not to the
 * agent itself. Same boundary discipline as the rest of the system.
 */
function ActionBar({
  loanId,
  selectedPath,
}: {
  loanId: string;
  selectedPath: DecisionPath;
}) {
  const queryClient = useQueryClient();
  const log = useMutation({
    mutationFn: (target: string) =>
      api.addNote(loanId, `decision_apply · path=${selectedPath} · target=${target}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["loan", loanId, "audit"] });
    },
  });

  if (selectedPath === "decline") {
    return (
      <div className="flex flex-wrap items-center justify-end gap-2">
        <SecondaryButton
          Icon={IconPencil}
          onClick={() => log.mutate("internal_review")}
        >
          Edit letter
        </SecondaryButton>
        {/* Danger primary — distinct from brand-green because the
            consequence (adverse action letter) is the most loaded action
            in the system. */}
        <button
          onClick={() => log.mutate("borrower")}
          disabled={log.isPending}
          className="flex items-center gap-1 rounded-md px-3 py-1.5 text-xs font-medium disabled:opacity-50"
          style={{
            background: "var(--color-text-danger)",
            color: "var(--color-background-danger)",
          }}
        >
          Send adverse action letter
        </button>
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <SecondaryButton
        Icon={IconPencil}
        onClick={() => log.mutate("internal_review")}
      >
        Edit term sheet
      </SecondaryButton>
      <div className="flex gap-1.5">
        <SecondaryButton
          onClick={() => log.mutate("committee")}
          disabled={log.isPending}
        >
          Send to committee
        </SecondaryButton>
        <PrimaryButton
          onClick={() => log.mutate("borrower")}
          disabled={log.isPending}
        >
          Send to borrower
        </PrimaryButton>
      </div>
    </div>
  );
}


export function CreditDecisionPanel({ loanId }: Props) {
  const queryClient = useQueryClient();
  const [selectedPath, setSelectedPath] = useState<DecisionPath | null>(null);

  const decisionQuery = useQuery<DecisionResult | null, Error>({
    queryKey: ["loan", loanId, "decision"],
    queryFn: async () => null,
    staleTime: Infinity,
  });

  const conditionsQuery = useQuery<Condition[], Error>({
    queryKey: ["loan", loanId, "conditions"],
    queryFn: () => api.getConditions(loanId),
  });

  // Decision agent streams via SSE — same three-node trail as
  // underwriting, with the final ``done`` event carrying the
  // DecisionResult to drop into the query cache.
  const agentRun = useAgentRun();
  const runDecision = () =>
    agentRun.run({
      path: `/loans/${loanId}/agents/decision/run`,
      onDone: async (ev) => {
        const data = ev.result as DecisionResult | null;
        if (data) {
          queryClient.setQueryData(["loan", loanId, "decision"], data);
          setSelectedPath(data.path);
        }
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ["loan", loanId, "conditions"] }),
          queryClient.invalidateQueries({ queryKey: ["loan", loanId, "audit"] }),
        ]);
      },
    });

  // Default the selected path to whatever's in the latest result, if any.
  const result = decisionQuery.data;
  useEffect(() => {
    if (result && selectedPath == null) {
      setSelectedPath(result.path);
    }
  }, [result, selectedPath]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <div>
          <p className="text-[13px] font-medium">Credit decision</p>
          <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
            Picks a path on top of the underwriting workup. Conditions are
            written to the conditions table; an ECOA-compliant decline letter
            cites the specific failed rules.
          </p>
        </div>
        <PrimaryButton
          Icon={IconSparkles}
          onClick={runDecision}
          disabled={agentRun.isRunning}
        >
          {agentRun.isRunning
            ? "Running…"
            : result
              ? "Re-run agent"
              : "Run decision agent"}
        </PrimaryButton>
      </div>

      {agentRun.nodes.length > 0 && (
        <AgentProgress
          title="Decision agent"
          nodes={agentRun.nodes}
          error={agentRun.error}
        />
      )}

      {!result && !agentRun.isRunning && agentRun.nodes.length === 0 && (
        <div className="rounded-lg border-[0.5px] border-dashed border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] p-8 text-center">
          <p className="text-sm text-[var(--color-text-secondary)]">
            No decision run yet. Click <strong>Run decision agent</strong>.
            (Best run after Underwriting has produced rule outcomes.)
          </p>
        </div>
      )}

      {result && (
        <>
          {/* Recommendation card — the AI's call. Icon stays brand-green
              regardless of the chosen path: it's the "this is AI work"
              signal, not the verdict's signal. Verdict colour lives in
              the three path cards below. */}
          <div className="grid grid-cols-[36px_1fr] items-start gap-3 rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] p-4">
            <div
              className="flex h-9 w-9 items-center justify-center rounded-full"
              style={{
                background: "var(--color-background-success)",
                color: "var(--color-brand)",
              }}
            >
              <IconSparkles size={16} />
            </div>
            <div>
              <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
                AI recommendation · {Math.round(result.confidence * 100)}% confidence
              </p>
              {/* Verdict is set in Source Serif. It's the one place in
                  the app where the LLM is putting its name on a
                  committee-bound recommendation — serif reads like a
                  signature rather than a chip. */}
              <p className="font-editorial mt-1 text-[20px] leading-tight">
                {result.verdict_text}
              </p>
              <p className="mt-2 text-[13px] leading-relaxed text-[var(--color-text-primary)]">
                {result.rationale}
              </p>
            </div>
          </div>

          {/* 3 paths */}
          <div className="flex gap-2">
            {(["approve", "conditional", "decline"] as DecisionPath[]).map((p) => (
              <button
                key={p}
                onClick={() => setSelectedPath(p)}
                className="block flex-1 text-left"
              >
                <PathCard
                  path={p}
                  selected={selectedPath === p}
                />
              </button>
            ))}
          </div>

          {/* Conditions */}
          {selectedPath === "conditional" && (
            <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
              <SectionLabel Icon={IconListCheck}>
                Conditions to close ({conditionsQuery.data?.length ?? 0})
              </SectionLabel>
              <ConditionsList
                conditions={conditionsQuery.data ?? []}
                isLoading={conditionsQuery.isPending}
              />
            </div>
          )}

          {/* Term sheet (approve / conditional) */}
          {(selectedPath === "approve" || selectedPath === "conditional") &&
            result.term_sheet && (
              <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
                <SectionLabel
                  Icon={IconFileText}
                  trailing="Auto-generated · editable in a real build"
                >
                  Term sheet draft
                </SectionLabel>
                <TermSheetView ts={result.term_sheet} />
              </div>
            )}

          {/* Adverse action letter (decline) */}
          {selectedPath === "decline" && result.adverse_action_letter && (
            <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
              <SectionLabel Icon={IconAlertTriangle}>
                Adverse action letter · ECOA Reg B
              </SectionLabel>
              <AdverseActionLetterView letter={result.adverse_action_letter} />
            </div>
          )}

          {/* User selected a path the agent didn't draft for */}
          {selectedPath &&
            selectedPath !== result.path &&
            ((selectedPath === "decline" && !result.adverse_action_letter) ||
              ((selectedPath === "approve" || selectedPath === "conditional") &&
                !result.term_sheet)) && (
              <p className="rounded bg-[var(--color-background-warning)] px-3 py-2 text-xs text-[var(--color-text-warning)]">
                You&apos;ve selected a path the AI didn&apos;t draft for
                (recommendation was <strong>{result.path}</strong>). Re-run the
                agent or override manually before sending.
              </p>
            )}

          {/* Action bar — stubs in this phase: each writes an audit event.
              Real builds would send via Resend / transition the loan stage. */}
          {selectedPath && (
            <ActionBar
              loanId={loanId}
              selectedPath={selectedPath}
            />
          )}

          <p className="text-[10px] text-[var(--color-text-tertiary)]">
            agent_run {result.agent_run_id.slice(0, 8)} ·{" "}
            {new Date(result.generated_at).toLocaleString()}
          </p>
        </>
      )}
    </div>
  );
}
