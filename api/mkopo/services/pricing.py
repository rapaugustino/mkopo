"""LLM pricing registry + cost calculator.

Maps each model identifier to a (input $/M tokens, output $/M tokens)
pair, and exposes :func:`compute_cost` to turn token counts into a
two-tuple of decimal dollars.

Why a separate module:

  - Prices change. Keeping them in one searchable file means an
    upstream rate change is a one-line PR.
  - The gateway shouldn't know about pricing — its job is calls.
    The cost computation is a side concern that runs after the
    call's token usage is known.
  - Other code (eval rollups, observability detail drawer) also
    needs to compute or look up cost; centralising prevents drift
    between "what the gateway recorded" and "what the rollup shows".

What's *not* here: per-tenant pricing tiers, volume discounts,
prompt-caching savings. If those land later, this module is the
natural home — keep the public API (``compute_cost``) stable.

Sources (Q1 2026 list prices, public docs):

  - Anthropic Claude — https://www.anthropic.com/pricing
  - OpenAI — https://openai.com/api/pricing
  - Embeddings — text-embedding-3-* line item
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ModelPricing:
    """Per-million-token rates in USD.

    ``input_per_1m`` and ``output_per_1m`` are both Decimal so the
    multiplication chain stays exact — float drift across a few
    thousand rolled-up rows is enough to make the dashboard total
    diverge from the manual receipt by visible cents.
    """

    input_per_1m: Decimal
    output_per_1m: Decimal


# Pricing as of Jan 2026 list prices. New model IDs added here pick
# up cost recording automatically on the next call.
PRICING: dict[str, ModelPricing] = {
    # --- Anthropic Claude (used by the agents) ---
    # Sonnet 4.5 — the default agent model.
    "claude-sonnet-4-5-20250929": ModelPricing(
        input_per_1m=Decimal("3.00"),
        output_per_1m=Decimal("15.00"),
    ),
    # Opus 4.5 — heavy model used for the underwriting summary +
    # the decision path selection.
    "claude-opus-4-5-20251115": ModelPricing(
        input_per_1m=Decimal("15.00"),
        output_per_1m=Decimal("75.00"),
    ),
    # Older snapshots — kept so historical rows compute correctly
    # against the rate that was in force when the call was made.
    # If a model is re-priced, add the new id; do not edit the old
    # row, or you'll retroactively rewrite history.
    "claude-sonnet-4-6": ModelPricing(
        input_per_1m=Decimal("3.00"),
        output_per_1m=Decimal("15.00"),
    ),
    "claude-opus-4-6": ModelPricing(
        input_per_1m=Decimal("15.00"),
        output_per_1m=Decimal("75.00"),
    ),
    # --- OpenAI (used for embeddings if configured) ---
    "text-embedding-3-small": ModelPricing(
        input_per_1m=Decimal("0.02"),
        # Embeddings are input-only; recording output cost as 0
        # keeps the schema uniform.
        output_per_1m=Decimal("0"),
    ),
    "text-embedding-3-large": ModelPricing(
        input_per_1m=Decimal("0.13"),
        output_per_1m=Decimal("0"),
    ),
}


_PER_MILLION = Decimal("1000000")


def compute_cost(
    *,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> tuple[Decimal | None, Decimal | None]:
    """Return ``(cost_input_usd, cost_output_usd)`` for one LLM call.

    Returns ``(None, None)`` when the model isn't in the registry
    (unknown name, third-party provider) or when token counts are
    missing (failed-before-completion calls). Callers persist these
    as nullable columns; the rollup queries filter on ``IS NOT NULL``.

    Splitting input vs output is the lever for tuning decisions:

    - Input-dominated cost ⇒ the prompt / context window is bloated.
      Trim retrieved chunks, shorten the system prompt, swap a
      smaller-context model.
    - Output-dominated cost ⇒ the model is over-explaining or the
      response schema is too verbose. Tighten the schema or set
      ``max_tokens`` lower.

    Combining the two upstream would hide which lever to pull.
    """
    rates = PRICING.get(model)
    if rates is None:
        return None, None
    in_cost: Decimal | None = None
    out_cost: Decimal | None = None
    if input_tokens is not None:
        in_cost = (Decimal(input_tokens) * rates.input_per_1m) / _PER_MILLION
    if output_tokens is not None:
        out_cost = (Decimal(output_tokens) * rates.output_per_1m) / _PER_MILLION
    return in_cost, out_cost


def known_models() -> list[str]:
    """Identifiers we have pricing for. Used by tests + the
    observability rollups that flag "uncosted calls" rows."""
    return sorted(PRICING.keys())
