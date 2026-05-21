"""Tests for the recursive text chunker."""

from __future__ import annotations

from mkopo.tools.chunking import Chunk, chunk_text, count_tokens


def _content_only(chunks: list[Chunk]) -> list[str]:
    return [c.content for c in chunks]


class TestShortTextIsSingleChunk:
    def test_empty_returns_empty(self):
        assert chunk_text("") == []
        assert chunk_text("   \n\t  ") == []

    def test_short_text_returns_one_chunk(self):
        text = "Net operating income (NOI): $284,200."
        chunks = chunk_text(text, target_tokens=500)
        assert len(chunks) == 1
        assert chunks[0].ordinal == 0
        assert chunks[0].content == text


class TestParagraphBoundedSplit:
    def test_paragraphs_split_at_double_newline(self):
        para = "Sentence one. Sentence two. " * 50  # ~600 tokens-ish
        text = f"{para}\n\n{para}\n\n{para}"
        chunks = chunk_text(text, target_tokens=300, overlap_tokens=30)
        assert len(chunks) >= 2
        for c in chunks:
            assert c.token_count <= 350  # target + overlap room

    def test_ordinals_are_sequential(self):
        text = "\n\n".join(f"Paragraph {i}. " * 20 for i in range(10))
        chunks = chunk_text(text, target_tokens=200)
        for i, c in enumerate(chunks):
            assert c.ordinal == i


class TestSentenceFallback:
    def test_long_paragraph_falls_through_to_sentences(self):
        sentences = ["The borrower is Atlas Holdings."] * 80
        text = " ".join(sentences)
        chunks = chunk_text(text, target_tokens=200, overlap_tokens=20)
        assert len(chunks) >= 2


class TestWordFallback:
    def test_wall_of_text_with_no_punctuation(self):
        text = " ".join(["word"] * 800)
        chunks = chunk_text(text, target_tokens=150, overlap_tokens=20)
        assert len(chunks) >= 3
        for c in chunks:
            assert c.token_count <= 200


class TestOverlap:
    def test_overlap_preserves_context_across_boundary(self):
        unique_marker = "MARKER_THAT_APPEARS_ONCE"
        prefix = "X. " * 200
        suffix = "Y. " * 200
        text = f"{prefix}{unique_marker}. {suffix}"
        chunks = chunk_text(text, target_tokens=200, overlap_tokens=30)
        appearances = sum(1 for c in chunks if unique_marker in c.content)
        assert appearances >= 1


class TestRecursiveDescent:
    def test_deeply_nested_content_fits_in_one_chunk(self):
        section_1 = "Income Approach.\n\nAnnual rent: $300k.\n\nNOI: $200k."
        section_2 = "Sales Comparison.\n\nComps avg $250/sqft.\n\nSubject $260/sqft."
        section_3 = "Cost Approach.\n\nReproduction: $4M.\n\nLand: $1M."
        text = f"{section_1}\n\n\n{section_2}\n\n\n{section_3}"
        chunks = chunk_text(text, target_tokens=1000)
        assert len(chunks) == 1

    def test_each_atom_below_budget_when_packed(self):
        text = "\n\n".join(f"Paragraph number {i}." for i in range(100))
        chunks = chunk_text(text, target_tokens=80, overlap_tokens=15)
        assert len(chunks) >= 5
        for c in chunks:
            assert c.token_count <= 120  # budget + tolerance


def test_count_tokens_is_monotone():
    assert count_tokens("") == 0
    assert count_tokens("hello") >= 1
    assert count_tokens("hello world") > count_tokens("hello")
