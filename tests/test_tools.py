"""End-to-end MCP tool tests through an in-memory FastMCP client."""

from __future__ import annotations

from typing import Any

from fastmcp import Client

from ucsc_link.mcp.capabilities import TOOLS

HTT = {"assembly": "hg38", "chrom": "chr4", "start": 3074800, "end": 3075000, "track": "rmsk"}


async def _raw(mcp: Any, name: str, args: dict[str, Any]) -> Any:
    async with Client(mcp) as client:
        return await client.call_tool(name, args, raise_on_error=False)


async def test_registered_tools_match_capabilities_and_are_documented(facade: Any) -> None:
    async with Client(facade) as client:
        tools = await client.list_tools()
    assert [t.name for t in tools] == TOOLS
    for tool in tools:
        props = tool.inputSchema.get("properties", {})
        for name, prop in props.items():
            assert prop.get("description"), f"{tool.name}.{name} lacks a description"
        for name in tool.inputSchema.get("required", []):
            assert props[name].get("examples"), f"{tool.name}.{name} lacks examples"
        assert tool.description and "Signature:" in tool.description
        assert tool.annotations is not None and tool.annotations.readOnlyHint is True


async def test_region_annotations_success_envelope(facade: Any, call_tool: Any) -> None:
    out = await call_tool(facade, "get_region_annotations", HTT)
    assert out["success"] is True and out["returned"] == 2
    meta = out["_meta"]
    assert meta["tool"] == "get_region_annotations" and meta["source"] == "ucsc"
    assert meta["unsafe_for_clinical_use"] is True and "elapsed_ms" in meta
    assert meta["next_commands"][0]["tool"] == "get_reference_sequence"


async def test_truncated_region_suggests_split_windows(facade: Any, call_tool: Any) -> None:
    out = await call_tool(facade, "get_region_annotations", {**HTT, "limit": 1})
    assert out["truncated"] is True
    halves = [c["arguments"] for c in out["_meta"]["next_commands"][:2]]
    assert halves[0]["start"] == 3074800 and halves[0]["end"] == halves[1]["start"]
    assert halves[1]["end"] == 3075000


async def test_unknown_chromosome_is_an_is_error_not_found(facade: Any) -> None:
    res = await _raw(facade, "get_region_annotations", {**HTT, "chrom": "chrZZ"})
    assert res.is_error is True
    assert res.structured_content["error_code"] == "not_found"
    assert res.structured_content["field"] == "chrom"


async def test_out_of_enum_assembly_is_invalid_input(facade: Any) -> None:
    res = await _raw(facade, "get_region_annotations", {**HTT, "assembly": "hs1"})
    assert res.is_error is True
    assert res.structured_content["error_code"] == "invalid_input"
    assert "hg38" in res.structured_content["allowed_values"]


async def test_unknown_argument_names_the_valid_ones(facade: Any) -> None:
    res = await _raw(facade, "get_diagnostics", {"bogus": 1})
    assert res.is_error is True
    assert res.structured_content["error_code"] == "invalid_input"


async def test_track_not_on_assembly_lists_alternatives(facade: Any) -> None:
    res = await _raw(
        facade, "get_region_annotations", {**HTT, "assembly": "hg19", "track": "cCREregistry"}
    )
    assert res.is_error is True
    assert "phyloP100wayAll" in res.structured_content["allowed_values"]


async def test_search_next_step_uses_refseq_select_to_reveal_strand(
    facade: Any, call_tool: Any
) -> None:
    out = await call_tool(facade, "search_genome", {"assembly": "hg38", "query": "BRCA1"})
    step = out["_meta"]["next_commands"][0]
    assert step["tool"] == "get_region_annotations"
    assert step["arguments"]["track"] == "ncbiRefSeqSelect"


async def test_reference_sequence_tool(facade: Any, call_tool: Any) -> None:
    out = await call_tool(
        facade,
        "get_reference_sequence",
        {"assembly": "hg38", "chrom": "chr4", "start": 3074876, "end": 3074940},
    )
    assert out["success"] is True and out["strand"] == "+" and out["length"] == 64


async def test_search_genome_fences_prose_on_the_wire(facade: Any) -> None:
    res = await _raw(facade, "search_genome", {"assembly": "hg38", "query": "BRCA1"})
    assert res.is_error is False
    first = res.structured_content["results"][0]
    assert first["description"]["kind"] == "untrusted_text"
    assert '"kind": "untrusted_text"' in res.content[0].text or '"kind":"untrusted_text"' in (
        res.content[0].text
    )


async def test_list_tracks_and_capabilities(facade: Any, call_tool: Any) -> None:
    tracks = await call_tool(facade, "list_tracks", {"assembly": "hg38"})
    assert tracks["success"] is True and tracks["total"] == len(tracks["results"])
    caps = await call_tool(facade, "get_server_capabilities", {})
    assert caps["coordinates"]["machine"].startswith("0-based half-open")
    diag = await call_tool(facade, "get_diagnostics", {})
    assert diag["upstream"]["rate_limiter"]["daily_cap"] == 5000


def test_track_enum_matches_the_allowlist() -> None:
    from typing import get_args

    from ucsc_link.mcp.tools.region import TrackName
    from ucsc_link.tracks import TRACKS

    assert set(get_args(TrackName)) == set(TRACKS)


async def test_search_query_with_control_or_bidi_chars_is_invalid_input(facade: Any) -> None:
    # UCSC 302-redirects such queries off-origin; that must not look like an outage.
    res = await _raw(facade, "search_genome", {"assembly": "hg38", "query": "BRCA1‮X"})
    assert res.is_error is True
    assert res.structured_content["error_code"] == "invalid_input"
    assert res.structured_content["field"] == "query"


async def test_oversized_window_error_offers_a_clamped_retry(facade: Any) -> None:
    res = await _raw(facade, "get_region_annotations", {**HTT, "start": 0, "end": 50_000_000})
    assert res.is_error is True
    first = res.structured_content["_meta"]["next_commands"][0]
    assert first["tool"] == "get_region_annotations"
    assert first["arguments"]["start"] == 0 and first["arguments"]["end"] == 2_000_000
