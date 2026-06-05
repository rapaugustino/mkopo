"""LLM-call regression-diff endpoint.

Compares two ``llm_calls`` rows on the metadata we persist. We don't
store user prompts or response text (PII + size concerns), so the diff
is intentionally metadata-only: model match, latency delta, token
delta, cost delta, status / error match, same system-prompt hash.
That's what a regression check actually needs — "did the expensive
model creep back in?", "did latency spike on this prompt?" — rather
than full prompt diffs.

Drives the regression-diff card in ``LLMCallDrawer`` on the
observability surface.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from mkopo.deps import CurrentUserDep, DbSessionDep
from mkopo.models.eval import LLMCall

router = APIRouter()


class LLMDiffField(BaseModel):
    """One row of the side-by-side diff card.

    ``label`` is the human-readable field name. ``a`` / ``b`` are the
    formatted values for the two calls. ``delta`` is a short string
    describing the change (e.g. ``"+0.42s"``, ``"+$0.0012"``,
    ``"matches"``). ``flag`` is the visual cue:

    - ``"match"``       — values agree
    - ``"different"``   — values differ in a benign / informational way
    - ``"regression"``  — b is worse than a (slower / more expensive
                          / failed where a succeeded)
    - ``"improvement"`` — b is better than a
    """

    label: str
    a: str
    b: str
    delta: str
    flag: str


class LLMDiffResponse(BaseModel):
    a_id: str
    b_id: str
    fields: list[LLMDiffField]
    # Quick takeaway. Drives a one-liner in the UI like "B is 0.42s
    # slower and $0.0008 more expensive on the same prompt."
    summary: str


@router.get("/diff/llm-calls", response_model=LLMDiffResponse)
async def diff_llm_calls(
    a: uuid.UUID,
    b: uuid.UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
) -> LLMDiffResponse:
    """Compare two ``llm_calls`` rows on stored metadata.

    Used by the regression-diff card in ``LLMCallDrawer``. Returns
    one row per compared field plus a one-line summary; the
    frontend renders rows in a side-by-side table.
    """
    rows = (await db.execute(select(LLMCall).where(LLMCall.id.in_([a, b])))).scalars().all()
    by_id = {r.id: r for r in rows}
    row_a = by_id.get(a)
    row_b = by_id.get(b)
    if row_a is None or row_b is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or both LLM call ids not found.",
        )

    fields: list[LLMDiffField] = []

    # Model
    fields.append(
        LLMDiffField(
            label="Model",
            a=row_a.model,
            b=row_b.model,
            delta="matches" if row_a.model == row_b.model else "different",
            flag="match" if row_a.model == row_b.model else "different",
        )
    )

    # System prompt hash — fingerprint match means we were running the
    # exact same prompt body, which is the precondition for the diff
    # being meaningful in the first place.
    same_prompt = row_a.system_prompt_hash == row_b.system_prompt_hash
    fields.append(
        LLMDiffField(
            label="System prompt",
            a=row_a.system_prompt_hash[:12],
            b=row_b.system_prompt_hash[:12],
            delta="same hash" if same_prompt else "DIFFERENT prompt",
            flag="match" if same_prompt else "regression",
        )
    )

    # Status
    same_status = row_a.status == row_b.status
    flag_status = (
        "match"
        if same_status
        else "regression"
        if row_b.status != "ok" and row_a.status == "ok"
        else "improvement"
        if row_a.status != "ok" and row_b.status == "ok"
        else "different"
    )
    fields.append(
        LLMDiffField(
            label="Status",
            a=row_a.status,
            b=row_b.status,
            delta="matches" if same_status else f"{row_a.status} → {row_b.status}",
            flag=flag_status,
        )
    )

    # Latency
    dt_lat = row_b.elapsed_seconds - row_a.elapsed_seconds
    fields.append(
        LLMDiffField(
            label="Latency",
            a=f"{row_a.elapsed_seconds:.2f}s",
            b=f"{row_b.elapsed_seconds:.2f}s",
            delta=f"{dt_lat:+.2f}s",
            flag=("match" if abs(dt_lat) < 0.05 else "regression" if dt_lat > 0 else "improvement"),
        )
    )

    # Tokens (input + output). Each direction can move independently;
    # we render the deltas verbatim. Output tokens going up usually
    # means the model wrote more, which can be regression (more cost,
    # more latency) or improvement (richer answer).
    fields.append(_token_field("Input tokens", row_a.input_tokens, row_b.input_tokens))
    fields.append(_token_field("Output tokens", row_a.output_tokens, row_b.output_tokens))

    # Cost — sum of input + output split. Some legacy rows have null
    # cost (model not in the pricing registry); render "—" in that
    # case and skip the regression flag.
    cost_a = _total_cost(row_a)
    cost_b = _total_cost(row_b)
    if cost_a is None or cost_b is None:
        fields.append(
            LLMDiffField(
                label="Cost",
                a="—" if cost_a is None else f"${cost_a:.6f}",
                b="—" if cost_b is None else f"${cost_b:.6f}",
                delta="not priced",
                flag="different",
            )
        )
    else:
        dc = cost_b - cost_a
        fields.append(
            LLMDiffField(
                label="Cost",
                a=f"${cost_a:.6f}",
                b=f"${cost_b:.6f}",
                delta=f"{'+' if dc >= 0 else ''}${dc:.6f}",
                flag=("match" if abs(dc) < 0.000005 else "regression" if dc > 0 else "improvement"),
            )
        )

    # Attempt count — anything > 0 means schema retries fired. If
    # one row needed retries and the other didn't, that's a signal
    # the prompt's structured-output reliability changed.
    same_att = row_a.attempt == row_b.attempt
    fields.append(
        LLMDiffField(
            label="Attempts",
            a=str(row_a.attempt),
            b=str(row_b.attempt),
            delta="matches" if same_att else f"{row_a.attempt} → {row_b.attempt}",
            flag=(
                "match"
                if same_att
                else "regression"
                if row_b.attempt > row_a.attempt
                else "improvement"
            ),
        )
    )

    summary = _diff_summary(fields)
    return LLMDiffResponse(a_id=str(a), b_id=str(b), fields=fields, summary=summary)


# ----- helpers ------------------------------------------------------------


def _token_field(label: str, a_val: int | None, b_val: int | None) -> LLMDiffField:
    """Format a token-count delta row. Handles nulls (some legacy
    rows have ``None``) by rendering "—" without flagging."""
    if a_val is None or b_val is None:
        return LLMDiffField(
            label=label,
            a="—" if a_val is None else str(a_val),
            b="—" if b_val is None else str(b_val),
            delta="not recorded",
            flag="different",
        )
    d = b_val - a_val
    return LLMDiffField(
        label=label,
        a=str(a_val),
        b=str(b_val),
        delta=f"{d:+d}",
        flag=("match" if d == 0 else "regression" if d > 0 else "improvement"),
    )


def _total_cost(row: LLMCall) -> float | None:
    """Sum of input + output cost. Returns ``None`` if either
    side is null."""
    if row.cost_input_usd is None or row.cost_output_usd is None:
        return None
    return float(row.cost_input_usd) + float(row.cost_output_usd)


def _diff_summary(fields: list[LLMDiffField]) -> str:
    """One-line takeaway for the diff card.

    Counts regressions vs improvements vs matches and renders a
    short sentence. The frontend pairs this with the per-field
    table so the operator gets the gist + the detail.
    """
    regressions = sum(1 for f in fields if f.flag == "regression")
    improvements = sum(1 for f in fields if f.flag == "improvement")
    if regressions == 0 and improvements == 0:
        return "Calls agree on every recorded field."
    parts = []
    if regressions:
        parts.append(f"{regressions} regression{'' if regressions == 1 else 's'}")
    if improvements:
        parts.append(f"{improvements} improvement{'' if improvements == 1 else 's'}")
    return f"B vs A: {' · '.join(parts)}."
