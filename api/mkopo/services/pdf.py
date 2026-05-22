"""PDF text extraction.

We use ``pypdf`` rather than a managed service (AWS Textract, Google
Document AI) because:

- It works offline and has no per-page cost — important for portfolio
  scope and for keeping the dev loop fast.
- For text-native PDFs (loan applications, appraisals exported from
  Excel / Word) the quality is comparable to managed OCR.
- For scanned PDFs (image-only pages), pypdf returns empty strings and
  we mark the page accordingly. A production deployment with mixed
  document quality would layer Textract on top — that's a 30-line
  change in this module, not a system rewrite.

The contract this module exposes is one function: ``extract_text``.
Everything else (chunking, embedding, persistence) happens downstream
in ``services.ingest`` against the resulting plain string.
"""

from __future__ import annotations

import io

import structlog
from pypdf import PdfReader

logger = structlog.get_logger()


# A page is considered "image-only / needs OCR" if it yields fewer than
# this many characters of extractable text. Tuned against real-world
# scanned appraisals — ~30 chars catches genuinely empty pages without
# false-positiving short cover pages.
MIN_PAGE_CHARS_FOR_TEXT_NATIVE = 30


def extract_text(body: bytes) -> tuple[str, dict[str, int]]:
    """Extract text from a PDF byte buffer.

    Returns ``(text, stats)`` where ``stats`` includes:

    - ``page_count``: total pages in the PDF
    - ``pages_with_text``: pages that yielded usable extracted text
    - ``pages_needing_ocr``: pages that came back empty / near-empty

    Each page is separated by ``"\\n\\n"`` in the returned text. Pages
    flagged as needing OCR are replaced inline with the marker
    ``[page N — image only, OCR not run]`` so chunking still produces
    one chunk per page and downstream features know the gap exists.
    """
    reader = PdfReader(io.BytesIO(body))
    page_count = len(reader.pages)
    pages_with_text = 0
    pages_needing_ocr = 0
    out: list[str] = []

    for i, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001 — pypdf raises a zoo of exceptions
            logger.exception("pdf_page_extract_failed", page=i)
            text = ""

        if len(text) >= MIN_PAGE_CHARS_FOR_TEXT_NATIVE:
            out.append(text)
            pages_with_text += 1
        else:
            out.append(f"[page {i} — image only, OCR not run]")
            pages_needing_ocr += 1

    extracted = "\n\n".join(out)
    stats = {
        "page_count": page_count,
        "pages_with_text": pages_with_text,
        "pages_needing_ocr": pages_needing_ocr,
        "char_count": len(extracted),
    }
    logger.info("pdf_extract_complete", **stats)
    return extracted, stats
