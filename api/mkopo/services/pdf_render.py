"""Render text fixtures to PDF bytes for seeding + manual demos.

The intake agent's PDF extractor (``services/pdf.py``) runs ``pypdf``
over real bytes and pulls out the text layer page-by-page. To get a
seeded loan into a state where the agent actually exercises that
code path — instead of reading text directly out of
``Document.meta.text_content`` — we need real PDF bytes in storage.

This module turns a plain-text fixture string into a multi-page PDF
that:

- Has a header line so the document looks like a bank form, not a
  raw novel.
- Wraps lines at the canvas width so long paragraphs don't run off.
- Page-breaks cleanly so the agent extractor sees the same per-page
  structure a real upload would have.
- Embeds a tiny footer with the document type + a synthetic file
  reference so the PDFs are visually distinguishable in the in-app
  viewer.

The output is byte-identical across runs given the same input string
+ header — important for the materials hash story (if the seed
re-runs and produces the same bytes, content_hash stays stable).

Reportlab is overkill for this — fpdf2 would do — but reportlab is
already a Python standard for PDF generation and adding one more
dep is cheaper than rolling our own line-breaker.
"""

from __future__ import annotations

from io import BytesIO

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

# Page geometry — generous margins, narrow body. Underwriters squint
# at compressed bank docs; we render slightly more readable than a
# typical real-world PDF. Width drives the wrap.
_MARGIN_X = 0.9 * inch
_MARGIN_TOP = 1.0 * inch
_MARGIN_BOTTOM = 0.8 * inch
_BODY_FONT = "Helvetica"
_BODY_SIZE = 10.0
_BODY_LEADING = 13.0  # px between lines
_HEADER_FONT = "Helvetica-Bold"
_HEADER_SIZE = 14.0
_FOOTER_FONT = "Helvetica-Oblique"
_FOOTER_SIZE = 8.0


def _wrap_text(c: canvas.Canvas, text: str, max_width: float) -> list[str]:
    """Wrap ``text`` to fit in ``max_width`` points at the current
    body font. Splits on whitespace; preserves paragraph blank
    lines as explicit empty strings (so the renderer emits a
    one-line vertical break). Long unbroken words get split mid-
    word at the boundary — rare in bank prose but defensive.
    """
    out: list[str] = []
    for raw_line in text.split("\n"):
        if raw_line == "":
            out.append("")
            continue
        # If the original line fits whole, keep it whole — preserves
        # the original line breaks in the fixture (e.g. "Field: value"
        # lines).
        if c.stringWidth(raw_line, _BODY_FONT, _BODY_SIZE) <= max_width:
            out.append(raw_line)
            continue
        # Word-wrap the line, preserving indent on continuation rows.
        words = raw_line.split(" ")
        current = ""
        for w in words:
            candidate = w if not current else f"{current} {w}"
            if c.stringWidth(candidate, _BODY_FONT, _BODY_SIZE) <= max_width:
                current = candidate
            else:
                if current:
                    out.append(current)
                current = w
        if current:
            out.append(current)
    return out


def render_text_to_pdf(*, title: str, body: str, footer_ref: str | None = None) -> bytes:
    """Convert a text fixture into PDF bytes.

    ``title`` becomes the bold page header on every page (also
    written as the document title metadata).
    ``body`` is the raw text — whitespace + newlines preserved
    through the wrapper.
    ``footer_ref`` lands as a small italic footer line on every
    page. Typically a synthetic file reference like ``"LOAN-1003-A4"``.

    Returns the assembled PDF bytes — caller passes them straight
    into ``storage.put_object`` or writes them to a fixture path.
    """
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    page_w, page_h = LETTER
    usable_w = page_w - 2 * _MARGIN_X

    # Set the embedded document title metadata. Shows up in the PDF
    # viewer's title bar / tab name; nice touch for the in-app
    # preview when the file is opened.
    c.setTitle(title)
    c.setAuthor("Mkopo Lens")
    c.setSubject("Loan application document")

    lines = _wrap_text(c, body, usable_w)

    page_num = 1

    def _draw_header() -> None:
        c.setFont(_HEADER_FONT, _HEADER_SIZE)
        c.drawString(_MARGIN_X, page_h - _MARGIN_TOP + 0.15 * inch, title)
        # Thin separator line under the header — visually delimits
        # the chrome from the body.
        c.setLineWidth(0.4)
        c.setStrokeColorRGB(0.55, 0.55, 0.5)
        c.line(
            _MARGIN_X,
            page_h - _MARGIN_TOP - 0.08 * inch,
            page_w - _MARGIN_X,
            page_h - _MARGIN_TOP - 0.08 * inch,
        )

    def _draw_footer() -> None:
        if not footer_ref:
            return
        c.setFont(_FOOTER_FONT, _FOOTER_SIZE)
        c.setFillColorRGB(0.5, 0.5, 0.45)
        c.drawString(
            _MARGIN_X,
            _MARGIN_BOTTOM - 0.4 * inch,
            f"{footer_ref}  ·  Page {page_num}",
        )
        # Reset fill colour so the next page's body draws in black.
        c.setFillColorRGB(0, 0, 0)

    _draw_header()
    _draw_footer()
    c.setFont(_BODY_FONT, _BODY_SIZE)

    y = page_h - _MARGIN_TOP - 0.3 * inch
    for line in lines:
        if y < _MARGIN_BOTTOM:
            c.showPage()
            page_num += 1
            _draw_header()
            _draw_footer()
            c.setFont(_BODY_FONT, _BODY_SIZE)
            y = page_h - _MARGIN_TOP - 0.3 * inch
        if line == "":
            # Blank line = vertical break (one body leading worth).
            y -= _BODY_LEADING
            continue
        c.drawString(_MARGIN_X, y, line)
        y -= _BODY_LEADING

    c.save()
    return buf.getvalue()


def filename_for_pdf(original: str) -> str:
    """Map a fixture filename to its PDF equivalent.

    ``"loan_application.txt"`` → ``"loan_application.pdf"``. Anything
    that's already a non-text extension is returned unchanged so
    the seed can include images / native PDFs verbatim if it ever
    needs to.
    """
    if original.endswith(".txt"):
        return original[: -len(".txt")] + ".pdf"
    return original
