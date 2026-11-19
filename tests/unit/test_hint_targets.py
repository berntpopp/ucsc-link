"""Guard: every tool/argument a response hint names must exist (fleet bench lesson).

Covers ``_meta.next_commands`` in the package source (``cmd("x", **kw)`` calls, AST
scan) and in emitted responses. Own tools are checked against the live input schemas;
fleet siblings carry their GeneFoundry-gateway name (``<namespace>_<tool>``) and are
checked against pinned parameter sets -- update the pin if a sibling renames.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from fastmcp import Client

import ucsc_link

_CROSS_SERVER: dict[str, set[str]] = {
    "hgnc_resolve_symbol": {"query", "response_mode"},
    "mondo_resolve_disease": {"query", "response_mode"},
}
REGION = {"assembly": "hg38", "chrom": "chr17", "start": 43044294, "end": 43044344}


async def _own(mcp: Any) -> dict[str, set[str]]:
    async with Client(mcp) as client:
        return {
            t.name: set(t.input_schema.get("properties", {})) for t in await client.list_tools()
        }


def _source_refs() -> list[tuple[str, set[str]]]:
    out: list[tuple[str, set[str]]] = []
    for path in Path(ucsc_link.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "cmd"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                out.append((node.args[0].value, {k.arg for k in node.keywords if k.arg}))
    return out


def _emitted(obj: Any) -> list[tuple[str, set[str]]]:
    steps = (obj.get("_meta") or {}).get("next_commands") or []
    return [(s["tool"], set(s["arguments"])) for s in steps]


def _assert_resolves(refs: list[tuple[str, set[str]]], own: dict[str, set[str]]) -> None:
    assert refs, "no hints collected -- the walk is broken"
    for tool, args in refs:
        known = own.get(tool, _CROSS_SERVER.get(tool))
        assert known is not None, f"hint names unknown tool {tool!r}"
        assert args <= known, f"{tool}: unknown argument(s) {sorted(args - known)}"


async def test_source_hint_templates_resolve(facade: Any) -> None:
    _assert_resolves(_source_refs(), await _own(facade))


async def test_emitted_hints_resolve(facade: Any, call_tool: Any) -> None:
    calls = [
        ("list_tracks", {}),
        ("get_server_capabilities", {}),
        ("get_diagnostics", {}),
        ("search_genome", {"assembly": "hg38", "query": "BRCA1"}),
        ("search_genome", {"assembly": "hg38", "query": "breast cancer"}),
        ("get_region_annotations", {**REGION, "track": "rmsk"}),
        ("get_region_annotations", {**REGION, "end": 45044294, "track": "rmsk"}),
        ("get_reference_sequence", REGION),
        ("get_region_annotations", {**REGION, "track": "nope"}),
    ]
    refs: list[tuple[str, set[str]]] = []
    for name, args in calls:
        refs += _emitted(await call_tool(facade, name, args))
    _assert_resolves(refs, await _own(facade))
