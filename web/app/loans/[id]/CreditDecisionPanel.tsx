"use client";

import { useState } from "react";
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
import { toast } from "sonner";
import { humanizeStatus } from "@/lib/humanize";
import { useAgentRun } from "@/lib/useAgentRun";
import { AgentProgress } from "@/app/components/AgentProgress";
import { BorrowerMessagePreviewModal } from "@/app/components/BorrowerMessagePreviewModal";
import { MarkdownBlock } from "@/app/components/MarkdownBlock";
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

/** Build the borrower-visible message body for a decision. The body
 *  is what lands on /apply/[id] when the underwriter clicks "Send to
 *  borrower" — see the rationale in :func:`sendDecisionToBorrower`. */
function composeBorrowerMessage(result: DecisionResult): string {
  if (result.path === "decline") {
    // Adverse action letter is required to cite the specific reasons.
    // We trust the agent's draft — the underwriter has already had a
    // chance to "Edit letter" before clicking send.
    return result.adverse_action_letter?.body_text ?? result.verdict_text;
  }
  const ts = result.term_sheet;
  const lines: string[] = [];
  if (result.path === "approve") {
    lines.push("We've approved your loan application.");
  } else {
    lines.push("We've conditionally approved your loan application.");
  }
  if (ts) {
    lines.push(
      "",
      "Term sheet:",
      `· Principal: $${ts.principal}`,
      `· Rate: ${ts.rate_pct.toFixed(2)}% (${ts.rate_basis})`,
      `· Term: ${ts.term_months} months`,
      `· Amortization: ${ts.amortization}`,
      `· Origination fee: ${ts.origination_fee_pct.toFixed(2)}%`,
      `· Prepay: ${ts.prepay_terms}`,
    );
    if (ts.notes) lines.push("", ts.notes);
  }
  if (result.path === "conditional" && result.conditions.length > 0) {
    lines.push(
      "",
      "Conditions to satisfy before closing:",
      ...result.conditions.map(
        (c, i) =>
          `${i + 1}. ${c.description}${
            c.due_within_days
              ? ` (within ${c.due_within_days} days)`
              : ""
          }`,
      ),
    );
  }
  lines.push("", "Sign in to your application to see updates.");
  return lines.join("\n");
}

/** Target stage for each decision path. ``conditional`` is the only
 *  path that goes through CONDITIONS before APPROVED → CLOSING.
 *  ``approve`` jumps directly to APPROVED because there are no
 *  outstanding conditions. */
function targetStageFor(path: DecisionPath): "conditions" | "approved" | "declined" {
  if (path === "approve") return "approved";
  if (path === "conditional") return "conditions";
  return "declined";
}

/**
 * Decision action buttons.
 *
 * "Send to borrower" / "Send adverse action letter" do TWO things,
 * in this order:
 *
 *   1. Write a borrower-visible note (action ``borrower_reply`` —
 *      same audit shape the timeline + the /apply/[id] view both
 *      already consume). Body composed from the agent's draft +
 *      whatever the underwriter saw on screen.
 *   2. Transition the loan stage: decision → approved / conditions /
 *      declined depending on the path.
 *
 * The two writes aren't atomic at the DB level but the audit log
 * shows both intents, so an operator can see exactly what happened
 * if step 2 fails (the message is still on the timeline).
 *
 * "Send to committee" is intentionally still audit-only — Mkopo
 * doesn't model a committee surface today; the audit event is the
 * canonical handoff signal.
 *
 * "Edit term sheet" / "Edit letter" surface a toast pointing at the
 * agent re-run rather than silently logging; an in-place editor is
 * out of scope (the underwriter can re-run the decision agent with
 * adjusted rules if the term sheet needs material changes).
 */
function ActionBar({
  loanId,
  selectedPath,
  result,
}: {
  loanId: string;
  selectedPath: DecisionPath;
  result: DecisionResult;
}) {
  const queryClient = useQueryClient();
  // Whether the preview modal is open. The send-to-borrower button
  // doesn't fire the mutation directly anymore — it opens this modal,
  // and the modal's onConfirm passes the edited body into the
  // mutation. Adds the missing "last-mile review" step that used to
  // be missing from the panel.
  const [previewOpen, setPreviewOpen] = useState(false);

  const sendDecisionToBorrower = useMutation({
    mutationFn: async (editedBody: string) => {
      // Note first so the timeline records the message even if the
      // stage transition then fails (e.g. someone advanced the loan
      // in another tab and the transition is now invalid).
      await api.addNote(loanId, editedBody, "borrower_reply");
      const target = targetStageFor(selectedPath);
      await api.transitionStage(
        loanId,
        target,
        `decision_panel · path=${selectedPath}`,
      );
      return target;
    },
    onSuccess: (target) => {
      // Refetch every surface that observes either the message stream
      // or the stage. ``materials`` is invalidated too because the
      // drift banner re-evaluates against the new decision-stage.
      queryClient.invalidateQueries({ queryKey: ["loan", loanId, "audit"] });
      queryClient.invalidateQueries({ queryKey: ["loan", loanId] });
      queryClient.invalidateQueries({ queryKey: ["loan", loanId, "materials"] });
      setPreviewOpen(false);
      toast.success(
        selectedPath === "decline"
          ? "Adverse action letter sent. Loan moved to declined."
          : `Decision sent to borrower. Loan moved to ${target}.`,
      );
    },
    onError: (e) => {
      const msg = e instanceof Error ? e.message : String(e);
      toast.error("Couldn't send the decision", { description: msg });
    },
  });

  const sendToCommittee = useMutation({
    mutationFn: () =>
      api.addNote(
        loanId,
        `Routed to credit committee for review. Path: ${selectedPath}.`,
        "internal_note",
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["loan", loanId, "audit"] });
      toast.success("Routed to committee", {
        description:
          "Logged on the timeline. Committee handoff is internal — borrower isn't notified until you click \"Send to borrower\".",
      });
    },
  });

  // "Edit term sheet" / "Edit letter" — placeholder until we wire a
  // proper editor. Surfaces the right next step rather than silently
  // logging, which the previous stub did.
  const editStub = () =>
    toast.info(
      selectedPath === "decline"
        ? "To revise the letter, re-run the decision agent with adjusted rules."
        : "To revise the term sheet, re-run the decision agent with adjusted inputs.",
    );

  // Pre-compose the draft body once so the modal opens populated. The
  // modal owns local edits from there; this string is just the seed.
  const previewBody = composeBorrowerMessage(result);
  const isDecline = selectedPath === "decline";
  const principalReasons = isDecline
    ? result.adverse_action_letter?.principal_reasons
    : undefined;

  return (
    <>
      {isDecline ? (
        <div className="flex flex-wrap items-center justify-end gap-2">
          <SecondaryButton Icon={IconPencil} onClick={editStub}>
            Edit letter
          </SecondaryButton>
          {/* Danger primary — distinct from brand-green because the
              consequence (adverse action letter) is the most loaded action
              in the system. Opens the preview modal instead of firing
              directly so the staff member gets a last-mile review of the
              text the borrower will read. */}
          <button
            onClick={() => setPreviewOpen(true)}
            disabled={sendDecisionToBorrower.isPending}
            className="flex items-center gap-1 rounded-md px-3 py-1.5 text-xs font-medium disabled:opacity-50"
            style={{
              background: "var(--color-text-danger)",
              color: "var(--color-background-danger)",
            }}
          >
            Review &amp; send adverse action letter
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <SecondaryButton Icon={IconPencil} onClick={editStub}>
            Edit term sheet
          </SecondaryButton>
          <div className="flex gap-1.5">
            <SecondaryButton
              onClick={() => sendToCommittee.mutate()}
              disabled={sendToCommittee.isPending}
            >
              Send to committee
            </SecondaryButton>
            <PrimaryButton onClick={() => setPreviewOpen(true)}>
              Review &amp; send to borrower
            </PrimaryButton>
          </div>
        </div>
      )}

      <BorrowerMessagePreviewModal
        open={previewOpen}
        title={
          isDecline
            ? "Send adverse action letter"
            : "Send decision to borrower"
        }
        description={
          isDecline
            ? "The borrower will see this exact text. ECOA Reg B requires each principal reason to be referenced by name in the body."
            : "Review and edit before sending. The borrower will see this text on their /apply page."
        }
        variant={isDecline ? "danger" : "default"}
        initialBody={previewBody}
        principalReasons={principalReasons}
        confirmLabel={
          isDecline ? "Send adverse action letter" : "Send to borrower"
        }
        isSubmitting={sendDecisionToBorrower.isPending}
        onConfirm={({ body }) => sendDecisionToBorrower.mutate(body)}
        onClose={() => setPreviewOpen(false)}
      />
    </>
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
        // Pre-flight gate fired (e.g. underwriting hasn't run yet) —
        // show the friendly reason instead of a generic "no result".
        if (ev.skip_reason) {
          toast.message("Decision didn't run", {
            description: ev.skip_reason,
          });
        } else if (data) {
          toast.success("Decision drafted", {
            description: `${PATH_META[data.path].label} · ${Math.round(data.confidence * 100)}% confidence`,
          });
        }
      },
    });

  // Default the selected path to whatever's in the latest result, if
  // any. React-19 "set state during render with a guard" — the
  // alternative useEffect form causes a cascading render, which the
  // react-hooks/set-state-in-effect lint flags.
  const result = decisionQuery.data;
  const [seenResult, setSeenResult] = useState(result);
  if (seenResult !== result) {
    setSeenResult(result);
    if (result && selectedPath == null) {
      setSelectedPath(result.path);
    }
  }

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
          errorDetail={agentRun.errorDetail}
          skipReason={agentRun.skipReason}
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
              <div className="mt-2 text-[var(--color-text-primary)]">
                <MarkdownBlock variant="relaxed">
                  {result.rationale}
                </MarkdownBlock>
              </div>
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
                (recommendation was <strong>{PATH_META[result.path].label}</strong>).
                Re-run the agent or override manually before sending.
              </p>
            )}

          {/* Action bar — "Send to borrower" / AAL now writes the
              borrower-visible note AND transitions the stage. See the
              ActionBar docstring for the boundary between this and the
              decision-agent's drafting work. */}
          {selectedPath && (
            <ActionBar
              loanId={loanId}
              selectedPath={selectedPath}
              result={result}
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
