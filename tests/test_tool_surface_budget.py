"""Regression guard for the Tool-Surface Budget Standard v1.

The wire tool definitions sit in the model's prompt prefix and are re-sent on every
request. This asserts ucsc-link's own advertised surface stays under budget:

* no tool publishes an ``outputSchema`` (Rule 3: dict envelopes keep
  ``structuredContent`` without one), and
* each tool definition stays under the per-tool ceiling and the whole surface under
  the per-server ceiling.

Token budgets are B1 = 1,200 tokens/tool and B2 = 10,000 tokens/server. Measurement
uses the router's byte-identical canonical ``surface`` core: default ``json.dumps``
framing for each tool and, critically, for the complete list.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastmcp import Client

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from surface import (
    MAX_SERVER_TOKENS,
    MAX_TOOL_TOKENS,
    is_array,
    properties,
    surface_metrics,
    tokens,
)


async def test_no_tool_publishes_output_schema(facade: Any) -> None:
    async with Client(facade) as client:
        tools = await client.list_tools()
    assert tools and all(t.outputSchema is None for t in tools)


async def test_tool_surface_within_budget(facade: Any) -> None:
    async with Client(facade) as client:
        tools = await client.list_tools()
    definitions = [tool.model_dump(exclude_none=True, by_alias=True, mode="json") for tool in tools]
    for tool in definitions:
        cost = tokens(tool)
        assert cost <= MAX_TOOL_TOKENS, (
            f"{tool['name']} definition is {cost} tokens (> B1 {MAX_TOOL_TOKENS})"
        )
    total = surface_metrics(definitions)["surface"]
    assert total <= MAX_SERVER_TOKENS, (
        f"total tool surface is {total} tokens (> B2 {MAX_SERVER_TOKENS})"
    )


async def test_tool_arguments_meet_canonical_schema_documentation_gate(facade: Any) -> None:
    """Apply router rules S1-S3 to the actual advertised input schemas."""
    async with Client(facade) as client:
        tools = await client.list_tools()
    failures: list[str] = []
    for model in tools:
        tool = model.model_dump(exclude_none=True, by_alias=True, mode="json")
        required = set((tool.get("inputSchema") or {}).get("required") or [])
        for name, prop in properties(tool).items():
            if not prop.get("description"):
                failures.append(f"S1 {tool['name']}.{name}")
            if name in required and not prop.get("examples"):
                failures.append(f"S2 {tool['name']}.{name}")
            if is_array(prop) and not prop.get("examples"):
                failures.append(f"S3 {tool['name']}.{name}")
    assert not failures, failures
