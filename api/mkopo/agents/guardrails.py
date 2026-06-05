"""LLM-as-judge guardrail with bounded self-correction loop.

Three patterns from the literature, combined:

1. **Constitutional AI** (Bai et al. 2022) — outputs are evaluated
   against an explicit list of principles encoded in a constitution
   rather than against vague "be safe" instructions. The
   constitution is the single source of truth a reviewer can audit.

2. **LLM-as-Judge** (Zheng et al. 2023) — a *separate* LLM call,
   ideally on a different model + with a different prompt, judges
   whether the drafter's output adheres to the constitution. Mixing
   judge model ≠ drafter model reduces the chance both make the
   same error.

3. **Self-Refine** (Madaan et al. 2023) — on judge failure, the
   drafter is invoked again with the judge's critique appended to
   the prompt. Bounded retries (here: 2) so a stubborn failure
   doesn't loop forever.

The module is genuinely graph-agnostic. Three pieces compose any
Self-Refine loop:

- :func:`judge_against_constitution` — the core judgment call any
  node can make.
- :func:`make_validator_node` — builds a LangGraph node that runs
  one or more judgments against state and writes the worst verdict
  back. Used by intake, underwriting, and decision agents.
- :func:`make_validator_router` — builds the conditional-edge
  routing function that loops back to the drafter on block (bounded
  by :data:`MAX_VALIDATION_ATTEMPTS`) or proceeds to the next node.

The judge always goes through the same :class:`LLMGateway` the
drafter uses, so every judgment writes an ``llm_calls`` row + lands
in the observability page. The judgment itself is stored in
``agent_runs.payload.guardrail_judgment`` for audit lookup.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field

from mkopo.config import get_settings
from mkopo.llm_gateway import get_gateway

logger = structlog.get_logger()


@dataclass(frozen=True)
class Constitution:
    """The explicit principles an output must satisfy.

    Three buckets so the judge can issue graduated verdicts:

    - ``principles``: aspirations the output should meet. Failing one
      is a ``warn`` (audit, persist anyway) unless multiple fail.
    - ``red_lines``: hard violations (e.g. "letter contains a
      bracketed placeholder", "AAL omits ECOA notice"). Any single
      red-line failure is a ``block`` — the output gets sent back
      to the drafter with the critique.
    - ``forbidden_substrings``: cheap pre-judge fast-fail. Matched
      via plain string containment before any LLM call so an
      obvious failure doesn't pay for the round trip.

    ``name`` lands on the audit event so different constitutions
    are distinguishable on the observability page (e.g.
    "decision.aal_v1" vs "decision.term_sheet_v1").
    """

    name: str
    principles: tuple[str, ...]
    red_lines: tuple[str, ...]
    forbidden_substrings: tuple[str, ...] = ()


class _Judgment(BaseModel):
    """LLM judge's structured verdict.

    The ``severity`` field is the routing signal — the decision
    agent reads it to decide between persist (``ok``), persist-with-
    audit (``warn``), or loop-back (``block``). ``failed_*`` lists
    surface the specific principles that failed so the next drafter
    iteration can target the fix. ``critique`` is plain English
    suitable for the audit event AND for inclusion in the revised
    prompt.
    """

    severity: Literal["ok", "warn", "block"] = Field(
        description=(
            "'ok' = adheres to every principle; 'warn' = a single "
            "principle failed but no red-line crossed; 'block' = at "
            "least one red-line crossed OR multiple principles "
            "failed badly enough to warrant a re-draft."
        )
    )
    failed_principles: list[str] = Field(
        default_factory=list,
        description=(
            "Exact strings from the constitution's principles list "
            "that the output failed to satisfy. Empty when severity "
            "is 'ok'."
        ),
    )
    failed_red_lines: list[str] = Field(
        default_factory=list,
        description=(
            "Exact strings from the constitution's red_lines list that the output crossed."
        ),
    )
    critique: str = Field(
        max_length=600,
        description=(
            "One-paragraph explanation of what went wrong + what "
            "the drafter should change. Plain English, no jargon. "
            "Concrete enough that re-feeding it to the drafter "
            "produces a meaningfully different output."
        ),
    )


@dataclass(frozen=True)
class JudgmentResult:
    """Adapter shape returned from :func:`judge_against_constitution`.

    Wraps the Pydantic ``_Judgment`` + a ``passed`` boolean for
    convenient ``if not result.passed`` checks. The wrapping
    dataclass survives session-boundary moves safely.
    """

    passed: bool
    severity: Literal["ok", "warn", "block"]
    failed_principles: list[str]
    failed_red_lines: list[str]
    critique: str

    @classmethod
    def from_judgment(cls, j: _Judgment) -> JudgmentResult:
        return cls(
            passed=j.severity == "ok",
            severity=j.severity,
            failed_principles=list(j.failed_principles),
            failed_red_lines=list(j.failed_red_lines),
            critique=j.critique,
        )

    @classmethod
    def auto_block(cls, reason: str) -> JudgmentResult:
        """Synthesised ``block`` verdict — used by the
        ``forbidden_substrings`` fast-fail path so callers always get
        the same shape regardless of whether the LLM was consulted."""
        return cls(
            passed=False,
            severity="block",
            failed_principles=[],
            failed_red_lines=[reason],
            critique=f"Pre-judge fast-fail: {reason}",
        )

    def to_audit_payload(self) -> dict[str, object]:
        """Compact form for ``agent_runs.payload.guardrail_judgment``.
        Keeps the audit small but searchable — the observability page
        renders failed principles as chips, the critique as the
        long-form rationale."""
        return {
            "severity": self.severity,
            "failed_principles": self.failed_principles,
            "failed_red_lines": self.failed_red_lines,
            "critique": self.critique,
        }


# System prompt template for the judge. Deliberately model-agnostic
# (no Claude-specific phrasing) so swapping providers in
# ``llm_gateway`` doesn't require prompt rewrites. The judge is
# instructed to be strict — false negatives (letting a bad output
# through) are worse than false positives (forcing a re-draft).
_JUDGE_SYSTEM = (
    "You are a CONSTITUTIONAL JUDGE evaluating whether a piece of "
    "LLM-generated content adheres to an explicit set of principles.\n\n"
    "Your job is NOT to evaluate the *quality* of the content. Your "
    "job is to check whether each principle in the supplied "
    "constitution is satisfied.\n\n"
    "Rules:\n"
    "- Read the constitution carefully. Each principle is a yes/no "
    "check.\n"
    "- For each principle that the output FAILS, list the exact "
    "principle string in ``failed_principles``.\n"
    "- For each red-line that the output CROSSES, list the exact "
    "red-line string in ``failed_red_lines``.\n"
    "- Choose severity:\n"
    "  - 'ok' if every principle is satisfied AND no red-line is "
    "crossed.\n"
    "  - 'block' if at least one red-line is crossed OR more than "
    "two principles fail.\n"
    "  - 'warn' otherwise (one or two principles failed but no "
    "red-line).\n"
    "- The ``critique`` field is a single short paragraph aimed at "
    "the drafter — it should describe what to change, concretely, "
    "so the next draft can address it. Do not restate the "
    "constitution; describe the specific failure.\n"
    "- Be strict. If you are uncertain whether a principle is "
    "satisfied, lean toward 'warn'.\n"
)


async def judge_against_constitution(
    *,
    output_text: str,
    constitution: Constitution,
    context: str = "",
) -> JudgmentResult:
    """Run the judge against ``output_text``.

    ``context`` is appended to the user message so the judge has
    enough surrounding information to evaluate principles like
    "must address the borrower by name" (which require knowing the
    borrower's name).

    The forbidden-substring check fires first as a fast-fail so
    obvious violations don't pay for an LLM round-trip. If any
    forbidden substring is present, the function short-circuits
    with a synthetic block verdict citing the matched substring.
    """
    # Fast-fail check first.
    lowered = output_text.lower()
    for forbidden in constitution.forbidden_substrings:
        if forbidden.lower() in lowered:
            logger.info(
                "guardrail_fast_fail",
                constitution=constitution.name,
                forbidden=forbidden,
            )
            return JudgmentResult.auto_block(f"output contains forbidden substring: {forbidden!r}")

    settings = get_settings()
    gateway = get_gateway()

    principles_block = "\n".join(f"- {p}" for p in constitution.principles)
    red_lines_block = (
        "\n".join(f"- {r}" for r in constitution.red_lines)
        if constitution.red_lines
        else "(none defined)"
    )

    user = (
        f"Constitution: {constitution.name}\n\n"
        f"Principles (must all be satisfied):\n{principles_block}\n\n"
        f"Red lines (any single failure → block):\n{red_lines_block}\n\n"
        f"Context (for principles that require it):\n{context or '(no extra context)'}\n\n"
        f"Output to evaluate:\n---\n{output_text}\n---"
    )

    judgment: _Judgment = await gateway.call_structured(
        # Use the lighter / faster model for the judge — it's a
        # rubric-checking task, not a deep reasoning one, and the
        # judge runs on every drafter call so cost matters.
        # Different model than the drafter (which uses the heavy
        # model for decision/AAL drafting) so a shared error mode
        # is less likely.
        model=settings.llm_default_model,
        system=_JUDGE_SYSTEM,
        user=user,
        schema=_Judgment,
    )

    result = JudgmentResult.from_judgment(judgment)
    logger.info(
        "guardrail_judgment",
        constitution=constitution.name,
        severity=result.severity,
        n_failed_principles=len(result.failed_principles),
        n_failed_red_lines=len(result.failed_red_lines),
    )
    return result


# ---------------------------------------------------------------------------
# Constitutions — one per drafted artifact. Add new ones here when
# adding a new drafter. The constitution is the public contract for
# what "valid output" means; the drafter prompt can drift, but the
# constitution is what the judge checks against.
# ---------------------------------------------------------------------------


# Constitution-string literals are long by design — the judge LLM
# evaluates the exact text, so wrapping them onto multiple lines
# would change the strings the model sees and could be read as
# different principles. The per-line lint escape below is a
# deliberate Python idiom for prose-string constants.
# fmt: off
DECISION_VERDICT_CONSTITUTION = Constitution(
    name="decision.verdict_v1",
    principles=(
        "The verdict text refers to the applicant by name as supplied in the context.",  # noqa: E501
        "The rationale field cites at least one of the rule outcomes from the supplied context, by name.",  # noqa: E501
        "The rationale does not promise specific approval terms (rate, fees, dates) — those belong in the term sheet only.",  # noqa: E501
        "The rationale is grounded in the supplied rule outcomes and does not introduce facts not present in the context.",  # noqa: E501
        "The confidence score is consistent with the rationale (≥0.8 only if rationale describes a clean, unambiguous case).",  # noqa: E501
    ),
    red_lines=(
        "The output contains bracketed placeholders like [APPLICANT NAME], [DATE], [LOAN AMOUNT].",  # noqa: E501
        "The verdict path is 'approve' or 'conditional' but the rationale describes a blocking rule failure.",  # noqa: E501
        "The rationale fabricates a credit score, DTI, LTV, or any numerical field not present in the context.",  # noqa: E501
        "The output recommends a course of action prohibited by the rules engine (e.g. approval despite block-severity failure).",  # noqa: E501
    ),
)


ADVERSE_ACTION_LETTER_CONSTITUTION = Constitution(
    name="decision.adverse_action_letter_v1",
    principles=(
        "The letter is addressed to the applicant by name as supplied in the context.",  # noqa: E501
        "The letter cites the today's date supplied in the context (not a fabricated date).",  # noqa: E501
        "The letter cites the lender name + address + phone + email from the context (or omits the line entirely if marked '(not configured)').",  # noqa: E501
        "The body references each principal_reason in plain English — never as the raw rule_id token like 'doc_completeness' or 'ltv_under_cap'.",  # noqa: E501
        "The letter includes the standard ECOA disclosures (right to know specific reasons; right to a copy of any appraisal used).",  # noqa: E501
        "The credit-reporting paragraph appears if and only if a credit reporting agency was supplied in the context.",  # noqa: E501
        "The letter is signed using the authorized officer name + title from the context (or 'Credit Committee' generically if the name is not configured).",  # noqa: E501
        "The tone is formal, factual, and unambiguous — no hedging or apologetic softening.",  # noqa: E501
    ),
    red_lines=(
        "The output contains bracketed placeholders like [LENDER NAME], [DATE], [APPLICANT NAME], [CREDIT REPORTING AGENCY NAME, ADDRESS, PHONE].",  # noqa: E501
        "The output uses the raw rule_id token (e.g. 'doc_completeness', 'ltv_under_cap') in the letter body — only the friendly label is allowed in prose.",  # noqa: E501
        "The output omits a principal_reason that the rules engine flagged as a blocking failure.",  # noqa: E501
        "The output makes promises about future application outcomes ('if you re-apply you will be approved').",  # noqa: E501
    ),
    # Cheap pre-judge fast-fail. Cover the common drift patterns so a
    # regression in the drafter prompt gets caught before paying for
    # the judge LLM call.
    forbidden_substrings=(
        "[LENDER NAME]",
        "[DATE]",
        "[APPLICANT NAME]",
        "[CREDIT REPORTING AGENCY",
        "[LOAN AMOUNT]",
        "[AUTHORIZED OFFICER",
    ),
)
# fmt: on


# Hard cap on the self-correction loop. After this many failed
# judgments the drafter has had enough chances; we persist the latest
# draft with a ``flagged_by_judge`` audit so a human reviewer can
# inspect. Two retries (three attempts total) is consistent with the
# Self-Refine paper's finding that returns flatten past ~3 iterations.
MAX_VALIDATION_ATTEMPTS = 3


# fmt: off
INTAKE_DOC_REQUEST_CONSTITUTION = Constitution(
    name="intake.doc_request_v1",
    principles=(
        "The email greets the borrower using the exact name supplied in the context.",  # noqa: E501
        "The email mentions the loan reference supplied in the context at least once (body or subject).",  # noqa: E501
        "The sign-off names the officer, title, and institution from the context exactly — no placeholders.",  # noqa: E501
        "The email asks only for items that map to the missing-fields list in the context — it does not invent new asks.",  # noqa: E501
        "The body is plain prose (no Markdown — no leading '#' headers, no '**bold**', no '1.' numbered lists).",  # noqa: E501
        "The tone is professional, friendly, and the body is ≤ 160 words.",  # noqa: E501
        "The subject line is specific and includes the loan reference.",  # noqa: E501
        "The asks match the loan class — a personal-loan borrower is not asked for an appraisal/rent roll; a business borrower is not asked for pay stubs.",  # noqa: E501
    ),
    red_lines=(
        "The output contains bracketed placeholders like [BORROWER NAME], [LOAN REFERENCE], [LOAN OFFICER], [INSTITUTION], [OFFICER EMAIL].",  # noqa: E501
        "The output promises a credit decision, rate, or term not yet rendered by the rules engine.",  # noqa: E501
        "The output discloses internal scoring, the DTI cap, the FICO floor, or any raw rule_id token.",  # noqa: E501
        "The output asks for documents irrelevant to the loan class (rent roll for a personal-loan borrower; pay stubs for a business borrower).",  # noqa: E501
    ),
    forbidden_substrings=(
        "[BORROWER NAME]",
        "[LOAN REFERENCE]",
        "[LOAN OFFICER",
        "[INSTITUTION",
        "[OFFICER EMAIL]",
        "[SIGN-OFF]",
    ),
)


UNDERWRITING_SUMMARY_CONSTITUTION = Constitution(
    name="underwriting.summary_v1",
    principles=(
        "Every section's prose references at least one extraction field by name (e.g. annual_noi, credit_score) or one KPI from the supplied KPI block.",  # noqa: E501
        "Rule outcomes are referenced by friendly label in prose — never by raw rule_id token like 'doc_completeness' or 'ltv_under_cap'.",  # noqa: E501
        "The recommendation is consistent with the rule outcomes — 'decline' only when a BLOCKING rule failed; 'request_more_info' when docs are missing.",  # noqa: E501
        "No numerical KPI (DSCR, LTV, DTI, FICO, NOI, debt yield) is quoted unless present in the supplied KPI block.",  # noqa: E501
        "The summary is written for a credit committee — formal, factual, ≤ 5 sections, each section 1–3 sentences.",  # noqa: E501
        "Vocabulary matches the loan class — commercial language (DSCR, rent roll) for business loans; consumer language (DTI, FICO, employer) for personal loans.",  # noqa: E501
    ),
    red_lines=(
        "The output contains bracketed placeholders like [DSCR], [LTV], [BORROWER], [PROPERTY ADDRESS], [FICO].",  # noqa: E501
        "The output recommends 'proceed_to_decision' when the rule outcomes contain a BLOCKING failure.",  # noqa: E501
        "The output fabricates a credit score, DSCR, LTV, NOI, DTI, or any numeric field absent from the supplied KPI block.",  # noqa: E501
        "The output mixes commercial vocabulary into a personal-loan summary (or consumer vocabulary into a commercial summary) — these are different products.",  # noqa: E501
    ),
    forbidden_substrings=(
        "[DSCR]",
        "[LTV]",
        "[DTI]",
        "[NOI]",
        "[FICO]",
        "[BORROWER ENTITY]",
        "[PROPERTY ADDRESS]",
    ),
)
# fmt: on


# ---------------------------------------------------------------------------
# Self-Refine builders — graph-agnostic node + router factories.
#
# The decision agent was the first user of this loop. Extending it
# to intake + underwriting could have been three near-identical
# `validate_X` nodes + three near-identical `route_after_validate_X`
# routers; instead, both pieces are factored here as builder
# functions parameterized on the judgments and the retry/persist
# edges. The agents wire them in like any other LangGraph node.
# ---------------------------------------------------------------------------


# State written by the validator node. Mirrors the fields the
# drafter nodes already read (``last_critique``) and the conditional
# router consumes (``last_judgment``). The dict shape keeps it
# TypedDict-friendly — agents extend their own state classes with
# these three keys so the existing typing stays useful.
ValidatorState = dict[str, Any]


@dataclass(frozen=True)
class JudgmentSpec:
    """One judgment the validator should run against the graph state.

    The ``extract_text`` callable returns either the text to judge
    or ``None`` to skip this judgment (used e.g. when an AAL is only
    present on the decline path — verdict-text is always judged,
    AAL text is judged only when it exists).

    The ``extract_context`` callable returns the supporting context
    string that the judge LLM sees. The context lets the judge
    evaluate principles that reference outside facts ("addresses
    the borrower by name") without the validator having to thread
    state into the constitution itself.
    """

    constitution: Constitution
    extract_text: Callable[[ValidatorState], str | None]
    extract_context: Callable[[ValidatorState], str]


def make_validator_node(
    specs: tuple[JudgmentSpec, ...],
) -> Callable[[ValidatorState], Awaitable[ValidatorState]]:
    """Build a LangGraph node that runs every ``JudgmentSpec`` and
    writes the worst severity into ``state.last_judgment`` +
    ``state.last_critique``.

    "Worst" follows the natural severity ranking — block beats warn
    beats ok. This means a clean verdict but a flagged AAL still
    triggers the loop-back (correct — we'd rather force a re-draft
    of the verdict than ship a bad letter).

    Spec text-extractors returning ``None`` are skipped silently —
    that's the documented way to make a judgment conditional on
    state (e.g. AAL-only-on-decline).
    """

    async def _validate(state: ValidatorState) -> ValidatorState:
        judgments: list[JudgmentResult] = []
        for spec in specs:
            text = spec.extract_text(state)
            if not text:
                continue
            j = await judge_against_constitution(
                output_text=text,
                constitution=spec.constitution,
                context=spec.extract_context(state),
            )
            judgments.append(j)

        if not judgments:
            # Nothing to judge (every spec returned None). Treat as
            # a silent pass so the router proceeds to persist.
            return state

        severity_rank = {"ok": 0, "warn": 1, "block": 2}
        worst = max(judgments, key=lambda j: severity_rank[j.severity])
        return {
            **state,
            "last_judgment": worst.to_audit_payload(),
            # Only carry the critique forward when the verdict was
            # not ``ok`` — the drafter uses ``last_critique`` as the
            # signal to add the "previous draft was rejected"
            # paragraph. Always wiping it on ok avoids a stale
            # critique leaking into a clean re-run.
            "last_critique": worst.critique if not worst.passed else None,
        }

    return _validate


def make_validator_router(
    *,
    retry_node: str,
    persist_node: str,
) -> Callable[[ValidatorState], str]:
    """Build a LangGraph conditional-edge router for Self-Refine.

    Three outcomes:

    - ``ok`` / ``warn``: route to ``persist_node``. ``warn`` is
      recorded in the audit but doesn't block — a human still
      reviews the final artifact and can decide.
    - ``block`` with retries remaining: route to ``retry_node``.
      The drafter sees ``last_critique`` in state and revises.
    - ``block`` with no retries left: route to ``persist_node``
      anyway. The audit captures the unresolved judgment so a
      reviewer sees the flag.

    This is the LangGraph-native form of the Self-Refine cycle —
    no custom loop logic, just a conditional edge that points back
    to the source node.
    """

    def _route(state: ValidatorState) -> str:
        j = state.get("last_judgment") or {}
        severity = j.get("severity", "ok")
        attempts = state.get("validation_attempts", 0)

        if severity == "block" and attempts < MAX_VALIDATION_ATTEMPTS:
            logger.info(
                "guardrail_loop_back",
                attempt=attempts,
                max_attempts=MAX_VALIDATION_ATTEMPTS,
                retry_node=retry_node,
            )
            return retry_node
        if severity == "block":
            logger.warning(
                "guardrail_max_attempts_reached",
                attempts=attempts,
                persist_node=persist_node,
            )
        return persist_node

    return _route
