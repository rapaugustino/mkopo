"use client";

/**
 * /help — glossary + scope/limits + how-to-read-dashboards.
 *
 * Renders the same content as ``docs/GLOSSARY.md``, ``docs/SCOPE.md``,
 * and the methodological notes in ``docs/METRICS.md`` — keeping a
 * single visual source of truth so a reader who lands cold can
 * resolve every acronym and know the project's honest scope.
 *
 * Tooltips elsewhere can deep-link in via ``href="/help#air"`` etc.
 * Each glossary term has a stable ``id`` matching the slug the
 * tooltip will use.
 *
 * If you add a new metric or domain term to the UI, add it here too —
 * this is the catalog people search when they don't know what they're
 * looking at.
 */

import { useEffect, useState } from "react";
import {
  IconBook2,
  IconHelp,
  IconMap2,
  IconRuler2,
} from "@tabler/icons-react";
import { BrandHeader } from "@/app/components/BrandHeader";
import { Pill } from "@/app/components/Pill";

type HelpTab = "glossary" | "scope" | "reading";

const TABS: {
  value: HelpTab;
  label: string;
  Icon: React.ComponentType<{ size?: number }>;
}[] = [
  { value: "glossary", label: "Glossary", Icon: IconBook2 },
  { value: "scope", label: "Scope & limits", Icon: IconMap2 },
  { value: "reading", label: "How to read", Icon: IconRuler2 },
];

interface GlossaryTerm {
  /** Slug used as the anchor target. Lowercase, hyphenated. */
  id: string;
  /** Acronym or short form. */
  term: string;
  /** Full expansion. Omit if not an acronym. */
  expansion?: string;
  /** Plain-English explanation, 1–3 sentences. */
  blurb: string;
  /** Optional pointer at the file or UI surface where this appears. */
  where?: string;
}

interface GlossarySection {
  id: string;
  title: string;
  /** Short description rendered under the section heading. */
  intro: string;
  terms: GlossaryTerm[];
}

/* ---------- Glossary content ---------- */

const GLOSSARY: GlossarySection[] = [
  {
    id: "section-lending",
    title: "Lending domain",
    intro:
      "Domain vocabulary that's specific to consumer / commercial credit, the AALs we generate, and the regulatory frame the demo gestures at.",
    terms: [
      {
        id: "uw",
        term: "UW",
        expansion: "Underwriting",
        blurb:
          "The step where a credit-risk decision is built from the loan package. In this codebase it's the underwriting LangGraph agent that consumes extracted facts, runs the rules engine, and emits a summary.",
        where: "api/mkopo/agents/underwriting.py, Underwriting tab on a loan",
      },
      {
        id: "aal",
        term: "AAL",
        expansion: "Adverse Action Letter (a.k.a. Adverse Action Notice / AAN)",
        blurb:
          "Written explanation a lender must send when an application is declined, citing the principal reasons. Required by ECOA (Reg B §1002.9) and FCRA. Drafted by the decision agent here, gated by the AAL fidelity eval.",
        where: "api/mkopo/agents/decision.py, AAL fidelity card on /eval",
      },
      {
        id: "ecoa",
        term: "ECOA",
        expansion: "Equal Credit Opportunity Act (Regulation B)",
        blurb:
          "US federal law prohibiting credit discrimination on protected bases and requiring an adverse-action notice with specific reasons. The AAL fidelity eval checks that generated letters satisfy §1002.9(b).",
      },
      {
        id: "cfpb",
        term: "CFPB",
        expansion: "Consumer Financial Protection Bureau",
        blurb:
          "US regulator for consumer financial products. CFPB Circular 2022-03 (specific reasons in AALs) and 2023-09 (AI-specific guidance) drive the AAL fidelity rubric.",
      },
      {
        id: "fcra",
        term: "FCRA",
        expansion: "Fair Credit Reporting Act",
        blurb:
          "US law governing how credit-report-based adverse actions must be disclosed. Not deeply implemented here — see Scope.",
      },
      {
        id: "hmda",
        term: "HMDA",
        expansion: "Home Mortgage Disclosure Act",
        blurb:
          "US law requiring lenders to report mortgage origination data. The fairness card surfaces approval-rate parity (AIR) — the metric HMDA examiners look at.",
      },
      {
        id: "sr-11-7",
        term: "SR 11-7",
        blurb:
          "Federal Reserve supervisory letter on model risk management — the de facto bank-MRM standard in the US. Requires effective challenge, ongoing monitoring, documentation. Cited in the decision verdict and AAL fidelity evals.",
      },
      {
        id: "ltv",
        term: "LTV",
        expansion: "Loan-to-Value ratio",
        blurb:
          "loan_amount / appraised_value. A core mortgage risk metric — higher LTV means less equity cushion if values fall.",
      },
      {
        id: "dti",
        term: "DTI",
        expansion: "Debt-to-Income ratio",
        blurb:
          "monthly_debts / monthly_income. Consumer credit risk metric — how much of the borrower's income is already committed to debt service.",
      },
      {
        id: "dscr",
        term: "DSCR",
        expansion: "Debt Service Coverage Ratio",
        blurb:
          "net_operating_income / debt_service. Commercial real-estate metric the UW agent computes for income-property loans.",
      },
      {
        id: "noi",
        term: "NOI",
        expansion: "Net Operating Income",
        blurb:
          "Rental income minus operating expenses for an investment property. Input to DSCR.",
      },
      {
        id: "kyc",
        term: "KYC / AML",
        expansion: "Know Your Customer / Anti-Money Laundering",
        blurb:
          "Identity verification and transaction monitoring obligations. The demo does NOT implement these — see Scope.",
      },
    ],
  },
  {
    id: "section-stats",
    title: "Statistical & ML metrics",
    intro:
      "Formulas the eval dashboard renders. Every one of these traces back to a real backend function — see docs/METRICS.md for file:line references.",
    terms: [
      {
        id: "psi",
        term: "PSI",
        expansion: "Population Stability Index",
        blurb:
          "Distribution-shift metric: Σ (p − q) · ln(p / q) where p is the current share per bin and q is the reference. Bands (Siddiqi 2017): <0.10 stable, 0.10–0.25 minor shift, ≥0.25 major shift.",
        where: "PSI card on /eval, api/mkopo/services/psi.py",
      },
      {
        id: "air",
        term: "AIR",
        expansion: "Adverse Impact Ratio",
        blurb:
          "The four-fifths rule (EEOC 1978): min group approval rate ÷ max group approval rate. Below 0.80 historically triggers regulator scrutiny. In this demo the protected-class signal is SYNTHETIC — see the card's own disclosure.",
        where: "Fairness card on /eval, api/mkopo/services/fairness.py",
      },
      {
        id: "ece",
        term: "ECE",
        expansion: "Expected Calibration Error",
        blurb:
          "Weighted gap between predicted confidence and empirical accuracy per bin (Naeini et al. 2015, Guo et al. 2017). Lower is better — <0.05 well calibrated, 0.05–0.10 moderate, ≥0.10 poor.",
        where: "Calibration card on /eval, api/mkopo/services/calibration.py",
      },
      {
        id: "brier",
        term: "Brier score",
        blurb:
          "Strictly proper scoring rule (Brier 1950): mean squared error between predicted probability and outcome. Used alongside ECE because ECE is bin-dependent.",
      },
      {
        id: "mmd",
        term: "MMD",
        expansion: "Maximum Mean Discrepancy",
        blurb:
          "Distribution-shift test for high-dimensional data (Gretton et al. 2012). We compute MMD² between current and reference system-prompt embeddings to detect prompt-template drift. Computation lives in the backend; no UI card yet.",
        where: "api/mkopo/services/prompt_drift.py",
      },
      {
        id: "f1",
        term: "F1 / precision / recall",
        blurb:
          "Precision = TP/(TP+FP). Recall = TP/(TP+FN). F1 = 2PR/(P+R), the harmonic mean. Macro-F1 = unweighted mean across classes — treats classes equally regardless of support.",
        where: "Decision verdict card on /eval",
      },
      {
        id: "confusion-matrix",
        term: "Confusion matrix",
        blurb:
          "Per-class TP/FP/TN/FN table. We render it for the decision agent (approve / decline / refer-to-human) to expose per-class failure modes.",
      },
      {
        id: "refusal-z",
        term: "Refusal-rate spike test",
        blurb:
          "Binomial proportion test: z = (p_cur − p_base) / sqrt(p_base · (1 − p_base) / n_cur). |z| ≥ 2 flagged as spike. Current window = 7d, baseline = 28d ending 7d ago (gap prevents overlap).",
        where: "Refusal card on /eval, api/mkopo/services/refusal.py",
      },
      {
        id: "rag",
        term: "RAG",
        expansion: "Retrieval-Augmented Generation",
        blurb:
          "Generation pattern that retrieves text chunks from a corpus and includes them in the LLM context. Used here for 'Ask the file' inside the UW workspace.",
      },
      {
        id: "ragas",
        term: "RAGAS",
        blurb:
          "RAG Assessment framework (Es et al. 2024, arXiv:2309.15217). We borrow its faithfulness metric (fraction of claims grounded in retrieved context) for the UW groundedness eval — adapted to work directly against source docs.",
        where: "api/evals/tasks/uw_groundedness.py",
      },
      {
        id: "llm-judge",
        term: "LLM-as-judge",
        blurb:
          "Pattern of using an LLM to score outputs of another LLM. We use it for UW groundedness, prompt-injection detection, the constitutional judge, and 'rewrite with AI'. Mitigations: judge model is pinned, judge prompt is versioned.",
      },
      {
        id: "judge-accuracy",
        term: "Judge accuracy",
        blurb:
          "For evals where the LLM is the scorer, we report HOW OFTEN the judge classified into the expected band — not the raw faithfulness score. Honesty: we don't claim the underlying probability is perfectly calibrated, only that the classification is reliable.",
      },
      {
        id: "embedding",
        term: "Embedding",
        blurb:
          "Dense numeric vector representing the meaning of a piece of text. We use OpenAI text-embedding-3-small for document chunks and text-embedding-3-large for the prompt-drift monitor.",
      },
      {
        id: "p95",
        term: "p50 / p95 / p99 latency",
        blurb:
          "50th / 95th / 99th percentile of a latency distribution. p95 is the standard tail metric for user-facing latency. NOTE: two implementations live here — nearest-rank in routers/evals.py and linear-interpolation in services/agent_economics.py. Numbers can differ on small n.",
      },
    ],
  },
  {
    id: "section-safety",
    title: "AI safety & governance",
    intro:
      "Vocabulary around how the agent stays inside the lines: input-layer detection, output judges, stage machinery, governance frameworks.",
    terms: [
      {
        id: "nist-rmf",
        term: "NIST AI RMF",
        expansion: "AI Risk Management Framework",
        blurb:
          "Voluntary US framework (NIST AI 100-1, 2023) for AI risk governance. Organised around Govern, Map, Measure, Manage.",
      },
      {
        id: "nist-600-1",
        term: "NIST AI 600-1",
        expansion: "Generative AI Profile",
        blurb:
          "NIST AI 600-1 (2024) tailors the RMF to GenAI. Defines twelve risk categories. We map our eval cards onto five via the NISTBadge widget: Confabulation, Harmful Bias, Information Security, Information Integrity, Value Chain.",
        where: "web/app/eval/cards/NISTBadge.tsx",
      },
      {
        id: "tevv",
        term: "TEVV",
        expansion: "Test, Evaluation, Validation, Verification",
        blurb:
          "NIST's umbrella term for what the /eval dashboard does.",
      },
      {
        id: "hitl",
        term: "HITL",
        expansion: "Human-In-The-Loop",
        blurb:
          "Workflow pattern where the agent pauses for human review at a decision boundary. Implemented as a LangGraph interrupt for extraction approval. HITL pauses are visible as 'interrupted' in the agent reliability rollup.",
      },
      {
        id: "prompt-injection",
        term: "Prompt injection",
        blurb:
          "Class of attack where untrusted input (a document, chat message, URL) contains instructions that try to override the agent's system prompt. The input-layer detector catches the common patterns (instruction override, role play, exfiltration).",
        where: "api/mkopo/safety/injection.py, /safety page",
      },
      {
        id: "constitutional-judge",
        term: "Constitutional judge",
        blurb:
          "Per-decision LLM-as-judge that scores whether the agent's output satisfies a set of principles (no PII leakage, no contradicting rule engine, etc.). Failure holds the decision for human review.",
        where: "api/mkopo/safety/constitutional.py",
      },
      {
        id: "fail-closed",
        term: "Fail-closed",
        blurb:
          "When in doubt, deny. The injection detector and the stage machine are both fail-closed: an unrecognised input or invalid transition is blocked rather than passed through.",
      },
      {
        id: "red-teaming",
        term: "Red teaming",
        blurb:
          "Structured adversarial testing. The adversarial_injection eval is a starter red-team suite. A production red team is out of scope — see Scope.",
      },
      {
        id: "stage-machine",
        term: "Stage machine",
        blurb:
          "The state graph that controls legal loan stage transitions. Encoded as a directed graph; transitions outside it raise StageMachineError.",
        where: "api/mkopo/services/stages.py",
      },
      {
        id: "stage-lock",
        term: "Stage lock",
        blurb:
          "Per-stage rule about which agents and document mutations are permitted in each stage. Encoded as data.",
        where: "api/mkopo/services/stage_locks.py",
      },
    ],
  },
  {
    id: "section-tech",
    title: "Technical / platform",
    intro:
      "Plumbing terms that show up in code comments and observability rows.",
    terms: [
      {
        id: "sse",
        term: "SSE",
        expansion: "Server-Sent Events",
        blurb:
          "HTTP streaming protocol used to push agent-progress updates from the FastAPI backend to the browser as they happen.",
      },
      {
        id: "rbac",
        term: "RBAC",
        expansion: "Role-Based Access Control",
        blurb:
          "Tool catalog is filtered by user role (staff vs borrower) so e.g. a borrower can't invoke update_loan_field on someone else's loan.",
      },
      {
        id: "jwt",
        term: "JWT",
        expansion: "JSON Web Token",
        blurb:
          "Stateless auth token format. We use it with server-side revocation (jti denylist) so logouts and password changes invalidate live tokens immediately.",
      },
      {
        id: "magic-link",
        term: "Magic link",
        blurb:
          "Single-use sign-in link sent to the user's email. Borrower auth alternative to password.",
      },
      {
        id: "orm",
        term: "ORM",
        expansion: "Object-Relational Mapping",
        blurb:
          "SQLAlchemy 2.x Mapped[...] style classes that mirror Postgres tables.",
      },
      {
        id: "migration",
        term: "Migration",
        blurb:
          "Versioned schema-change script. We use Alembic; migrations live in api/alembic/versions/.",
      },
      {
        id: "llm-gateway",
        term: "LLM gateway",
        blurb:
          "Single chokepoint (api/mkopo/llm/gateway.py) through which every LLM call flows. Writes to llm_calls and agent_steps for observability + cost accounting.",
      },
      {
        id: "thread-id",
        term: "thread_id",
        blurb:
          "Identifier propagated via Python ContextVar that ties every LLM call inside one agent run together. Used in the trace tree and the per-agent economics join.",
      },
      {
        id: "parent-step-id",
        term: "parent_step_id",
        blurb:
          "Foreign key on llm_calls that builds the trace tree: a call's parent is the agent step it was made from.",
      },
      {
        id: "materials-hash",
        term: "Materials hash",
        blurb:
          "SHA-256 of the inputs that fed a decision (loan snapshot + rule results + extraction). Stored alongside the verdict so we can detect post-hoc tampering or drift in decision inputs.",
      },
      {
        id: "golden",
        term: "Golden set / golden fixture",
        blurb:
          "YAML file under api/evals/golden_sets/ that defines a labelled input-output pair for an eval task. Frozen ground truth.",
      },
      {
        id: "drift-monitor",
        term: "Drift monitor",
        blurb:
          "Scheduled job that pulls recent extractions out of the review queue and writes per-field 'production' accuracy task_runs.",
        where: "api/mkopo/services/drift.py",
      },
      {
        id: "task-runs",
        term: "task_runs / llm_calls / agent_steps",
        blurb:
          "Three Postgres tables. llm_calls = every LLM call (tokens, cost, latency, schema). agent_steps = every LangGraph node execution (parent of LLM calls). task_runs = every eval task result (golden or production).",
      },
    ],
  },
];

/* ---------- Scope/Limits content ---------- */

interface ScopeBlock {
  title: string;
  /** Each entry is a single bullet point — keep them tight. */
  items: { label: string; body?: string }[];
  variant: "ok" | "limit";
}

const SCOPE_DEMONSTRATES: ScopeBlock = {
  title: "What this project demonstrates",
  variant: "ok",
  items: [
    {
      label: "Agentic workflow",
      body: "Multi-agent LangGraph pipelines (intake → underwriting → decision) with stage transitions, HITL interrupts, pre-flight gates, tool-using chat agents with role-based tool filtering, and a stage machine + stage locks encoded as data.",
    },
    {
      label: "Evaluation harness",
      body: "13 eval tasks (golden + production) covering extraction, decisions, AAL fidelity, intake email, adversarial injection, UW groundedness, tool-call accuracy. Every number on the dashboard is computed by a real backend function — see docs/METRICS.md.",
    },
    {
      label: "Observability",
      body: "Every LLM call persisted with tokens/cost/latency/schema. Every LangGraph node persisted with parent/child links. thread_id propagated via ContextVar so calls inside a run are joinable. Trace tree on the loan detail page.",
    },
    {
      label: "Production monitors",
      body: "Drift, calibration (ECE/Brier), fairness (AIR/four-fifths), PSI, refusal-rate z-test, per-agent economics, embedding-distribution drift (MMD). All real implementations citing the textbook source.",
    },
  ],
};

const SCOPE_LIMITS: ScopeBlock = {
  title: "What this project does NOT cover",
  variant: "limit",
  items: [
    {
      label: "No regulatory certification",
      body: "AAL fidelity checks the STRUCTURE CFPB Circular 2022-03 demands — not legal compliance. A regulatory attorney must review before any AAL is sent to a real applicant.",
    },
    {
      label: "Synthetic protected-class signal",
      body: "AIR / four-fifths math is real; the grouping signal is a SHA-256 bucketing of loan_id. The card discloses this. Production needs real attribute capture, which has its own collection / storage compliance.",
    },
    {
      label: "No KYC / AML / FCRA workflow",
      body: "No identity verification, sanctions screening, BSA monitoring, credit-bureau adverse-action workflow. Real lenders bring a CIP pipeline.",
    },
    {
      label: "No infrastructure-as-code",
      body: "Runs on local docker-compose. Production needs Terraform/Pulumi, secrets management, CD pipelines, multi-tenancy, data-residency controls, granular RBAC matrix.",
    },
    {
      label: "No real data integrations",
      body: "No FICO/Experian credit pulls, no Plaid/Finicity, no MLS, no Textract/Document AI OCR, no DocuSign. Seed data is fabricated.",
    },
    {
      label: "No production-grade red team",
      body: "The adversarial_injection eval is a starter pattern catalog (~20 patterns). A real red team — paid researchers, jailbreak elicitation — is out of scope.",
    },
    {
      label: "No model card",
      body: "We pin the model identifier in the gateway but don't ship a NIST-style model card with capabilities / limitations / eval history.",
    },
    {
      label: "MMD without UI",
      body: "Embedding-distribution drift is computed in services/prompt_drift.py but has no card. Results go to task_runs with prefix prompt_drift. — visible via API, not via the dashboard.",
    },
    {
      label: "Two p95 implementations",
      body: "routers/evals.py uses nearest-rank; services/agent_economics.py uses linear-interpolation. Numbers can differ on small n. Both are valid p95 conventions.",
    },
    {
      label: "Scenarios catalog 'verified by' is static",
      body: "Each card claims it's verified by a pytest, but the manifest is hard-coded. A test failure does NOT automatically flip the card. Aspirational claim.",
    },
  ],
};

/* ---------- "How to read" content ---------- */

const READING_STEPS: { n: number; title: string; body: string }[] = [
  {
    n: 1,
    title: "What does the label claim?",
    body: "Hover the tooltip. Every metric label has one. The tooltip names the failure mode the metric catches.",
  },
  {
    n: 2,
    title: "What's the backend function?",
    body: "Open docs/METRICS.md and find the entry — it points at file:function so you can read the computation in one click.",
  },
  {
    n: 3,
    title: "What's the data source?",
    body: "Almost always a Postgres table: task_runs, llm_calls, agent_steps, agent_runs. Inspect rows directly via psql.",
  },
  {
    n: 4,
    title: "What window?",
    body: "Production cards mostly use 7-day current vs 28-day baseline. LLM tiles cascade 24h → 7d → all-time depending on data availability — the tile shows which.",
  },
  {
    n: 5,
    title: "What threshold or band?",
    body: "Documented in the card source and in docs/METRICS.md. PSI 0.10 / 0.25, AIR 0.85 / 0.80, ECE 0.05 / 0.10, etc.",
  },
];

const COMMON_CONFUSIONS: { title: string; body: string }[] = [
  {
    title: "Number looks wrong on a fresh DB",
    body: "Almost always a data-completeness issue: the cron sweeps haven't run (drift at 3 AM UTC, golden at 4 AM UTC) or the production monitor's data floor isn't met (PSI needs n≥30, refusal needs n≥20). Run scripts/run_all_monitors.py manually to populate.",
  },
  {
    title: "Headline accuracy is averaging something it shouldn't",
    body: "Add the new task's name prefix to _DERIVED_METRIC_PREFIXES in routers/evals.py. The denylist is curated by hand — economics / fairness / PSI etc. are excluded because they're not accuracy-shaped.",
  },
  {
    title: "Two metrics with similar names",
    body: "'Confidence calibration' on the production-drift row (ECE + Brier) is DIFFERENT from 'Confidence calibration' in the diagnostics row (band-conditional accept rate). Both legitimately calibration, different formulas.",
  },
  {
    title: "Golden ≠ production almost always",
    body: "Real-world inputs are out-of-distribution vs the labelled set, and staff overrides include 'right answer, wrong format' cases. A divergence ≥ 0.03 trips the drift banner; the field row gains an Investigate link.",
  },
];

/* ---------- Components ---------- */

function TabBar({
  active,
  onChange,
}: {
  active: HelpTab;
  onChange: (t: HelpTab) => void;
}) {
  return (
    <div
      className="flex items-center gap-0.5 border-b-[0.5px]"
      style={{ borderColor: "var(--color-border-tertiary)" }}
    >
      {TABS.map((t) => {
        const isActive = active === t.value;
        const Icon = t.Icon;
        return (
          <button
            key={t.value}
            onClick={() => onChange(t.value)}
            className={
              "relative flex items-center gap-1.5 px-3 py-2 text-[12px] font-medium transition-colors " +
              (isActive
                ? "text-[var(--color-text-primary)]"
                : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]")
            }
          >
            <Icon size={13} />
            {t.label}
            {isActive && (
              <span
                className="absolute bottom-[-0.5px] left-0 right-0 h-[2px]"
                style={{ background: "var(--color-brand)" }}
              />
            )}
          </button>
        );
      })}
    </div>
  );
}

function GlossaryCard({ term }: { term: GlossaryTerm }) {
  return (
    <article
      id={term.id}
      className="scroll-mt-24 rounded-lg border-[0.5px] p-3.5"
      style={{
        borderColor: "var(--color-border-tertiary)",
        background: "var(--color-background-primary)",
      }}
    >
      <header className="mb-1.5 flex items-baseline gap-2">
        <h3 className="text-[13px] font-semibold text-[var(--color-text-primary)]">
          {term.term}
        </h3>
        {term.expansion && (
          <span className="text-[11px] italic text-[var(--color-text-tertiary)]">
            {term.expansion}
          </span>
        )}
      </header>
      <p className="text-[12.5px] leading-relaxed text-[var(--color-text-primary)]">
        {term.blurb}
      </p>
      {term.where && (
        <p className="mt-1.5 text-[10.5px] text-[var(--color-text-tertiary)]">
          <span className="uppercase tracking-[0.04em]">Where</span>:{" "}
          <code className="text-[var(--color-text-secondary)]">
            {term.where}
          </code>
        </p>
      )}
    </article>
  );
}

function GlossaryPanel({ query }: { query: string }) {
  const q = query.trim().toLowerCase();
  const sections = q
    ? GLOSSARY.map((s) => ({
        ...s,
        terms: s.terms.filter(
          (t) =>
            t.term.toLowerCase().includes(q) ||
            (t.expansion?.toLowerCase().includes(q) ?? false) ||
            t.blurb.toLowerCase().includes(q),
        ),
      })).filter((s) => s.terms.length > 0)
    : GLOSSARY;

  if (sections.length === 0) {
    return (
      <p className="rounded-lg border-[0.5px] border-dashed p-6 text-center text-[12px] text-[var(--color-text-tertiary)]"
         style={{ borderColor: "var(--color-border-tertiary)" }}>
        No matching terms for &ldquo;{query}&rdquo;.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      {sections.map((s) => (
        <section key={s.id} id={s.id} className="scroll-mt-24">
          <header className="mb-2">
            <h2 className="text-[14px] font-semibold text-[var(--color-text-primary)]">
              {s.title}
            </h2>
            <p className="mt-0.5 text-[12px] text-[var(--color-text-tertiary)]">
              {s.intro}
            </p>
          </header>
          <div className="grid grid-cols-1 gap-2.5 lg:grid-cols-2">
            {s.terms.map((t) => (
              <GlossaryCard key={t.id} term={t} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function ScopeBlockView({ block }: { block: ScopeBlock }) {
  const palette =
    block.variant === "ok"
      ? {
          border: "var(--color-border-tertiary)",
          background: "var(--color-background-primary)",
          accent: "var(--color-brand)",
        }
      : {
          border: "var(--color-border-tertiary)",
          background: "var(--color-background-secondary)",
          accent: "var(--color-accent)",
        };
  return (
    <section
      className="rounded-lg border-[0.5px] p-4"
      style={{
        borderColor: palette.border,
        background: palette.background,
        borderLeft: `2px solid ${palette.accent}`,
      }}
    >
      <h2 className="mb-3 text-[14px] font-semibold text-[var(--color-text-primary)]">
        {block.title}
      </h2>
      <ul className="flex flex-col gap-2.5">
        {block.items.map((item) => (
          <li key={item.label} className="text-[12.5px] leading-relaxed">
            <span className="font-medium text-[var(--color-text-primary)]">
              {item.label}
            </span>
            {item.body && (
              <>
                {" — "}
                <span className="text-[var(--color-text-secondary)]">
                  {item.body}
                </span>
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function ScopePanel() {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-[12.5px] leading-relaxed text-[var(--color-text-secondary)]">
        This is a demo of agentic-workflow patterns for loan
        origination — an interview-defensibility project, not a
        production lending system. The structure below is the honest
        map of what that means: what we built well, what we left
        out, where you should be skeptical of a number.
      </p>
      <ScopeBlockView block={SCOPE_DEMONSTRATES} />
      <ScopeBlockView block={SCOPE_LIMITS} />
      <p className="text-[11.5px] italic text-[var(--color-text-tertiary)]">
        The full markdown source — including file paths and method
        references — lives at <code>docs/SCOPE.md</code> in the
        repo.
      </p>
    </div>
  );
}

function ReadingPanel() {
  return (
    <div className="flex flex-col gap-4">
      <section>
        <h2 className="mb-2 text-[14px] font-semibold">
          Defending a number in five steps
        </h2>
        <p className="mb-3 text-[12px] text-[var(--color-text-tertiary)]">
          When you're asked to defend something on the dashboard, walk
          through these in order — almost every confusion resolves by
          step 4.
        </p>
        <ol className="flex flex-col gap-2.5">
          {READING_STEPS.map((s) => (
            <li
              key={s.n}
              className="flex gap-3 rounded-lg border-[0.5px] p-3"
              style={{
                borderColor: "var(--color-border-tertiary)",
                background: "var(--color-background-primary)",
              }}
            >
              <span
                className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[11px] font-semibold"
                style={{
                  background: "var(--color-brand)",
                  color: "var(--color-brand-light)",
                }}
              >
                {s.n}
              </span>
              <div>
                <h3 className="text-[12.5px] font-semibold">
                  {s.title}
                </h3>
                <p className="mt-0.5 text-[12px] leading-relaxed text-[var(--color-text-secondary)]">
                  {s.body}
                </p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section>
        <h2 className="mb-2 text-[14px] font-semibold">
          Common confusions
        </h2>
        <div className="grid grid-cols-1 gap-2.5 lg:grid-cols-2">
          {COMMON_CONFUSIONS.map((c) => (
            <article
              key={c.title}
              className="rounded-lg border-[0.5px] p-3"
              style={{
                borderColor: "var(--color-border-tertiary)",
                background: "var(--color-background-primary)",
              }}
            >
              <h3 className="text-[12.5px] font-semibold">{c.title}</h3>
              <p className="mt-1 text-[12px] leading-relaxed text-[var(--color-text-secondary)]">
                {c.body}
              </p>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}

/* ---------- Page ---------- */

/** Pick the initial tab from the URL hash so deep-links like
 *  `/help#air` land on the glossary already-selected. SSR-safe: on
 *  the server ``window`` is undefined and we default to glossary;
 *  on first client render the same default holds until hydration
 *  paints, then the useEffect-driven scroll kicks in. */
function initialTabFromHash(): HelpTab {
  if (typeof window === "undefined") return "glossary";
  const hash = window.location.hash.slice(1);
  if (!hash) return "glossary";
  return hash.startsWith("scope-") ? "scope" : "glossary";
}

export default function HelpPage() {
  // Lazy state initialiser — runs once at mount, not on every
  // render, and crucially NOT inside a useEffect (the linter rule
  // we're avoiding here). Same end result: the right tab is
  // selected before the panel mounts so the scroll-into-view below
  // finds its target.
  const [tab, setTab] = useState<HelpTab>(initialTabFromHash);
  const [query, setQuery] = useState("");

  // Scroll the deep-linked anchor into view AFTER the panel paints.
  // queueMicrotask gives React a tick to mount the children before
  // we ask for ``getElementById`` — the alternative (calling sync)
  // returns null because the glossary panel hasn't rendered yet.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const hash = window.location.hash.slice(1);
    if (!hash) return;
    queueMicrotask(() => {
      const el = document.getElementById(hash);
      el?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, []);

  const totalTerms = GLOSSARY.reduce((s, sec) => s + sec.terms.length, 0);

  return (
    <div className="flex flex-col gap-3">
      <BrandHeader
        leading={<IconHelp size={26} />}
        title="Help"
        sub="Acronyms, scope and limits, and how to defend a number on the dashboard. Tooltips elsewhere link here."
        actions={
          tab === "glossary" ? (
            <div className="flex items-center gap-2">
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={`Search ${totalTerms} terms…`}
                className="form-input-compact w-[180px]"
                aria-label="Search glossary"
              />
              <Pill variant="neutral">{totalTerms} terms</Pill>
            </div>
          ) : null
        }
      />
      <TabBar active={tab} onChange={setTab} />
      {tab === "glossary" && <GlossaryPanel query={query} />}
      {tab === "scope" && <ScopePanel />}
      {tab === "reading" && <ReadingPanel />}
    </div>
  );
}
