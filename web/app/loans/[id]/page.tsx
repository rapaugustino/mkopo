"use client";

import Link from "next/link";
import { use, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { IconFileText, IconFolder, IconSparkles } from "@tabler/icons-react";
import { api, type AuditEvent, type IntakeInterrupt, type Loan } from "@/lib/api";
import { humanizeLoanType, humanizePartyType } from "@/lib/humanize";
import { useAgentRun } from "@/lib/useAgentRun";
import { AgentProgress } from "@/app/components/AgentProgress";
import { BrandHeader } from "@/app/components/BrandHeader";
import { PrimaryButton } from "@/app/components/PrimaryButton";
import { SecondaryButton } from "@/app/components/SecondaryButton";
import { StagePill } from "@/app/components/StagePill";
import { CaseFileTimeline } from "./CaseFileTimeline";
import { CreditDecisionPanel } from "./CreditDecisionPanel";
import { IntakeApprovalModal } from "./IntakeApprovalModal";
import { UnderwritingWorkspace } from "./UnderwritingWorkspace";

interface PageProps {
  params: Promise<{ id: string }>;
}

type Tab = "activity" | "underwriting" | "decision";
const TABS: { id: Tab; label: string }[] = [
  { id: "activity", label: "Activity" },
  { id: "underwriting", label: "Underwriting" },
  { id: "decision", label: "Decision" },
];

/** Guarantors render as small clickable chips below the header.
 *
 * Discoverability hook for the entity inspector at /parties/[id]. Hovering
 * shows the party type ("entity" / "person"); clicking navigates.
 */
function GuarantorChips({ loan }: { loan: Loan }) {
  if (loan.guarantors.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
      <span className="text-[var(--color-text-secondary)]">Guarantors:</span>
      {loan.guarantors.map((g) => (
        <Link
          key={g.id}
          href={`/parties/${g.id}`}
          title={`${humanizePartyType(g.party_type)} · view profile`}
          className="rounded-full border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2 py-0.5 font-medium text-[var(--color-text-info)] hover:bg-[var(--color-background-secondary)]"
        >
          {g.name}
        </Link>
      ))}
    </div>
  );
}

export default function LoanPage({ params }: PageProps) {
  const { id } = use(params);
  const queryClient = useQueryClient();
  const router = useRouter();
  const searchParams = useSearchParams();
  const activeTab: Tab = (searchParams.get("tab") as Tab) || "activity";

  const setTab = (tab: Tab) => {
    const next = new URLSearchParams(searchParams.toString());
    next.set("tab", tab);
    router.replace(`/loans/${id}?${next.toString()}`, { scroll: false });
  };

  const [pendingInterrupt, setPendingInterrupt] = useState<IntakeInterrupt | null>(null);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  const loanQuery = useQuery<Loan, Error>({
    queryKey: ["loan", id],
    queryFn: () => api.getLoan(id),
  });

  const auditQuery = useQuery<AuditEvent[], Error>({
    queryKey: ["loan", id, "audit"],
    queryFn: () => api.getAuditEvents(id),
  });

  const invalidateLoan = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["loan", id] }),
      queryClient.invalidateQueries({ queryKey: ["loan", id, "audit"] }),
      queryClient.invalidateQueries({ queryKey: ["loans"] }),
    ]);

  // Intake runs over SSE — `useAgentRun` exposes the live node trail
  // plus callbacks for the two terminal events we care about
  // (interrupt → open modal; done → refresh audit log + loan).
  const intakeRun = useAgentRun();
  const startIntake = () =>
    intakeRun.run({
      path: `/loans/${id}/agents/intake/run`,
      onInterrupt: (payload) =>
        // The stream's interrupt payload IS the IntakeInterrupt shape
        // the backend used to return synchronously — cast and surface.
        setPendingInterrupt(payload as unknown as IntakeInterrupt),
      onDone: async (ev) => {
        await invalidateLoan();
        if (ev.status === "complete") {
          setStatusMsg("Intake complete — nothing missing, no email needed.");
        } else if (ev.status !== "awaiting_approval") {
          setStatusMsg(`Intake finished — status: ${ev.status}.`);
        }
      },
    });

  // Resume also streams (remaining nodes — `send` if approved, none if
  // cancelled). We pass the resume payload as the request body and
  // re-use the same node-trail UI.
  const resumeRun = useAgentRun();
  const resumeSend = async (subject: string, bodyText: string) => {
    await resumeRun.run({
      path: `/loans/${id}/agents/intake/resume`,
      body: { action: "send", subject, body_text: bodyText },
      onDone: async () => {
        await invalidateLoan();
        setStatusMsg("Email sent — see audit log below.");
      },
    });
  };
  const resumeCancel = async () => {
    await resumeRun.run({
      path: `/loans/${id}/agents/intake/resume`,
      body: { action: "cancel" },
      onDone: async () => {
        await invalidateLoan();
        setStatusMsg("Draft cancelled — no email was sent.");
      },
    });
  };

  const error = loanQuery.error || auditQuery.error;
  const loan = loanQuery.data;
  const events = auditQuery.data ?? [];

  if (error && !loan) {
    return <p className="text-sm text-[var(--color-text-danger)]">Error: {error.message}</p>;
  }
  if (!loan) return <p className="text-sm">Loading…</p>;

  const ownerLine = loan.owner ? ` · ${loan.owner.name} (owner)` : "";
  const subTitle = `${humanizeLoanType(loan.loan_type)} loan · $${Number(loan.amount).toLocaleString()}${ownerLine}`;

  return (
    <div className="flex flex-col gap-4">
      <BrandHeader
        title={
          <span>
            {loan.reference}
            <span className="text-[var(--color-text-secondary)]"> · </span>
            <span className="text-[var(--color-text-primary)]">
              {loan.borrower?.name ?? "—"}
            </span>
          </span>
        }
        sub={subTitle}
        badge={<StagePill stage={loan.stage} />}
        actions={
          <>
            <SecondaryButton Icon={IconFolder}>Docs</SecondaryButton>
            <SecondaryButton Icon={IconFileText}>Open file</SecondaryButton>
            {/* "Run intake" is the demo's main "kick off the agent"
                affordance. Only relevant while the loan is still in
                intake — past that point the workspace tab has its own
                "Re-run agent" buttons, so we hide this to keep the
                header uncluttered. */}
            {loan.stage === "intake" && (
              <PrimaryButton
                Icon={IconSparkles}
                onClick={startIntake}
                disabled={intakeRun.isRunning || resumeRun.isRunning}
              >
                {intakeRun.isRunning ? "Running intake…" : "Run intake"}
              </PrimaryButton>
            )}
          </>
        }
      />

      <GuarantorChips loan={loan} />

      {/* Live progress trail from the SSE stream. Two streams can be
          active in this view: the initial intake run, then a resume
          run after the underwriter approves the draft. Render whichever
          has nodes — they share the same UI affordance. */}
      {(intakeRun.nodes.length > 0 || resumeRun.nodes.length > 0) && (
        <AgentProgress
          title={resumeRun.nodes.length > 0 ? "Sending borrower email" : "Intake agent"}
          nodes={resumeRun.nodes.length > 0 ? resumeRun.nodes : intakeRun.nodes}
          error={resumeRun.error ?? intakeRun.error}
        />
      )}

      {statusMsg && (
        <p className="rounded bg-[var(--color-background-secondary)] px-3 py-2 text-xs text-[var(--color-text-secondary)]">
          {statusMsg}
        </p>
      )}

      <nav
        className="flex gap-1 border-b-[0.5px] border-[var(--color-border-tertiary)]"
        role="tablist"
        aria-label="Loan sections"
      >
        {TABS.map((t) => {
          const isActive = activeTab === t.id;
          return (
            <button
              key={t.id}
              role="tab"
              aria-selected={isActive}
              onClick={() => setTab(t.id)}
              className={
                "px-3 py-2 text-xs font-medium transition-colors " +
                (isActive
                  ? "border-b-2 text-[var(--color-text-primary)]"
                  : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]")
              }
              style={isActive ? { borderColor: "var(--color-brand)" } : undefined}
            >
              {t.label}
            </button>
          );
        })}
      </nav>

      {activeTab === "activity" && <CaseFileTimeline loanId={id} events={events} />}
      {activeTab === "underwriting" && <UnderwritingWorkspace loanId={id} />}
      {activeTab === "decision" && <CreditDecisionPanel loanId={id} />}

      {pendingInterrupt && (
        <IntakeApprovalModal
          interrupt={pendingInterrupt}
          onSend={async (subject, bodyText) => {
            await resumeSend(subject, bodyText);
          }}
          onCancel={async () => {
            await resumeCancel();
          }}
          onClose={() => setPendingInterrupt(null)}
        />
      )}
    </div>
  );
}
