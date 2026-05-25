"""Filename-heuristic document classifier.

We need to pick a ``DocumentType`` at upload time — not at extraction
time — so the downstream rule-engine + transition-prerequisite checks
(which key off ``Document.doc_type``) agree with the UI's required-doc
checklist (which keys off filename). Before this module existed, the
upload paths hardcoded ``doc_type=UNKNOWN`` for every file and the
two surfaces disagreed:

- Status-page checklist saw ``loan_application.txt`` and ticked the
  "Loan application" box (filename heuristic, frontend).
- The stage-transition prereq check queried
  ``WHERE doc_type IN ('loan_application', ...)`` and saw zero rows,
  blocking the transition with "missing required document(s)"
  even though the file was clearly there.

The classifier here is intentionally cheap and deterministic — no
LLM call, no PDF parsing. The intake agent does proper content-based
extraction later; this is just enough to keep the two surfaces in
sync at upload time. UNKNOWN remains the fallback for files whose
name carries no signal.
"""

from __future__ import annotations

from mkopo.models import DocumentType

# (DocumentType, [keywords]). The keywords are checked as
# whitespace-normalized substrings in the lowercased filename. Order
# matters — the first match wins, so place more-specific types first.
_RULES: list[tuple[DocumentType, tuple[str, ...]]] = [
    # Tax returns + bank statements are personal-loan staples; both
    # have unambiguous filenames in practice (no overlap with the
    # commercial doc set).
    (DocumentType.TAX_RETURN, ("tax_return", "tax-return", "1040", "form_1040")),
    (
        DocumentType.BANK_STATEMENT,
        ("bank_statement", "bank-statement", "account_statement"),
    ),
    # PFS — note the order matters: this must check BEFORE the more
    # generic ``financial_statement`` keyword (which we don't include,
    # but the substring would still match "personal_financial_statement").
    (
        DocumentType.PERSONAL_FINANCIAL_STATEMENT,
        ("personal_financial_statement", "personal-financial-statement", "pfs"),
    ),
    # Commercial pack.
    (DocumentType.APPRAISAL, ("appraisal", "valuation_report", "valuation-report")),
    (DocumentType.RENT_ROLL, ("rent_roll", "rent-roll", "rentroll")),
    (DocumentType.INSURANCE, ("insurance", "coi_", "certificate_of_insurance")),
    (DocumentType.TITLE_REPORT, ("title_report", "title-report", "title_commitment")),
    # Loan application LAST in this group because "loan" appears in a
    # lot of unrelated filenames; we want the more specific matches to
    # have a chance first.
    (
        DocumentType.LOAN_APPLICATION,
        ("loan_application", "loan-application", "application_form", "1003"),
    ),
]


def classify_from_filename(filename: str) -> DocumentType:
    """Return the best-guess :class:`DocumentType` for an upload.

    Falls through to ``UNKNOWN`` when no rule matches — the intake
    agent's content extractor will still run against the document
    body and can correct the classification later if needed.
    """
    if not filename:
        return DocumentType.UNKNOWN
    needle = filename.lower()
    for doc_type, keywords in _RULES:
        if any(kw in needle for kw in keywords):
            return doc_type
    return DocumentType.UNKNOWN
