"""Token-aware recursive text chunker for RAG.

Follows the well-known `RecursiveCharacterTextSplitter` pattern (LangChain).
Given an ordered list of separators and a target token size:

1. Try splitting on the highest-priority separator.
2. For each split piece, if it still exceeds `target_tokens`, recurse with
   the next separator down the list.
3. Pack the resulting atoms into chunks of at most `target_tokens`, with
   a small `overlap_tokens` tail carried into the next chunk so context
   doesn't get severed at boundaries.

Separator hierarchy chosen for loan documents:

    "\\n\\n\\n"  → top-level section divider
    "\\n\\n"    → paragraph
    ". "      → sentence
    "\\n"      → line (rent rolls, tabular data)
    " "       → word
    ""        → character (degenerate fallback)

For our synthetic ~300-token docs each becomes a single chunk; the
recursion + packing logic still runs so the pattern is real and ready
for actual PDFs.

Token counts use `cl100k_base` (the tokenizer shared across the
text-embedding-3 family). One tokenizer is loaded module-side and reused.
"""

from __future__ import annotations

from dataclasses import dataclass

import tiktoken

_TOKENIZER = tiktoken.get_encoding("cl100k_base")

DEFAULT_SEPARATORS: tuple[str, ...] = (
    "\n\n\n",
    "\n\n",
    ". ",
    "\n",
    " ",
    "",
)


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    content: str
    token_count: int


def count_tokens(text: str) -> int:
    return len(_TOKENIZER.encode(text))


def chunk_text(
    text: str,
    *,
    target_tokens: int = 500,
    overlap_tokens: int = 50,
    separators: tuple[str, ...] = DEFAULT_SEPARATORS,
) -> list[Chunk]:
    """Recursive chunk into ~target_tokens pieces with ~overlap_tokens overlap.

    `chunks[i].content` length is bounded by `target_tokens` (best-effort —
    a single un-splittable atom that exceeds target gets its own chunk).
    """
    text = text.strip()
    if not text:
        return []

    if count_tokens(text) <= target_tokens:
        return [Chunk(ordinal=0, content=text, token_count=count_tokens(text))]

    atoms = _recursive_split(text, separators, target_tokens)
    return _pack(atoms, target_tokens, overlap_tokens)


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


def _recursive_split(text: str, separators: tuple[str, ...], target_tokens: int) -> list[str]:
    """Split `text` into atoms, each ≤ target_tokens where possible.

    Walks the separator list from most- to least-preferred. For any split
    piece still over budget, recurses with the next separator.
    """
    if count_tokens(text) <= target_tokens:
        return [text]
    if not separators:
        # No separators left — fall back to a hard token split.
        return _hard_token_split(text, target_tokens)

    sep, rest = separators[0], separators[1:]
    if sep == "":
        return _hard_token_split(text, target_tokens)

    pieces = text.split(sep)
    # Re-attach the separator to non-last pieces so we don't lose it,
    # except for ". " (we keep the period, drop the trailing space).
    glued = _glue(pieces, sep)

    atoms: list[str] = []
    for piece in glued:
        if not piece.strip():
            continue
        if count_tokens(piece) <= target_tokens:
            atoms.append(piece)
        else:
            atoms.extend(_recursive_split(piece, rest, target_tokens))
    return atoms


def _glue(pieces: list[str], sep: str) -> list[str]:
    """Re-attach `sep` to each piece except possibly the last.

    Keeps reading natural: paragraphs end with their double-newline, sentences
    keep their period, etc.
    """
    if not pieces or sep == "":
        return pieces
    out: list[str] = []
    for i, p in enumerate(pieces):
        if i < len(pieces) - 1:
            out.append(p + sep)
        else:
            out.append(p)
    return out


def _hard_token_split(text: str, target_tokens: int) -> list[str]:
    """Last-resort split by raw token count. Always succeeds."""
    encoded = _TOKENIZER.encode(text)
    out: list[str] = []
    for start in range(0, len(encoded), target_tokens):
        out.append(_TOKENIZER.decode(encoded[start : start + target_tokens]))
    return out


def _pack(atoms: list[str], target_tokens: int, overlap_tokens: int) -> list[Chunk]:
    """Greedy-pack atoms into chunks ≤ target_tokens, with overlap tail."""
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_tokens = 0
    ordinal = 0
    for atom in atoms:
        atom_tokens = count_tokens(atom)
        if buf_tokens + atom_tokens > target_tokens and buf:
            content = "".join(buf).strip()
            chunks.append(
                Chunk(ordinal=ordinal, content=content, token_count=count_tokens(content))
            )
            ordinal += 1
            tail = _tail(content, overlap_tokens)
            buf = [tail] if tail else []
            buf_tokens = count_tokens(tail) if tail else 0
        buf.append(atom)
        buf_tokens += atom_tokens

    if buf:
        content = "".join(buf).strip()
        chunks.append(Chunk(ordinal=ordinal, content=content, token_count=count_tokens(content)))

    return chunks


def _tail(text: str, tokens: int) -> str:
    """Return the last `tokens` tokens of `text` as a string."""
    if tokens <= 0:
        return ""
    encoded = _TOKENIZER.encode(text)
    if len(encoded) <= tokens:
        return text
    return _TOKENIZER.decode(encoded[-tokens:])
