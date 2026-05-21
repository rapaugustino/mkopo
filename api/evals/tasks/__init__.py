"""Eval task implementations."""

from evals.tasks.extract_borrower_entity import ExtractBorrowerEntityTask
from evals.tasks.extract_noi import ExtractNOITask
from evals.tasks.summarize_underwriting import SummarizeUnderwritingTask

__all__ = [
    "ExtractBorrowerEntityTask",
    "ExtractNOITask",
    "SummarizeUnderwritingTask",
]
