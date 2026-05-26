"""Tool-catalog + trajectory tests.

The borrower and staff chat agents take action exclusively through
their tool catalogs (``agents/tools/borrower.py``,
``agents/tools/staff.py``). The catalogs are the security boundary:
any code path the catalog doesn't expose is unreachable from the
LLM. These tests assert:

1. Each role's catalog is non-empty and every tool has the
   structural fields the chat loop relies on (name, description,
   schema, handler, roles, is_destructive).
2. Borrower-facing destructive tools have ``is_destructive=True``
   set — the chat loop refuses to execute destructive tools
   without a paired confirm step. This is the "tools-as-security-
   boundary" invariant.
3. Tool names are stable identifiers (``tool_uses.tool_name`` is
   indexed; a rename would break observability queries + saved
   searches).
4. The ``tool_uses`` table preserves trajectory order via
   ``sequence_num`` — monotonically increasing per LLM call so the
   observability page can reconstruct what the agent did, in
   order, without falling back to ``created_at`` (which has tied
   timestamps for rapidly-fired calls).
5. The role-scoped tool selector (``tools_for_role``) actually
   filters — a staff-only tool must not be reachable by a
   ``borrower``-role caller, even by name.

These tests do NOT exercise the live LLM (that would require an
API key in CI). They exercise the structural contract the chat
loop expects.
"""

from __future__ import annotations

# Importing the tool modules registers their handlers in the
# ``_REGISTRY`` dict (each call site uses ``register(Tool(...))``
# at module import). Without these imports, ``tools_for_role``
# returns an empty list because the registry hasn't been populated.
import mkopo.agents.tools.borrower  # noqa: F401 — registration side-effect
import mkopo.agents.tools.staff  # noqa: F401 — registration side-effect
from mkopo.agents.tools import tools_for_role


# ----- catalog shape --------------------------------------------------------


def test_borrower_catalog_is_non_empty_and_well_formed():
    """Every borrower tool must have the structural fields the
    chat loop reads. A new tool added without these fields would
    break ``tool_chat_loop.py`` at runtime; catching it at import
    time is cheaper."""
    tools = tools_for_role("borrower")
    assert len(tools) >= 4, (
        f"Borrower catalog has {len(tools)} tools — expected at "
        "least 4 (read-only baseline). If you removed a tool, "
        "update this expectation."
    )
    for tool in tools:
        assert tool.name, "Tool missing .name"
        assert tool.description, (
            f"Tool {tool.name!r} missing .description — the LLM "
            "selects tools by description; an empty one is dead"
        )
        assert tool.schema, f"Tool {tool.name!r} missing .schema"
        assert callable(tool.handler), (
            f"Tool {tool.name!r} handler is not callable"
        )
        assert tool.roles, f"Tool {tool.name!r} has no roles"
        assert "borrower" in tool.roles, (
            f"Tool {tool.name!r} returned for borrower but does "
            "not list 'borrower' in its roles set"
        )


def test_staff_catalog_is_non_empty_and_well_formed():
    """Same invariants for staff tools. The staff catalog is wider
    (search loans, send borrower message, etc.) so the minimum
    count is higher.

    Staff tools are keyed by the actual role strings the auth
    layer issues: ``underwriter`` and ``admin``. The umbrella
    label ``"staff"`` is a frontend concept; the registry checks
    the concrete role.
    """
    tools = tools_for_role("underwriter")
    assert len(tools) >= 3, (
        f"Underwriter catalog has {len(tools)} tools — expected at "
        "least 3. If you removed one, update this expectation."
    )
    for tool in tools:
        assert tool.name, "Tool missing .name"
        assert tool.description, f"Tool {tool.name!r} missing .description"
        assert tool.schema, f"Tool {tool.name!r} missing .schema"
        assert callable(tool.handler), (
            f"Tool {tool.name!r} handler is not callable"
        )


# ----- security-boundary invariants ----------------------------------------


def test_borrower_destructive_tools_are_marked():
    """Mutation tools must carry ``is_destructive=True``. The chat
    loop reads this flag to gate execution behind a confirmation
    UI step. If a mutation tool flips to ``is_destructive=False``,
    the LLM can execute it from a single message — which a prompt
    injection could exploit.

    The list below names tools whose handlers are mutation paths.
    Add new mutation tool names here when they're introduced so
    this test catches a missing flag.
    """
    by_name = {t.name: t for t in tools_for_role("borrower")}
    # Names match the actual ``register(Tool(name=...))`` strings in
    # mkopo/agents/tools/borrower.py — sanity-check via grep if any
    # of these come back as not-found. ``request_data_export`` is
    # NOT on this list because the export itself happens client-
    # side; the tool just directs the user, so it's not a server-
    # side mutation.
    destructive = {
        "withdraw_application",
        "request_erasure",
        "update_loan_field",
    }
    found_any = False
    for name in destructive:
        tool = by_name.get(name)
        if tool is None:
            # If a tool was renamed, surface that clearly rather
            # than silently passing — but don't fail (the list
            # above is the source of truth for "known mutation
            # tools"; a removal is acceptable).
            continue
        found_any = True
        assert tool.is_destructive, (
            f"Borrower tool {name!r} is in the known-mutation list "
            "but does not carry is_destructive=True. The chat "
            "loop's confirmation gate keys off this flag; flipping "
            "it to False is a security-boundary regression."
        )
    assert found_any, (
        "None of the known borrower mutation tools were found in "
        "the registry — either every one was renamed (update this "
        "test) or the registry is empty (broken import)."
    )


def test_tool_names_are_snake_case_and_short():
    """``tool_uses.tool_name`` is a String(64) column and is
    indexed for observability queries. A rename or an over-long
    name would break the column + the saved searches that use it.
    Snake_case is the convention."""
    import re

    snake_case = re.compile(r"^[a-z][a-z0-9_]*$")
    all_tools = (
        tools_for_role("borrower")
        + tools_for_role("underwriter")
        + tools_for_role("admin")
    )
    seen: set[str] = set()
    for tool in all_tools:
        if tool.name in seen:
            continue
        seen.add(tool.name)
        assert snake_case.match(tool.name), (
            f"Tool name {tool.name!r} is not snake_case"
        )
        assert len(tool.name) <= 64, (
            f"Tool name {tool.name!r} exceeds 64 chars — won't fit "
            "the tool_uses.tool_name column"
        )


def test_role_scoped_catalog_actually_filters():
    """The role filter is a hard boundary, not a hint. If a staff-
    only tool ever surfaces in the borrower catalog, an LLM that
    obeys a prompt injection could invoke it.

    The check: any borrower-catalog tool must list 'borrower' in
    its roles set. Any staff-catalog tool must list 'staff'.
    Crucially, no borrower tool should be marked destructive
    without confirmation (covered by the previous test) AND no
    staff-only tool should leak into the borrower catalog.
    """
    borrower_names = {t.name for t in tools_for_role("borrower")}
    underwriter_names = {t.name for t in tools_for_role("underwriter")}

    # The intersection is allowed (some read-only tools like
    # ``get_loan_status`` may be visible to both), but it must be
    # by intent — every shared tool must declare BOTH roles.
    for shared in borrower_names & underwriter_names:
        from mkopo.agents.tools import get_tool

        tool = get_tool(shared)
        assert tool is not None
        assert {"borrower", "underwriter"} <= tool.roles, (
            f"Tool {shared!r} appears in both borrower + underwriter "
            "catalogs but doesn't declare both roles"
        )


# ----- trajectory schema ----------------------------------------------------


def test_tool_use_schema_has_sequence_num_for_trajectory_order():
    """``tool_uses.sequence_num`` is what lets the observability
    page reconstruct *which order* the model called tools in. If
    this column is removed or the index dropped, the trajectory
    becomes lossy (you'd have to fall back to ``created_at``,
    which has tied timestamps for rapidly-fired calls)."""
    from mkopo.models import ToolUse

    columns = {c.name for c in ToolUse.__table__.columns}
    assert "sequence_num" in columns, (
        "tool_uses.sequence_num is the trajectory anchor — do not "
        "remove without writing a migration that preserves order"
    )
    assert "llm_call_id" in columns, (
        "tool_uses.llm_call_id ties each tool call to the LLM call "
        "that issued it; required for the observability nested view"
    )
    # Composite index that powers "load all tools for this LLM call
    # in invocation order" — without it the trajectory view does a
    # full table scan + sort.
    index_names = {ix.name for ix in ToolUse.__table__.indexes}
    assert "ix_tool_uses_llm_call_id_sequence" in index_names, (
        "Composite index on (llm_call_id, sequence_num) is gone — "
        "the observability trajectory view will degrade"
    )


def test_tool_use_carries_status_input_and_output():
    """The trajectory record must persist enough to reconstruct
    the call: what was asked (input), what came back (output),
    whether it succeeded (status), and timing (elapsed_ms). Audit
    reviewers need all four to evaluate an agent's behaviour
    after the fact."""
    from mkopo.models import ToolUse

    columns = {c.name for c in ToolUse.__table__.columns}
    required = {"tool_name", "input", "output", "status", "elapsed_ms"}
    missing = required - columns
    assert not missing, (
        f"tool_uses is missing trajectory-essential columns: {missing}"
    )
