"""aal_fidelity — scores the AAL drafter on regulator-load-bearing criteria.

CFPB Circular 2022-03 ("Adverse Action Notification Requirements
in Connection with Credit Decisions Based on Complex Algorithms")
and the follow-up Circular 2023-09 require lenders to give the
applicant the **principal reasons** the model declined the
application. "The model is too complex" is not a defense.

This task pins four observable properties of the drafted AAL:

1. **principal_reasons_complete** — every blocking rule_id from the
   fixture's expected list appears in the drafted ``principal_reasons``.
2. **friendly_label_in_body** — each expected friendly label (the
   borrower-readable phrase) appears in the body text.
3. **no_rule_id_in_body** — the raw rule_id token (e.g.
   ``ltv_under_cap``) does NOT appear in the body prose. The
   constitutional judge already blocks this, but the eval scores it
   as a per-example metric so a regression shows on the trend.
4. **right_to_know_disclosure** — the ECOA "right to a statement
   of specific reasons" sentence appears in the body. Required by
   12 CFR §1002.9(b).

Score = AND of all four (a single failure on any criterion fails
the example). The aggregate exposes per-criterion pass rates so a
reviewer can spot whether the AAL drafter is failing on a specific
property without losing the overall pass/fail.

The drafter LLM is invoked through the same gateway the live agent
uses — so this measures the prompt-as-deployed.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from evals.types import Example, TaskScore
from mkopo.config import get_settings
from mkopo.llm_gateway import get_gateway

# Phrases that mark the ECOA "right to know" disclosure. We accept
# any one of several common formulations — the prompt has freedom in
# how to render it, the eval just verifies the disclosure exists.
_RIGHT_TO_KNOW_MARKERS = [
    "right to know",
    "right to receive",
    "specific reasons",
    "statement of specific reasons",
    "principal reasons",
]


SYSTEM = (
    "You are drafting a borrower-facing adverse-action letter for a "
    "declined loan application. The letter must comply with ECOA "
    "Regulation B § 1002.9.\n\n"
    "Rules:\n"
    "- Cite EVERY supplied blocking-failure rule as a principal "
    "reason. Do not omit any.\n"
    "- In the body PROSE, refer to each reason using the friendly "
    "label (plain-English phrase). NEVER use the raw rule_id token.\n"
    "- Include the ECOA 'right to a statement of specific reasons' "
    "disclosure exactly once. Standard phrasing is fine.\n"
    "- Do not use bracketed placeholders ([NAME], [DATE], etc.).\n"
    "- Tone is formal, factual, non-apologetic."
)


class _DraftedAAL(BaseModel):
    subject: str = Field(max_length=120)
    body_text: str = Field(max_length=2400)
    principal_reasons: list[str] = Field(
        description=(
            "Machine-readable list of the rule_id tokens that were the principal basis for decline."
        )
    )


def _build_user(inputs: dict[str, Any]) -> str:
    blocks = "\n".join(
        f"- rule_id={r['rule_id']}, severity={r['severity']}, "
        f"passed={r['passed']}, "
        f"message={r['message']}, "
        f"friendly_label={r.get('friendly_label', r['rule_id'])}"
        for r in inputs["rule_outcomes"]
    )
    return (
        f"Applicant: {inputs['applicant_name']}\n"
        f"Loan class: {inputs.get('loan_class', 'business')}\n\n"
        f"Rule outcomes:\n{blocks}\n\n"
        "Draft the adverse-action letter now."
    )


class AALFidelityTask:
    name = "aal_fidelity"
    # Threshold relaxed vs decision_verdict because four AND-ed
    # criteria are harder to hit perfectly than a single classification
    # task. Tighten as the prompt + golden set mature.
    threshold = 0.75

    async def predict(self, example: Example) -> dict[str, Any]:
        settings = get_settings()
        result: _DraftedAAL = await get_gateway().call_structured(
            model=settings.llm_default_model,
            system=SYSTEM,
            user=_build_user(example.inputs),
            schema=_DraftedAAL,
        )
        return {
            "subject": result.subject,
            "body_text": result.body_text,
            "principal_reasons": result.principal_reasons,
        }

    def score(self, prediction: dict[str, Any], expected: dict[str, Any]) -> TaskScore:
        body = (prediction.get("body_text") or "").lower()
        principal_reasons = [(r or "").lower() for r in prediction.get("principal_reasons") or []]

        expected_rule_ids = [(r or "").lower() for r in expected.get("blocking_rule_ids", [])]
        expected_labels = [
            (lbl or "").lower() for lbl in expected.get("friendly_labels_in_body", [])
        ]

        # Criterion 1: every expected blocking rule_id appears in
        # principal_reasons (machine-readable list).
        principal_reasons_complete = all(
            any(rid in pr for pr in principal_reasons) for rid in expected_rule_ids
        )

        # Criterion 2: every expected friendly label appears in the
        # body prose.
        friendly_label_in_body = all(label in body for label in expected_labels)

        # Criterion 3: NONE of the raw rule_id tokens appears as a
        # word in the body. We bracket-match on word boundaries so
        # "ltv" inside "ltv_under_cap" matches but ordinary uses of
        # "ltv" in prose are NOT flagged unless they're identical to
        # the rule_id form.
        no_rule_id_in_body = not any(
            re.search(rf"\b{re.escape(rid)}\b", body) for rid in expected_rule_ids
        )

        # Criterion 4: ECOA "right to know" disclosure appears.
        right_to_know_disclosure = any(marker in body for marker in _RIGHT_TO_KNOW_MARKERS)

        passed = (
            principal_reasons_complete
            and friendly_label_in_body
            and no_rule_id_in_body
            and right_to_know_disclosure
        )

        return TaskScore(
            score=sum(
                [
                    principal_reasons_complete,
                    friendly_label_in_body,
                    no_rule_id_in_body,
                    right_to_know_disclosure,
                ]
            )
            / 4.0,
            passed=passed,
            details={
                "principal_reasons_complete": principal_reasons_complete,
                "friendly_label_in_body": friendly_label_in_body,
                "no_rule_id_in_body": no_rule_id_in_body,
                "right_to_know_disclosure": right_to_know_disclosure,
            },
        )

    def aggregate(
        self,
        scores: list[TaskScore],
        examples: list[Example],
    ) -> dict[str, Any]:
        """Per-criterion pass rate across the golden set.

        Lets the dashboard show "ECOA disclosure: 100% pass" even
        when overall fidelity is < threshold because the body uses
        the rule_id token in one example. Surfaces *which* criterion
        is failing.
        """
        criteria = [
            "principal_reasons_complete",
            "friendly_label_in_body",
            "no_rule_id_in_body",
            "right_to_know_disclosure",
        ]
        n = len(scores)
        per_criterion: dict[str, dict[str, float | int]] = {}
        for c in criteria:
            passed = sum(1 for s in scores if s.details.get(c) is True)
            per_criterion[c] = {
                "n": n,
                "passed": passed,
                "rate": passed / n if n else 0.0,
            }
        return {"per_criterion": per_criterion}
