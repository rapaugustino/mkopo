"""Tests for the LangGraph interrupt unwrapper in agents.streaming.

The unwrapper exists because LangGraph 1.x's ``astream(mode="updates")``
yields the ``__interrupt__`` payload in several shapes depending on the
release: a tuple/list of ``Interrupt`` objects, a single ``Interrupt``
instance, a dict that pre-serialises the wrapper, or — in test doubles —
the raw payload itself. The frontend's IntakeApprovalModal once crashed
because the old extractor only handled ``list`` (not ``tuple``) and let
the wrapper through to JSON, so the modal got ``[{value: …}]`` when it
expected ``{type: …, draft: …}``. These tests pin the regression.
"""

from __future__ import annotations

from mkopo.agents.streaming import _unwrap_interrupt


class _FakeInterrupt:
    """Stand-in for ``langgraph.types.Interrupt`` — only the ``.value``
    attribute is what the unwrapper looks at."""

    def __init__(self, value: object) -> None:
        self.value = value


PAYLOAD: dict[str, object] = {
    "type": "approve_email",
    "draft": {"subject": "Documents needed", "body_text": "Please send..."},
    "missing_fields": ["annual_noi"],
}


def test_unwraps_tuple_of_interrupts():
    # LangGraph 1.x newer releases yield this shape — the actual bug.
    assert _unwrap_interrupt((_FakeInterrupt(PAYLOAD),)) == PAYLOAD


def test_unwraps_list_of_interrupts():
    assert _unwrap_interrupt([_FakeInterrupt(PAYLOAD)]) == PAYLOAD


def test_unwraps_bare_interrupt():
    assert _unwrap_interrupt(_FakeInterrupt(PAYLOAD)) == PAYLOAD


def test_unwraps_dict_with_value_and_metadata():
    # Some serialisation paths surface this shape.
    wrapped = {"value": PAYLOAD, "id": "i-1", "ns": ["graph", "node"]}
    assert _unwrap_interrupt(wrapped) == PAYLOAD


def test_passes_through_raw_payload():
    # Test doubles + future LangGraph versions may emit the payload
    # directly. We must not unwrap a "value" key that's legitimately
    # in the payload itself.
    assert _unwrap_interrupt(PAYLOAD) == PAYLOAD


def test_does_not_strip_value_key_off_payload():
    # If the agent ever puts a "value" key in its payload but no
    # wrapper-like metadata, we should leave it alone.
    payload_with_value_key = {
        "type": "approve_email",
        "value": "do not strip me",
        "draft": {"subject": "x", "body_text": "y"},
    }
    assert _unwrap_interrupt(payload_with_value_key) == payload_with_value_key


def test_empty_sequence_returns_none():
    assert _unwrap_interrupt(()) is None
    assert _unwrap_interrupt([]) is None


def test_none_returns_none():
    assert _unwrap_interrupt(None) is None


def test_deeply_nested_unwraps_within_cap():
    # tuple → Interrupt → dict wrapper — three layers, well under the
    # paranoia cap.
    triple = (_FakeInterrupt({"value": PAYLOAD, "id": "i", "ns": []}),)
    assert _unwrap_interrupt(triple) == PAYLOAD
