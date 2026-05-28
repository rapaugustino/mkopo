"""tool_call_accuracy — borrower-chat tool selection + argument correctness.

The borrower chat agent is the only LLM surface where a real
applicant can talk directly to the system. The agent's job is to
read the user's intent and dispatch to the right tool: ask about
status → ``get_loan_status``; "cancel my application" →
``withdraw_application``; "where do I send my W-2?" →
``list_missing_fields``. A bad tool choice here ranges from
annoying (LLM answers in prose instead of calling the tool) to
materially wrong (calls ``update_loan_field`` when the user only
asked a question).

Two scoring criteria — the standard pair for agent-trajectory
evaluation:

  1. **Trajectory Inclusion** — every expected tool name appears
     in the model's tool_use list. Extra tool calls don't fail the
     test (a chatty model that calls ``get_loan_status`` AND
     ``list_documents`` when asked about status is still useful);
     missing-an-expected-tool does.

  2. **Tool Argument Correctness** — for each expected tool, the
     arguments include the expected keys. We do key-match not
     value-match because the loan_id is parameterised on the user's
     session; the eval can't know which loan id the LLM should
     supply.

Score = AND of both criteria. Threshold 0.75 — chat agents are
trickier than structured-output tasks; we leave headroom for the
LLM to add a defensible extra tool call.

Implementation note: this task does NOT run the full chat agent
(that requires DB context + auth). It calls the gateway directly
with a mocked tool catalog and asks for a single-turn tool-use
response. That's enough to catch a regression in tool *selection*
— the harder property. Argument-value correctness in production
is gated by the tool itself (e.g. ``withdraw_application``
re-auth check), not by the prompt.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from evals.types import Example, TaskScore
from mkopo.config import get_settings
from mkopo.llm_gateway import get_gateway

# Mocked borrower-chat tool catalog. Mirrors the names + intent
# signatures of the production tools in
# ``mkopo/agents/tools/borrower.py``. If the production catalog
# grows, add entries here so the eval matches the deployed surface.
_TOOL_CATALOG = [
    {
        "name": "get_loan_status",
        "description": (
            "Return the current stage + status of the borrower's "
            "loan. Use for any 'where is my application' / 'is it "
            "approved' / 'what stage' question."
        ),
        "args": ["loan_id"],
    },
    {
        "name": "list_documents",
        "description": (
            "List documents the borrower has already uploaded. Use "
            "for 'what have I sent you' / 'do you have my W-2'."
        ),
        "args": ["loan_id"],
    },
    {
        "name": "list_missing_fields",
        "description": (
            "List underwriting fields still missing for the loan. "
            "Use for 'what do you still need' / 'what documents do "
            "I need to send' / 'why am I waiting'."
        ),
        "args": ["loan_id"],
    },
    {
        "name": "get_decision_reasoning",
        "description": (
            "Explain why a loan was approved / declined / "
            "conditioned. Use for 'why was I declined' / 'what's "
            "the reason for the conditions' questions on a "
            "decisioned loan."
        ),
        "args": ["loan_id"],
    },
    {
        "name": "update_loan_field",
        "description": (
            "Mutate a single field on the loan (address, phone, "
            "annual_income, etc.). DO NOT call for status / "
            "documents questions — only when the user explicitly "
            "asks to change something."
        ),
        "args": ["loan_id", "field", "value"],
    },
    {
        "name": "withdraw_application",
        "description": (
            "Permanently withdraw the application. Triggers a "
            "sensitive-op re-auth gate in production. Use ONLY "
            "when the user says 'cancel' / 'withdraw' / 'I no "
            "longer want this loan'."
        ),
        "args": ["loan_id"],
    },
]


SYSTEM = (
    "You are the borrower-facing assistant for Mkopo Lens, a loan "
    "origination system. The borrower has one open loan (loan_id "
    "is supplied separately by the runtime). Your job: read the "
    "borrower's message and decide which tool(s) to call.\n\n"
    "Rules:\n"
    "- Always include ``loan_id`` in tool calls; the runtime "
    "supplies the value, you supply the literal placeholder "
    "``{{loan_id}}`` for your structured output.\n"
    "- For mutating tools (``update_loan_field``, "
    "``withdraw_application``) only call when the user explicitly "
    "asks to change / cancel. Never call them for read-only "
    "questions.\n"
    "- Multiple tool calls are fine if the user asked compound "
    "questions. Single tool calls are preferable when one tool "
    "covers the intent.\n"
)


class _PlannedToolCall(BaseModel):
    tool_name: str = Field(
        description="Name of the tool to call. Must match the catalog exactly."
    )
    args: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments for the call. Use ``{{loan_id}}`` for the loan id placeholder.",
    )


class _PlannedResponse(BaseModel):
    """The eval doesn't actually execute tools — it just asks the
    LLM what it WOULD call. One-turn, no follow-up."""

    tool_calls: list[_PlannedToolCall] = Field(
        default_factory=list,
        description=(
            "Tools the agent would call to answer the user. Empty "
            "if no tool is appropriate (e.g. small-talk)."
        ),
    )


def _format_catalog() -> str:
    """Render the mocked tool catalog as a bulleted system-prompt
    fragment."""
    lines = []
    for t in _TOOL_CATALOG:
        args = ", ".join(t["args"])
        lines.append(
            f"- {t['name']}({args}): {t['description']}"
        )
    return "\n".join(lines)


class ToolCallAccuracyTask:
    """Trajectory inclusion + argument correctness on borrower-chat
    tool selection. Threshold 0.75."""

    name = "tool_call_accuracy"
    threshold = 0.75

    async def predict(self, example: Example) -> dict[str, Any]:
        settings = get_settings()
        user_msg = example.inputs["user_message"]
        user = (
            f"Available tools:\n{_format_catalog()}\n\n"
            f"Borrower message:\n\"\"\"\n{user_msg}\n\"\"\"\n\n"
            "Return the tool calls you would make. Single turn, no "
            "follow-up. Empty list if no tool is appropriate."
        )
        result: _PlannedResponse = await get_gateway().call_structured(
            model=settings.llm_default_model,
            system=SYSTEM,
            user=user,
            schema=_PlannedResponse,
        )
        return {
            "tool_calls": [
                {"tool_name": c.tool_name, "args": c.args}
                for c in result.tool_calls
            ],
        }

    def score(
        self, prediction: dict[str, Any], expected: dict[str, Any]
    ) -> TaskScore:
        predicted_calls: list[dict[str, Any]] = prediction.get(
            "tool_calls", []
        )
        predicted_names = [c.get("tool_name") for c in predicted_calls]

        expected_tools: list[dict[str, Any]] = expected.get(
            "expected_tools", []
        )
        forbidden_tools: list[str] = expected.get("forbidden_tools", [])

        # Criterion 1: trajectory inclusion — every expected tool
        # name appears at least once in predicted.
        missing = [
            t["name"] for t in expected_tools
            if t["name"] not in predicted_names
        ]
        trajectory_inclusion = len(missing) == 0

        # Criterion 1b (negative): the model didn't call any
        # forbidden tool. Forbidden lists catch the mutating-tool
        # mis-selection class (e.g. a "what's my status" question
        # that produced ``withdraw_application``).
        wrong = [
            t for t in predicted_names if t in forbidden_tools
        ]
        no_forbidden = len(wrong) == 0

        # Criterion 2: argument correctness — for each expected
        # tool, the predicted call's args include every expected
        # key. We don't check values (the runtime supplies loan_id,
        # and the test fixture's other arg values are illustrative).
        argument_correctness = True
        missing_args: dict[str, list[str]] = {}
        for exp in expected_tools:
            name = exp["name"]
            required_arg_keys: list[str] = exp.get("required_arg_keys", [])
            matched_call = next(
                (c for c in predicted_calls if c.get("tool_name") == name),
                None,
            )
            if matched_call is None:
                # Already caught by trajectory_inclusion; skip.
                continue
            args = matched_call.get("args") or {}
            missing_keys = [k for k in required_arg_keys if k not in args]
            if missing_keys:
                argument_correctness = False
                missing_args[name] = missing_keys

        criteria = [
            trajectory_inclusion,
            no_forbidden,
            argument_correctness,
        ]
        passed = all(criteria)
        return TaskScore(
            score=sum(criteria) / len(criteria),
            passed=passed,
            details={
                "trajectory_inclusion": trajectory_inclusion,
                "no_forbidden": no_forbidden,
                "argument_correctness": argument_correctness,
                "predicted_tools": predicted_names,
                "missing_expected": missing,
                "called_forbidden": wrong,
                "missing_args": missing_args,
            },
        )

    def aggregate(
        self,
        scores: list[TaskScore],
        examples: list[Example],
    ) -> dict[str, Any]:
        """Per-criterion pass rate across the fixture set + per-
        tool selection accuracy. Lets the dashboard show which
        property is failing when the overall score is < 1.0."""
        criteria = (
            "trajectory_inclusion",
            "no_forbidden",
            "argument_correctness",
        )
        n = len(scores)
        per_criterion: dict[str, dict[str, int | float]] = {}
        for c in criteria:
            passed = sum(1 for s in scores if s.details.get(c) is True)
            per_criterion[c] = {
                "n": n,
                "passed": passed,
                "rate": passed / n if n else 0.0,
            }
        # Per-tool: how often was each expected tool correctly
        # selected when it appeared in the fixture's expected list?
        per_tool: dict[str, dict[str, int]] = {}
        for s, ex in zip(scores, examples, strict=True):
            for exp in ex.expected.get("expected_tools", []):
                name = exp["name"]
                bucket = per_tool.setdefault(
                    name, {"n": 0, "selected": 0}
                )
                bucket["n"] += 1
                if name in (s.details.get("predicted_tools") or []):
                    bucket["selected"] += 1
        return {
            "per_criterion": per_criterion,
            "per_tool": {
                name: {
                    "n": b["n"],
                    "selected": b["selected"],
                    "rate": b["selected"] / b["n"] if b["n"] else 0.0,
                }
                for name, b in per_tool.items()
            },
        }
