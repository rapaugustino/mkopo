"""uw_groundedness — RAGAS-style faithfulness scoring on UW summaries.

Background
----------

The underwriting summary is the load-bearing artifact between the
extractor + rules engine and the human approver. Factuality
regressions (the LLM invents a number that's not in the source
documents, or paraphrases a rule outcome inaccurately) are exactly
the failure mode SR 11-7 §VI calls "outcome analysis" — you can't
trust a summary as decision input if you can't bound how often it
hallucinates.

This task implements the RAGAS "Faithfulness" metric — the field-
standard for grounding evaluation in LLM apps. Reference:
- Es et al. 2024 (RAGAS): https://arxiv.org/abs/2309.15217
- Production framework: https://docs.ragas.io/en/latest/concepts/metrics/faithfulness.html

How it works
------------

For each fixture we get:
1. ``source_excerpts`` — the verbatim slices the summary should
   have drawn from (loan data, doc text, rule outcomes).
2. ``summary_text`` — the AI-generated summary to score.

A pinned judge LLM decomposes ``summary_text`` into atomic factual
claims, then verifies each claim against ``source_excerpts`` in a
single structured call. Score per example =
``supported_claims / total_claims``. Aggregate exposes the score
distribution + total claim counts so the dashboard can show "X of N
claims supported across the suite" without losing the per-example
detail.

Why a single combined call (claim extraction + verification) instead
of the canonical two-stage RAGAS pipeline: cost. The two-stage
pipeline does N+1 LLM calls per summary (one extract, N verify);
the combined call is 1. The accuracy tradeoff is small in practice
because Opus-tier models handle the joint task reliably.

Threshold: 0.80. Tighter than ``aal_fidelity`` because grounding is
load-bearing — but loose enough to absorb the occasional over-zealous
judge marking a fair paraphrase as "unsupported".
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from evals.types import Example, TaskScore
from mkopo.config import get_settings
from mkopo.llm_gateway import get_gateway


class _ClaimVerdict(BaseModel):
    """One atomic factual claim extracted from the summary, with the
    judge's verdict on whether the source excerpts entail it."""

    claim: str = Field(
        max_length=400,
        description=(
            "Atomic factual claim extracted from the summary. Must be "
            "verifiable independently — e.g. 'DSCR is 1.32x', not "
            "'the deal looks reasonable'."
        ),
    )
    supported: bool = Field(
        description=(
            "True if the claim is entailed by some span of the source "
            "excerpts. False if it contradicts the source or is "
            "unsupported (no matching span). Paraphrases that preserve "
            "the numeric / categorical fact count as supported."
        ),
    )
    evidence: str = Field(
        max_length=300,
        description=(
            "If supported: short quote or paraphrase of the supporting "
            "span. If unsupported: one-sentence explanation of why "
            "(contradicts X / no mention of X)."
        ),
    )


class _GroundednessResult(BaseModel):
    """Per-summary judgment. The list is sorted in claim order — the
    judge processes the summary left to right."""

    claims: list[_ClaimVerdict] = Field(
        min_length=1,
        description=(
            "Every atomic factual claim in the summary, with its "
            "verification verdict. Aim for granularity: a summary "
            "with three numbers + one risk callout should yield 4 "
            "claims, not 1."
        ),
    )


_JUDGE_SYSTEM = """You are a strict factuality auditor for AI-generated underwriting summaries.

Your job: decompose the summary into ATOMIC factual claims and verify
each one against the supplied source excerpts.

Rules:
1. Granularity. Each claim must be independently verifiable. "DSCR is
   1.32x" is a claim. "The deal looks reasonable" is not.
2. Coverage. Extract EVERY factual claim — numbers, categories
   (property type, loan class), names (sponsor, lender), rule
   outcomes ("DSCR passes"), and the directional verdict ("recommend
   approve"). Don't skip claims because they look obvious.
3. Verification. A claim is SUPPORTED iff some span of the source
   excerpts entails it. Numeric paraphrases (e.g. summary says "1.32",
   source says "1.32x" or "DSCR=1.32") are supported. Contradictions
   (summary says 1.20, source says 1.32) are NOT supported. Claims
   the source doesn't mention at all are NOT supported.
4. Evidence. For each supported claim, quote the supporting span (or
   a short paraphrase). For each unsupported claim, briefly explain
   why (contradicts X / no mention of X).
5. No editorial. Don't reward "the summary is reasonable" or punish
   "the summary is short". You're checking facts, not style.
"""


class UWGroundednessTask:
    """RAGAS-style faithfulness on underwriting summaries.

    Threshold 0.80 — a summary should have at least 80% of its
    claims grounded in the source. Tighter than aal_fidelity, looser
    than decision_verdict; calibrated so a single mildly-uncertain
    paraphrase doesn't fail the example.
    """

    name = "uw_groundedness"
    threshold = 0.80

    async def predict(self, example: Example) -> dict[str, Any]:
        """Run the judge in a single structured call.

        Inputs the fixture must provide:
          - source_excerpts: str (the document / rule-outcome
            verbatim text the summary should have drawn from)
          - summary_text: str (the AI-generated summary under test)

        We don't re-generate the summary inside the eval. Fixtures
        carry the summary verbatim so that a regression in the judge
        prompt is isolated from a regression in the summarizer.
        For prompt-as-deployed coverage of the summarizer itself,
        see ``summarize_underwriting``.
        """
        settings = get_settings()
        source = example.inputs["source_excerpts"]
        summary = example.inputs["summary_text"]
        # Pinned judge model — same convention as
        # SummarizeUnderwritingTask. Changing it invalidates
        # historical scores.
        result: _GroundednessResult = await get_gateway().call_structured(
            model=settings.llm_judge_model,
            system=_JUDGE_SYSTEM,
            user=(
                f"Source excerpts:\n---\n{source}\n---\n\n"
                f"Summary to verify:\n---\n{summary}\n---\n\n"
                "Decompose into atomic claims and verify each one."
            ),
            schema=_GroundednessResult,
        )
        return {
            "claims": [c.model_dump() for c in result.claims],
        }

    def score(self, prediction: dict[str, Any], expected: dict[str, Any]) -> TaskScore:
        """Per-example score.

        The eval measures *judge accuracy* — does the judge land each
        summary in the expected groundedness band?

        Fixtures declare ``expected.expected_band``:
          - ``"high"`` (default): clean summary; judge should score
            ≥ 0.85.
          - ``"low"``: planted-hallucination summary; judge should
            score < 0.85 (i.e. flag at least one unsupported claim).

        Without this two-sided check the gate would either:
          (a) treat hallucination fixtures as failures and refuse to
              ship even when the judge is doing its job, or
          (b) pass any time the judge fails to detect hallucination
              (a silent regression on the load-bearing metric).

        ``score`` carries the raw groundedness ratio so the dashboard
        can still show corpus-wide grounding levels per fixture.
        ``passed`` is the band-match — that's what feeds accuracy /
        gate.
        """
        claims: list[dict[str, Any]] = prediction.get("claims", [])
        total = len(claims)
        supported = sum(1 for c in claims if c.get("supported"))
        # Defensive: a 0-claim result is treated as a 0 score so
        # an empty judgment doesn't game the gate. The judge schema
        # forces min_length=1 so this is just belt-and-braces.
        raw_score = supported / total if total > 0 else 0.0

        expected_band = (expected.get("expected_band") or "high").lower()
        # The discrimination threshold: a clean summary's grounded
        # ratio should land at or above this, a hallucinated
        # summary's should land below. 0.85 gives the judge a small
        # tolerance band for the occasional false-negative-on-its-
        # own-paraphrase (e.g. judge marks "DSCR is 1.32x" against
        # "DSCR=1.32" as unsupported on string-match) without
        # collapsing the discrimination signal.
        discrimination = 0.85
        if expected_band == "low":
            passed = raw_score < discrimination
        else:
            passed = raw_score >= discrimination

        unsupported = [
            {"claim": c.get("claim"), "evidence": c.get("evidence")}
            for c in claims
            if not c.get("supported")
        ]
        return TaskScore(
            score=raw_score,
            passed=passed,
            details={
                "total_claims": total,
                "supported_claims": supported,
                "unsupported_claims": unsupported,
                "expected_band": expected_band,
                "discrimination_threshold": discrimination,
            },
        )

    def aggregate(
        self,
        scores: list[TaskScore],
        examples: list[Example],
    ) -> dict[str, Any]:
        """Corpus-wide groundedness + per-example distribution.

        The dashboard reads two numbers off this:
          - ``judge_accuracy`` — % of fixtures the judge classified
            into the right band. Feeds the "is the judge working?"
            headline.
          - ``avg_grounded_clean`` — mean grounded ratio across the
            high-band fixtures only. Lets the dashboard show how
            grounded "clean" summaries are without the hallucinated
            ones dragging the mean down.

        ``per_example`` carries each fixture's score + expected band
        so the card can render a scatter or per-row table.
        """
        n = len(scores)
        passed = sum(1 for s in scores if s.passed)
        total_claims = sum(int(s.details.get("total_claims") or 0) for s in scores)
        supported_claims = sum(int(s.details.get("supported_claims") or 0) for s in scores)

        # Split corpus stats by expected band so the dashboard can
        # show clean-fixture grounding (the "headline" number) without
        # the planted-hallucination examples dragging the mean down.
        clean_scores = [s for s in scores if (s.details.get("expected_band") or "high") == "high"]
        hallucinated_scores = [
            s for s in scores if (s.details.get("expected_band") or "high") == "low"
        ]
        avg_clean = sum(s.score for s in clean_scores) / len(clean_scores) if clean_scores else 0.0
        avg_hallucinated = (
            sum(s.score for s in hallucinated_scores) / len(hallucinated_scores)
            if hallucinated_scores
            else 0.0
        )

        per_example = [
            {
                "id": ex.id,
                "score": s.score,
                "passed": s.passed,
                "total_claims": int(s.details.get("total_claims") or 0),
                "supported_claims": int(s.details.get("supported_claims") or 0),
                "expected_band": s.details.get("expected_band") or "high",
            }
            for s, ex in zip(scores, examples, strict=True)
        ]
        return {
            # Judge accuracy is the same as TaskResult.accuracy by
            # construction; surfaced here for the dashboard so it
            # doesn't have to know the equivalence.
            "judge_accuracy": passed / n if n else 0.0,
            "avg_grounded_clean": avg_clean,
            "avg_grounded_hallucinated": avg_hallucinated,
            "total_claims": total_claims,
            "supported_claims": supported_claims,
            "n_clean": len(clean_scores),
            "n_hallucinated": len(hallucinated_scores),
            "per_example": per_example,
        }
