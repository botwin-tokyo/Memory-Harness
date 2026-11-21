from __future__ import annotations

import pytest

from memory_contract import CONVERSATIONAL_TOOLS, TOOLS, openai_tools, read_only_tools


@pytest.mark.asyncio
async def test_mcp_serves_the_contract_description_verbatim() -> None:
    'What the MCP server advertises IS the contract text, not a copy of it.'
    from memory_mcp.tools import mcp

    served = {t.name: t.description for t in await mcp.list_tools()}
    shared = ["recall", "explore", "search_library", "read_book_passage", "list_books"]

    for name in shared:
        assert name in served, f"{name} is not served by MCP at all"
        assert served[name] == TOOLS[name].description, (
            f"{name}: MCP and memory_contract have drifted.\n"
            f"  contract: {TOOLS[name].description[:80]}...\n"
            f"  mcp     : {served[name][:80]}..."
        )


def test_nothing_offered_mid_conversation_can_write() -> None:
    'The conversational loop is read-only, and that is asserted, not assumed.'
    for t in read_only_tools():
        assert t.read_only, f"{t.name} writes and must not be conversational"


def test_recall_is_not_offered_as_a_tool() -> None:
    'It is pre-fetched, so offering it invites a wasted round trip.'
    assert "recall" in TOOLS
    assert "recall" not in CONVERSATIONAL_TOOLS


def test_the_openai_payload_is_well_formed() -> None:
    'GLM will silently ignore a malformed tool rather than erroring.'
    for spec in openai_tools():
        assert spec["type"] == "function"
        fn = spec["function"]
        assert fn["name"] and fn["description"]
        params = fn["parameters"]
        assert params["type"] == "object"
        for req in params.get("required", []):
            assert req in params["properties"], f"{fn['name']}: required {req!r} not declared"
