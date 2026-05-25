"""Shared LangGraph checkpoint serializer.

Our intake / underwriting / decision graphs all keep typed Pydantic
models inside their state (``IntakeResult``, ``UnderwritingResult``,
``DecisionResult`` etc.). LangGraph serializes that state to msgpack
when checkpointing to Postgres and, on resume, has to import the
class to reconstruct the model.

Newer LangGraph versions are tightening that path — by default it now
warns on every deserialization of an unregistered module and is
scheduled to *block* unregistered modules in a future release. The
warning looks like this in our logs::

    Deserializing unregistered type mkopo.schemas.DecisionResult from
    checkpoint. This will be blocked in a future version. Set
    LANGGRAPH_STRICT_MSGPACK=true to block now, or add to
    allowed_msgpack_modules to allow explicitly.

The right fix is to pass an explicit allowlist of ``mkopo.schemas``
symbols to the checkpoint saver. That silences the warning today and
keeps us working when the default flips to strict.

We *don't* allowlist arbitrary modules (e.g. ``True``) because the
warning text spells out the security model — a write into the
checkpoint table could otherwise trigger code execution on resume.

Add new schemas to ``ALLOWED_SCHEMA_NAMES`` as agents start storing
them in state. Anything stored as a plain dict / list / int / str
already passes through without needing a registration.
"""

from __future__ import annotations

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

# Pydantic models that may appear inside agent state and therefore
# inside checkpoints. Keep this list short and intentional — every
# entry is a potential code-execution surface if the database is
# untrusted.
ALLOWED_SCHEMA_NAMES: tuple[str, ...] = (
    "UnderwritingResult",
    "UnderwritingSection",
    "UnderwritingKPIs",
    "RiskFlag",
    "DecisionResult",
    "TermSheet",
    "ConditionDraft",
    "AdverseActionLetter",
)


def make_serializer() -> JsonPlusSerializer:
    """Return a JsonPlusSerializer that allows our schemas on resume."""
    allowed = [("mkopo.schemas", name) for name in ALLOWED_SCHEMA_NAMES]
    return JsonPlusSerializer(allowed_msgpack_modules=allowed)
