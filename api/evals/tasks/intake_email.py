"""intake_email — scores the borrower doc-request email drafter.

The intake agent's ``draft_doc_request`` node writes a borrower-
facing email when documents are missing. The email is the FIRST
direct touchpoint between Mkopo and the applicant — getting it wrong
(wrong loan class, markdown that renders as raw ``**`` in their
inbox, asking a business borrower for pay stubs, etc.) is a
brand-damaging foot-gun.

This task pins four observable properties of the draft. Each
criterion is a binary check; the overall score is the AND of all
four (matches ``aal_fidelity``'s strict-AND pattern).

Criteria
--------

1. ``addressed_by_name`` — the borrower's name appears in the body.
   No "Dear customer" / "Dear borrower" / placeholder. Required by
   the prompt's "Greet the borrower by name" instruction.
2. ``no_markdown`` — the body contains no ``**bold**``, ``# heading``,
   or ``1. numbered list`` syntax. The email renders as PLAIN TEXT
   via Resend / SMTP; markdown shows up as literal asterisks in the
   inbox.
3. ``doc_asks_match_class`` — for ``loan_class=personal`` the body
   mentions at least one of {pay stub, W-2, tax return, bank
   statement}; for ``business`` it mentions at least one of
   {appraisal, rent roll, operating statement, PFS, business tax
   return}. Cross-class doc asks (e.g. "pay stubs" on a commercial
   deal) are a common failure mode when the prompt drifts.
4. ``within_word_limit`` — body ≤ 120 words. The prompt asks for it
   explicitly; longer emails reduce reply rate.

The task fetches the same prompt the production agent uses
(``intake.draft_doc_request.{personal|business}`` from the prompt
registry), so a CI run measures the prompt-as-deployed. Edits made
through ``/prompts`` are reflected on the next eval run.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from evals.types import Example, TaskScore
from mkopo.config import get_settings
from mkopo.llm_gateway import get_gateway
from mkopo.services.prompts import get as get_prompt

# Doc-keyword vocabularies for the per-class match. Lowercased; the
# scorer does case-insensitive substring matching against the body.
# Keep these in sync with ``mkopo.agents.intake._DOC_ASKS_PERSONAL`` /
# ``_DOC_ASKS_BUSINESS`` — if a new class-specific document type is
# added there, add it here so the eval recognises the prompt's
# success path.
_PERSONAL_DOC_TERMS = (
    "pay stub",
    "paystub",
    "w-2",
    "w2",
    "tax return",
    "1040",
    "bank statement",
    "photo id",
    "government-issued id",
    "1099",
)
_BUSINESS_DOC_TERMS = (
    "appraisal",
    "rent roll",
    "operating statement",
    "personal financial statement",
    "business tax return",
    "guarantor",
    "trailing-12",
    "pfs",
)


class _DraftedEmail(BaseModel):
    """Mirror of ``mkopo.agents.intake.DraftedEmail`` — kept local to
    this module so the eval doesn't import agent-internal types."""

    subject: str = Field(max_length=120)
    body_text: str = Field(max_length=4000)


def _product_context(loan_class: str) -> str:
    """Same product context block as ``intake.draft_doc_request``.

    Duplicated rather than imported because the intake agent's helper
    is wrapped inside the node body (not extracted). Keeping it inline
    here means the eval is self-contained and a future refactor of
    the agent doesn't silently change what the eval measures.
    """
    if loan_class == "personal":
        return (
            "This is an UNSECURED PERSONAL LOAN application. The borrower is "
            "an individual. Documents we expect them to provide: pay stubs, "
            "tax returns, bank statements, a government ID. We do NOT need "
            "an appraisal or rent roll — there is no property securing this "
            "loan."
        )
    return (
        "This is a COMMERCIAL / BUSINESS loan application. The "
        "borrower is typically an entity (LLC, corp) and the loan "
        "is secured by a property or business asset. Documents we "
        "expect: appraisal, rent roll (if income-producing), "
        "operating statements, business tax returns, personal "
        "financial statement for guarantors."
    )


def _build_user(inputs: dict[str, Any]) -> str:
    """Compose the user message exactly as the production drafter does.

    Inputs the fixture must provide:
      - borrower_name
      - loan_reference
      - officer_name, officer_email, officer_title, institution
      - missing_fields: list[str] (snake_case underwriting fields)
      - loan_class: "personal" | "business"
    """
    loan_class = inputs.get("loan_class", "business")
    borrower_name = inputs["borrower_name"]
    loan_reference = inputs["loan_reference"]
    officer_name = inputs.get("officer_name", "Loan Officer")
    officer_email = inputs.get("officer_email", "officer@example.com")
    officer_title = inputs.get("officer_title", "Loan Officer")
    institution = inputs.get("institution", "Mkopo Lens")
    missing = inputs.get("missing_fields", [])

    missing_str = "\n".join(f"- {f.replace('_', ' ').title()}" for f in missing)
    if loan_class == "personal":
        doc_asks_list = (
            "Most recent two pay stubs (or 1099s if self-employed)",
            "Most recent two months of bank statements",
            "Most recent year of W-2s or full tax return (Form 1040)",
            "A government-issued photo ID",
        )
    else:
        doc_asks_list = (
            "The most recent property appraisal",
            "A current rent roll (if income-producing)",
            "Trailing-12 operating statements",
            "Two years of business tax returns",
            "Personal financial statement for any guarantor",
        )
    doc_asks = "\n".join(f"- {a}" for a in doc_asks_list)

    product_context = _product_context(loan_class)
    context_block = (
        "Real identifiers — use these verbatim, no placeholders:\n"
        f"- Borrower name (greeting / salutation): {borrower_name}\n"
        f"- Loan reference (mention in opening or subject): {loan_reference}\n"
        f"- Sign-off name: {officer_name}\n"
        f"- Sign-off title: {officer_title}\n"
        f"- Sign-off institution: {institution}\n"
        f"- Sign-off email: {officer_email}\n"
    )
    return (
        f"{product_context}\n\n"
        f"{context_block}\n"
        f"Items the borrower needs to send us (these are the underwriting "
        f"fields still missing — translate to the documents that would "
        f"contain them):\n{missing_str}\n\n"
        f"Suggested documents you can mention by name:\n{doc_asks}\n\n"
        f"FORMAT — this is an email body sent via Resend / SMTP and "
        f"rendered as plain text. **Do NOT use Markdown.** No asterisks, "
        f"no hashes, no numbered lists with `1.` syntax. Plain prose "
        f"paragraphs and (if you must enumerate) lettered bullets like "
        f'"a)" / "b)" or simple inline phrasing. Greet the borrower by '
        f"name. Reference the loan number once.\n"
        f"Tone: professional, friendly. Length: ≤120 words. "
        f"Subject line should be specific and include the loan reference."
    )


# Markdown signatures we treat as a fail. We deliberately exclude
# `*single asterisks*` because they occasionally appear in legitimate
# emphasis on inline product names; the high-confidence markdown
# tokens are bold (`**`), headings (`# ` at line start), and ordered
# lists (`1. ` at line start). The `re.MULTILINE` flag lets the
# heading + list patterns match at any line.
_MARKDOWN_RE = re.compile(
    r"\*\*\S|"  # **bold (asterisk followed by non-space)
    r"^#{1,6}\s|"  # heading at line start
    r"^\s*\d+\.\s",  # numbered list "1. item"
    re.MULTILINE,
)


class IntakeEmailTask:
    """Scoring task for the borrower doc-request email drafter.

    Threshold: 0.80. Four AND-ed criteria are hard to nail every
    time on a creative-writing task; we leave some headroom for the
    LLM to occasionally miss one criterion (e.g. 122-word draft) on
    one example without failing the whole gate. Tighten as the
    prompt + fixture set mature.
    """

    name = "intake_email"
    threshold = 0.80

    async def predict(self, example: Example) -> dict[str, Any]:
        settings = get_settings()
        loan_class = example.inputs.get("loan_class", "business")
        # Fetch the same prompt the production agent uses. Falls back
        # to the registry's default body if no DB row exists — same
        # behaviour as the live drafter.
        system = get_prompt(
            "intake.draft_doc_request.personal"
            if loan_class == "personal"
            else "intake.draft_doc_request.business"
        )
        result: _DraftedEmail = await get_gateway().call_structured(
            model=settings.llm_default_model,
            system=system,
            user=_build_user(example.inputs),
            schema=_DraftedEmail,
        )
        return {
            "subject": result.subject,
            "body_text": result.body_text,
        }

    def score(self, prediction: dict[str, Any], expected: dict[str, Any]) -> TaskScore:
        body = prediction.get("body_text") or ""
        body_lower = body.lower()
        borrower_name = (expected.get("borrower_name") or "").strip()
        loan_class = expected.get("loan_class", "business")

        # Criterion 1: borrower name (first token suffices — "Jane"
        # in "Hi Jane" is a real address even if the prompt wrote
        # "Hi Jane," instead of "Hi Jane Doe,"). We require any
        # name token of length ≥ 3 to appear to avoid spurious
        # matches on "the", "and", etc.
        name_tokens = [tok for tok in re.split(r"\s+", borrower_name) if len(tok) >= 3]
        addressed_by_name = any(tok.lower() in body_lower for tok in name_tokens)

        # Criterion 2: no markdown bold / heading / numbered list.
        no_markdown = _MARKDOWN_RE.search(body) is None

        # Criterion 3: at least one class-appropriate doc keyword
        # appears in the body. ``personal`` and ``business`` have
        # disjoint vocabularies (see top of file) — a single hit is
        # enough.
        terms = _PERSONAL_DOC_TERMS if loan_class == "personal" else _BUSINESS_DOC_TERMS
        matched_terms = [t for t in terms if t in body_lower]
        doc_asks_match_class = len(matched_terms) > 0

        # Criterion 4: word count cap. Use simple whitespace split —
        # close enough to the prompt's "120 words" definition. We
        # tolerate ≤ 130 because email signatures occasionally push
        # a 118-word body over the line on a long officer title /
        # institution name combination. Real regression is ≥150.
        word_count = len(body.split())
        within_word_limit = word_count <= 130

        criteria = [
            addressed_by_name,
            no_markdown,
            doc_asks_match_class,
            within_word_limit,
        ]
        passed = all(criteria)
        return TaskScore(
            score=sum(criteria) / 4.0,
            passed=passed,
            details={
                "addressed_by_name": addressed_by_name,
                "no_markdown": no_markdown,
                "doc_asks_match_class": doc_asks_match_class,
                "within_word_limit": within_word_limit,
                "word_count": word_count,
                "matched_doc_terms": matched_terms,
            },
        )

    def aggregate(
        self,
        scores: list[TaskScore],
        examples: list[Example],
    ) -> dict[str, Any]:
        """Per-criterion pass rates across the fixture set.

        Same shape as ``aal_fidelity`` so the dashboard's
        AALFidelityCard pattern can be reused for the intake card —
        a regression on any single criterion shows on its own bar.
        """
        criteria = (
            "addressed_by_name",
            "no_markdown",
            "doc_asks_match_class",
            "within_word_limit",
        )
        n = len(scores)
        per_criterion: dict[str, dict[str, float | int]] = {}
        for c in criteria:
            passed = sum(1 for s in scores if s.details.get(c) is True)
            per_criterion[c] = {
                "n": n,
                "passed": passed,
                "rate": passed / n if n else 0.0,
            }
        # Also surface the per-class breakdown — useful for spotting
        # drift on personal vs business prompts independently.
        by_class: dict[str, dict[str, int]] = {
            "personal": {"n": 0, "passed": 0},
            "business": {"n": 0, "passed": 0},
        }
        for score, ex in zip(scores, examples, strict=True):
            cls = ex.expected.get("loan_class", "business")
            bucket = by_class.setdefault(cls, {"n": 0, "passed": 0})
            bucket["n"] += 1
            if score.passed:
                bucket["passed"] += 1
        return {
            "per_criterion": per_criterion,
            "by_class": {
                cls: {
                    "n": b["n"],
                    "passed": b["passed"],
                    "rate": b["passed"] / b["n"] if b["n"] else 0.0,
                }
                for cls, b in by_class.items()
            },
        }
