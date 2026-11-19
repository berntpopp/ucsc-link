"""Contract truth: what descriptions and error envelopes promise is what the wire carries."""

from __future__ import annotations

import re
from typing import Any

from fastmcp import Client

REGION = {"assembly": "hg38", "chrom": "chr17", "start": 43044294, "end": 43125483}


def _keys(obj: Any) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys(v)}
    return set()


async def test_backticked_fields_in_descriptions_are_returned(facade: Any, call_tool: Any) -> None:
    calls = {
        "search_genome": [{"assembly": "hg38", "query": "BRCA1"}],
        "get_region_annotations": [
            {**REGION, "track": "ncbiRefSeqSelect"},
            {**REGION, "track": "genomicSuperDups"},
        ],
    }
    async with Client(facade) as client:
        described = {t.name: t.description or "" for t in await client.list_tools()}
    for tool, arg_sets in calls.items():
        named = set(re.findall(r"`([a-z_]+)`", described[tool]))
        assert named, f"{tool}: no backticked fields -- the scan is broken"
        returned: set[str] = set()
        for args in arg_sets:
            returned |= _keys(await call_tool(facade, tool, args))
        assert named <= returned, f"{tool} describes but never returns {named - returned}"


async def test_search_without_exact_match_flags_weak_hits(facade: Any, call_tool: Any) -> None:
    out = await call_tool(facade, "search_genome", {"assembly": "hg38", "query": "breast cancer"})
    assert out["exact_matches"] == 0 and out["results"]
    assert all(hit["exact"] is False for hit in out["results"]) and "weak" in out["note"]
    steps = [s["tool"] for s in out["_meta"]["next_commands"]]
    assert steps == ["hgnc_resolve_symbol", "mondo_resolve_disease"]


async def test_exact_search_hit_is_flagged_and_has_no_weak_note(
    facade: Any, call_tool: Any
) -> None:
    out = await call_tool(facade, "search_genome", {"assembly": "hg38", "query": "BRCA1"})
    assert out["results"][0]["exact"] is True and "note" not in out


async def test_invalid_input_names_the_argument_in_field_errors(
    facade: Any, call_tool: Any
) -> None:
    bad_limit = await call_tool(
        facade, "search_genome", {"assembly": "hg38", "query": "BRCA1", "limit": 0}
    )
    inverted = await call_tool(facade, "get_reference_sequence", {**REGION, "end": 1})
    for env in (bad_limit, inverted):
        assert env["error_code"] == "invalid_input"
        assert env["field_errors"][0]["field"] == env["field"]
    assert bad_limit["field"] == "limit" and inverted["field"] == "end"
