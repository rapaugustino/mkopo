"use client";

import Link from "next/link";
import { use, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  IconArrowLeft,
  IconChevronLeft,
  IconChevronRight,
  IconClipboardList,
  IconGavel,
  IconMessageCircle,
  IconMicroscope,
  IconRoute,
  IconSparkles,
  IconTimeline,
} from "@tabler/icons-react";
import { toast } from "sonner";
import { api, type AuditEvent, type IntakeInterrupt, type Loan } from "@/lib/api";
import { humanizeLoanType, humanizePartyType, titleCase } from "@/lib/humanize";
import { useAgentRun } from "@/lib/useAgentRun";
import { AgentProgress } from "@/app/components/AgentProgress";
import { LoanLockBanner } from "./LoanLockBanner";
import { MaterialsFlow } from "./MaterialsFlow";
import { StaffChat } from "./StaffChat";
import { BrandHeader } from "@/app/components/BrandHeader";
import { PrimaryButton } from "@/app/components/PrimaryButton";
import { SecondaryButton } from "@/app/components/SecondaryButton";
import { Skeleton } from "@/app/components/Skeleton";
import { StagePill } from "@/app/components/StagePill";
import { AutonomyToggle } from "./AutonomyToggle";
import { CaseFileTimeline } from "./CaseFileTimeline";
import { CreditDecisionPanel } from "./CreditDecisionPanel";
import { OwnerPicker } from "./OwnerPicker";
import { LoanTrace } from "./LoanTrace";
import { DocsPanel } from "./DocsPanel";
import { IntakeApprovalModal } from "./IntakeApprovalModal";
import { StageActions } from "./StageActions";
import { UnderwritingWorkspace } from "./UnderwritingWorkspace";

interface PageProps {
  params: Promise<{ id: string }>;
}

type Tab = "activity" | "underwriting" | "decision" | "trace" | "assistant";

interface PhaseNavItem {
  id: Tab;
  label: string;
  description: string;
  Icon: React.ComponentType<{ size?: number }>;
}

/** Left-rail phase navigation on the loan detail page.
 *
 *  The order reflects the lifecycle of a loan from the underwriter's
 *  point of view — what you read first (Activity), what you analyse
 *  next (Underwriting), what you commit to (Decision), what you
 *  audit (Trace), and what you ask questions of (Assistant). The
 *  numbered keyboard shortcuts (1..5) reinforce this order so a
 *  power user moves left-to-right through a deal without touching
 *  the mouse.
 */
const TABS: PhaseNavItem[] = [
  {
    id: "activity",
    label: "Activity",
    description: "Case file timeline + documents",
    Icon: IconTimeline,
  },
  {
    id: "underwriting",
    label: "Underwriting",
    description: "Rules, KPIs, cited summary",
    Icon: IconMicroscope,
  },
  {
    id: "decision",
    label: "Decision",
    description: "Term sheet, conditions, AAL",
    Icon: IconGavel,
  },
  {
    id: "trace",
    label: "Trace",
    // Auditor-facing — agent-by-agent lineage with step traces and
    // LLM calls. Distinct from Activity, which is the human-language
    // narrative; Trace is the technical receipt.
    description: "Agent runs + LLM calls",
    Icon: IconRoute,
  },
  {
    id: "assistant",
    label: "Assistant",
    // Staff-facing copilot — search the pipeline, override
    // extractions, advance stages, message the borrower, by typing
    // what you want. Mirrors the borrower-side chat surface on
    // /apply/[id].
    description: "Search, override, advance, message",
    Icon: IconMessageCircle,
  },
];

void IconClipboardList; // reserved for a future "Conditions" sub-phase

/** Outline icon-only button used for prev/next loan navigation in the
 *  header. Renders as disabled (greyed, no hover) when ``href`` is null
 *  so the visual remains stable at the edges of the list. */
function NavArrow({
  href,
  Icon,
  label,
}: {
  href: string | null;
  Icon: React.ComponentType<{ size?: number }>;
  label: string;
}) {
  const base =
    "flex h-7 w-7 items-center justify-center rounded-md border-[0.5px] border-[var(--color-border-tertiary)]";
  if (!href) {
    return (
      <span
        aria-label={label}
        title={label}
        className={`${base} cursor-not-allowed bg-[var(--color-background-primary)] text-[var(--color-text-tertiary)] opacity-50`}
      >
        <Icon size={13} />
      </span>
    );
  }
  return (
    <Link
      href={href}
      aria-label={label}
      title={label}
      className={`${base} bg-[var(--color-background-primary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-background-secondary)] hover:text-[var(--color-text-primary)]`}
    >
      <Icon size={13} />
    </Link>
  );
}

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
          // ``?from=loan:<id>`` lets the party page render a breadcrumb
          // back to the originating case file. Without it the user
          // lands on the inspector with no way to retrace context — the
          // browser's back button works but isn't a UI affordance.
          href={`/parties/${g.id}?from=loan:${loan.id}`}
          title={`${humanizePartyType(g.party_type)} · view profile`}
          className="rounded-full border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2 py-0.5 font-medium text-[var(--color-text-info)] hover:bg-[var(--color-background-secondary)]"
        >
          {g.name}
        </Link>
      ))}
    </div>
  );
}

/**
 * Loading shell that matches the case-file layout: header strip, two
 * tab rows, then content blocks for the docs panel and timeline. We
 * mirror the rendered shape rather than showing a spinner so the page
 * doesn't visibly reflow when the data lands.
 */
function LoanDetailSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <div className="flex flex-col gap-1.5">
          <Skeleton width="w-64" height="h-4" />
          <Skeleton width="w-80" height="h-3" />
        </div>
        <div className="flex gap-1.5">
          <Skeleton width="w-16" height="h-7" />
          <Skeleton width="w-20" height="h-7" />
          <Skeleton width="w-36" height="h-7" />
        </div>
      </div>
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <Skeleton width="w-28" height="h-3" />
        <div className="mt-3 flex flex-col gap-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} width="w-full" height="h-8" />
          ))}
        </div>
      </div>
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <Skeleton width="w-24" height="h-3" />
        <div className="mt-3 flex flex-col gap-3">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="flex items-start gap-3">
              <Skeleton width="w-7" height="h-7" shape="full" />
              <div className="flex-1 space-y-1.5">
                <Skeleton width="w-1/3" height="h-3" />
                <Skeleton width="w-3/4" height="h-3" />
              </div>
            </div>
          ))}
        </div>
      </div>
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

  // Use the cached pipeline list (populated when the user came from the
  // pipeline view) to compute prev/next loan navigation. If the cache
  // is empty (user landed here directly), prev/next render disabled —
  // we don't speculatively fetch the whole list just to power two arrows.
  const loansList = useQuery<Loan[], Error>({
    queryKey: ["loans"],
    queryFn: () => api.listLoans(),
    staleTime: 60_000,
  });
  const { prevLoanId, nextLoanId, positionLabel } = useMemo(() => {
    const all = loansList.data ?? [];
    const idx = all.findIndex((l) => l.id === id);
    if (idx === -1) {
      return { prevLoanId: null, nextLoanId: null, positionLabel: "" };
    }
    return {
      prevLoanId: idx > 0 ? all[idx - 1]!.id : null,
      nextLoanId: idx < all.length - 1 ? all[idx + 1]!.id : null,
      positionLabel: `${idx + 1} of ${all.length}`,
    };
  }, [loansList.data, id]);

  // Keyboard shortcuts. We attach to window once on mount and ignore
  // every event whose target is an input/textarea/select/contenteditable
  // so typing in the composer or the new-loan modal isn't hijacked.
  //
  //   1 / 2 / 3  — switch to Activity / Underwriting / Decision tabs
  //   [          — previous loan in the pipeline order
  //   ]          — next loan
  //   Esc        — back to the pipeline
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (
        t &&
        (t.tagName === "INPUT" ||
          t.tagName === "TEXTAREA" ||
          t.tagName === "SELECT" ||
          t.isContentEditable)
      ) {
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      switch (e.key) {
        case "1":
          setTab("activity");
          break;
        case "2":
          setTab("underwriting");
          break;
        case "3":
          setTab("decision");
          break;
        case "4":
          setTab("trace");
          break;
        case "5":
          setTab("assistant");
          break;
        case "[":
        case "j":
          if (prevLoanId) router.push(`/loans/${prevLoanId}`);
          break;
        case "]":
        case "k":
          if (nextLoanId) router.push(`/loans/${nextLoanId}`);
          break;
        case "Escape":
          // Only escape if no modal/popover is "on top" — they have their
          // own handlers and will preventDefault before we get here.
          router.push("/");
          break;
        default:
          return;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // setTab is closed over but defined every render; harmless and the
    // alternative is converting it to useCallback, which would just
    // shift the dependency churn.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prevLoanId, nextLoanId, router]);

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
      onInterrupt: (payload) => {
        setPendingInterrupt(payload as unknown as IntakeInterrupt);
        toast.message("Intake paused for review", {
          description: "Underwriter approval required for the drafted email.",
        });
      },
      onDone: async (ev) => {
        await invalidateLoan();
        // Pre-flight gate fired — the agent politely declined to run
        // because something is missing. Surface the friendly reason
        // (no underscores, full sentence) instead of a generic toast.
        if (ev.skip_reason) {
          toast.message("Intake didn't run", {
            description: ev.skip_reason,
          });
          setStatusMsg(null);
        } else if (ev.status === "complete") {
          setStatusMsg(null);
          toast.success("Intake complete", {
            description: "Packet is complete — no borrower outreach needed.",
          });
        } else if (ev.status !== "awaiting_approval") {
          // Status reads as a snake_case enum on the wire — humanise it
          // so the toast says "Intake finished — Needs Documents" not
          // "needs_documents".
          toast.warning(`Intake finished — ${titleCase(ev.status)}`);
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
        setStatusMsg(null);
        toast.success("Email sent to borrower", {
          description: subject.slice(0, 80),
        });
      },
    });
  };
  const resumeCancel = async () => {
    await resumeRun.run({
      path: `/loans/${id}/agents/intake/resume`,
      body: { action: "cancel" },
      onDone: async () => {
        await invalidateLoan();
        setStatusMsg(null);
        toast.message("Draft cancelled", {
          description: "No email was sent. The loan stays in intake.",
        });
      },
    });
  };

  const error = loanQuery.error || auditQuery.error;
  const loan = loanQuery.data;
  const events = auditQuery.data ?? [];

  if (error && !loan) {
    return <p className="text-sm text-[var(--color-text-danger)]">Error: {error.message}</p>;
  }
  if (!loan) return <LoanDetailSkeleton />;

  // Owner used to live in the subtitle as "· Jane Doe (owner)" — now
  // it's an interactive control rendered below the header alongside
  // the guarantor chips. Keeping it out of the static subtitle means
  // the badge can carry the reassign affordance without competing with
  // the loan reference + borrower name for visual weight.
  const classLabel = loan.loan_class === "personal" ? "Personal" : "Business";
  const subTitle = `${classLabel} · ${humanizeLoanType(loan.loan_type)} loan · $${Number(loan.amount).toLocaleString()}`;

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
        badge={
          <span className="flex items-center gap-1.5">
            <StagePill stage={loan.stage} />
            <AutonomyToggle loan={loan} />
          </span>
        }
        actions={
          <>
            {/* NAVIGATION group — "where am I, where can I go".
                Visually separated from the action group on the
                right by a thin vertical divider so it's clear that
                Back + Prev/Next don't change the loan, they just
                move the viewer. */}
            <Link
              href="/"
              className="flex items-center gap-1.5 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2.5 py-1.5 text-[12px] text-[var(--color-text-primary)] hover:bg-[var(--color-background-secondary)]"
              title="Back to pipeline (Esc)"
            >
              <IconArrowLeft size={13} />
              Pipeline
            </Link>
            <div className="flex items-center gap-0.5">
              <NavArrow
                href={prevLoanId ? `/loans/${prevLoanId}` : null}
                Icon={IconChevronLeft}
                label="Previous loan ([)"
              />
              <span className="px-1 text-[11px] text-[var(--color-text-tertiary)]">
                {positionLabel}
              </span>
              <NavArrow
                href={nextLoanId ? `/loans/${nextLoanId}` : null}
                Icon={IconChevronRight}
                label="Next loan (])"
              />
            </div>

            {/* Vertical divider — separates "moving around" from
                "changing this loan". Without it the two groups blur
                into a single button row and the user can't tell
                which clicks are reversible (nav) vs. which mutate
                state (run intake, stage transition). */}
            {/* Divider — present any time the actions group will have
                content (i.e. unless we're on the decision stage where
                the StageActions component renders nothing). The check
                mirrors StageActions's null-when-decision behaviour. */}
            {loan.stage !== "decision" && (
              <span
                aria-hidden
                className="mx-1 h-5 w-px"
                style={{ background: "var(--color-border-tertiary)" }}
              />
            )}

            {/* ACTIONS group — "things that change this loan".
                "Extract documents" renders only during intake stage;
                past that point the underwriting/decision tabs own
                their own per-tab actions. The verb is deliberately
                about the artifact (the extracted-fields output)
                rather than "Run intake" — same naming discipline as
                "Generate summary" / "Generate decision draft" below,
                so users never mistake an AI action for the stage-
                transition control sitting right next to it. */}
            {loan.stage === "intake" && (
              <PrimaryButton
                Icon={IconSparkles}
                onClick={startIntake}
                disabled={intakeRun.isRunning || resumeRun.isRunning}
              >
                {intakeRun.isRunning ? "Extracting…" : "Extract documents"}
              </PrimaryButton>
            )}
            {/* Stage advance / decline. Renders nothing for terminal
                stages (servicing, declined) and for `decision` (the
                decision tab's action bar owns that). */}
            <StageActions loanId={id} currentStage={loan.stage} />
          </>
        }
      />

      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <OwnerPicker loan={loan} />
        <GuarantorChips loan={loan} />
      </div>

      {/* Materials lineage flow. Visible from the decision stage
          onward as a four-node graph (Documents → Extractions →
          Rules → Decision) with a cryptographic hash of every input
          that fed the verdict. Stays green when everything matches,
          flips red when drift is detected — same data the old
          single-line banner showed, in a form that explains the
          concept on first sight. */}
      <MaterialsFlow loanId={id} stage={loan.stage} />

      {/* Stage-lock banner. Quieter than the drift banner — it's
          informational ("this loan is finalised") rather than a
          warning. Pairs with the server-side 409s on the agent +
          upload endpoints: same source of truth, just rendered as
          context instead of a denial. */}
      <LoanLockBanner loanId={id} />

      {/* Live progress trail from the SSE stream. Two streams can be
          active in this view: the initial intake run, then a resume
          run after the underwriter approves the draft. Render whichever
          has nodes — they share the same UI affordance. */}
      {(intakeRun.nodes.length > 0 || resumeRun.nodes.length > 0) && (
        <AgentProgress
          title={resumeRun.nodes.length > 0 ? "Sending borrower email" : "Intake agent"}
          nodes={resumeRun.nodes.length > 0 ? resumeRun.nodes : intakeRun.nodes}
          error={resumeRun.error ?? intakeRun.error}
          errorDetail={resumeRun.errorDetail ?? intakeRun.errorDetail}
          skipReason={resumeRun.skipReason ?? intakeRun.skipReason}
        />
      )}

      {statusMsg && (
        <p className="rounded bg-[var(--color-background-secondary)] px-3 py-2 text-xs text-[var(--color-text-secondary)]">
          {statusMsg}
        </p>
      )}

      {/* Master-detail layout.
          Left rail: persistent phase navigation; the loan's "where
          am I" anchor that stays in view as the right pane scrolls.
          Right pane: phase content. The previous top-tab strip
          replaced the case-file shell every time you switched
          phases, so e.g. opening Underwriting hid the materials-
          drift banner that was the whole reason you were looking.
          Two-column layout fixes that: header + banner + agent
          progress are siblings of the grid, so they stay visible
          no matter which phase you're in.

          Responsive: ≥lg keeps the 200px left rail; below that the
          phase nav collapses to a horizontal scroll strip above the
          content. On phones (<sm) we render the nav as compact
          icon-and-label chips so all five phases fit without
          wrapping or scroll. */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[200px_minmax(0,1fr)]">
        <nav
          role="tablist"
          aria-label="Loan phases"
          className="flex h-fit flex-row flex-wrap gap-0.5 rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] p-1.5 lg:flex-col lg:flex-nowrap"
        >
          {TABS.map((t, i) => {
            const isActive = activeTab === t.id;
            const Icon = t.Icon;
            return (
              <button
                key={t.id}
                role="tab"
                aria-selected={isActive}
                onClick={() => setTab(t.id)}
                title={`${t.description} (${i + 1})`}
                // Two layouts behind one button:
                // - <lg: horizontal chip — icon + label, no
                //   description, no kbd. Five fit in a single row on
                //   a 375px viewport.
                // - ≥lg: full vertical rail item with description +
                //   kbd hint.
                className="flex flex-1 items-center gap-1.5 rounded-md px-2 py-1.5 text-left transition-colors lg:flex-none lg:items-start lg:gap-2 lg:px-2.5 lg:py-2"
                style={{
                  background: isActive
                    ? "var(--color-background-secondary)"
                    : "transparent",
                  color: isActive
                    ? "var(--color-text-primary)"
                    : "var(--color-text-secondary)",
                }}
              >
                <span
                  className="inline-flex h-4 w-4 shrink-0 items-center justify-center lg:mt-0.5"
                  style={{
                    color: isActive
                      ? "var(--color-brand)"
                      : "var(--color-text-tertiary)",
                  }}
                >
                  <Icon size={13} />
                </span>
                <span className="flex flex-1 flex-col">
                  <span className="flex items-baseline justify-between gap-1">
                    <span className="text-[12px] font-medium">{t.label}</span>
                    <kbd
                      className="hidden h-3.5 min-w-3.5 items-center justify-center rounded border border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-1 font-mono text-[9.5px] lg:inline-flex"
                      style={{ color: "var(--color-text-tertiary)" }}
                    >
                      {i + 1}
                    </kbd>
                  </span>
                  <span className="mt-0.5 hidden text-[10.5px] leading-tight text-[var(--color-text-tertiary)] lg:inline">
                    {t.description}
                  </span>
                </span>
              </button>
            );
          })}
        </nav>

        <div className="flex flex-col gap-4">
          {activeTab === "activity" && (
            <>
              <DocsPanel loanId={id} />
              <CaseFileTimeline loanId={id} events={events} />
            </>
          )}
          {activeTab === "underwriting" && <UnderwritingWorkspace loanId={id} />}
          {activeTab === "decision" && <CreditDecisionPanel loanId={id} />}
          {activeTab === "trace" && <LoanTrace loanId={id} />}
          {activeTab === "assistant" && <StaffChat loanId={id} />}
        </div>
      </div>

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
