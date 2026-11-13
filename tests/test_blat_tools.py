"""find_blat_alignments / predict_pcr_products through an in-memory FastMCP client.

Availability honesty (Contract-Truth): the tools are always advertised, so an
undeployed BLAT must answer a typed ``upstream_unavailable`` and capabilities /
diagnostics must say which assemblies are actually served.
"""

from __future__ import annotations

import pathlib
from typing import Any

from fastmcp import Client

from tests.test_blat import BRCA1_100, FX, _layout
from ucsc_link.blat import BlatService
from ucsc_link.service import UcscService

HTT_FWD, HTT_REV = "ATGAAGGCCTTCGAGTCCCTCAAGTCCTTC", "CTCACGGTCGGTGCAGCGGC"


async def _raw(mcp: Any, name: str, args: dict[str, Any]) -> Any:
    async with Client(mcp) as client:
        return await client.call_tool(name, args, raise_on_error=False)


async def test_undeployed_blat_is_an_honest_typed_error(facade: Any) -> None:
    res = await _raw(facade, "find_blat_alignments", {"sequence": BRCA1_100})
    assert res.is_error is True
    env = res.structured_content
    assert env["error_code"] == "upstream_unavailable" and env["retryable"] is False
    assert env["field"] == "assembly" and env["allowed_values"] == []
    assert env["_meta"]["next_commands"][0]["tool"] == "get_diagnostics"


async def test_capabilities_and_diagnostics_report_blat_availability(
    facade: Any, call_tool: Any
) -> None:
    caps = await call_tool(facade, "get_server_capabilities", {})
    assert caps["blat"]["configured_assemblies"] == []
    assert "commercial" in caps["blat"]["licence"]
    assert {"find_blat_alignments", "predict_pcr_products"} <= set(caps["tools"])
    diag = await call_tool(facade, "get_diagnostics", {})
    assert diag["blat"] == {"configured_assemblies": [], "assemblies": {}}


async def test_invalid_sequence_is_invalid_input_naming_the_field(facade: Any) -> None:
    res = await _raw(facade, "find_blat_alignments", {"sequence": "ACGTRYACGTACGTACGTACGT"})
    assert res.is_error is True
    assert res.structured_content["error_code"] == "invalid_input"
    assert res.structured_content["field"] == "sequence"


async def test_short_primer_is_invalid_input_naming_the_field(facade: Any) -> None:
    res = await _raw(
        facade, "predict_pcr_products", {"forward_primer": "ACGT", "reverse_primer": HTT_REV}
    )
    assert res.is_error is True
    assert res.structured_content["error_code"] == "invalid_input"
    assert res.structured_content["field"] == "forward_primer"


async def test_blat_hit_offers_the_gene_annotation_of_its_region(
    facade: Any, call_tool: Any, ucsc_service: UcscService, tmp_path: pathlib.Path
) -> None:
    psl = (FX / "hg38_brca1_exon.psl").read_text()
    ucsc_service.blat = BlatService(_layout(tmp_path, f"cat > \"$8\" <<'EOF'\n{psl}EOF"))
    out = await call_tool(
        facade, "find_blat_alignments", {"sequence": BRCA1_100, "assembly": "hg38"}
    )
    assert out["success"] is True and out["total"] == 3
    step = out["_meta"]["next_commands"][0]
    assert step == {
        "tool": "get_region_annotations",
        "arguments": {
            "assembly": "hg38",
            "chrom": "chr17",
            "start": 43092000,
            "end": 43092100,
            "track": "ncbiRefSeqSelect",
        },
    }


async def test_pcr_product_offers_the_gene_annotation_of_its_region(
    facade: Any, call_tool: Any, ucsc_service: UcscService, tmp_path: pathlib.Path
) -> None:
    fa = (FX / "hg38_htt_pcr.fa").read_text()
    ucsc_service.blat = BlatService(
        _layout(tmp_path, "exit 0", gf_pcr=f"cat > \"$7\" <<'EOF'\n{fa}EOF")
    )
    out = await call_tool(
        facade, "predict_pcr_products", {"forward_primer": HTT_FWD, "reverse_primer": HTT_REV}
    )
    assert out["success"] is True and out["results"][0]["size"] == 247
    step = out["_meta"]["next_commands"][0]
    assert step["tool"] == "get_region_annotations"
    assert step["arguments"]["start"] == 3074846 and step["arguments"]["end"] == 3075093


async def test_too_short_sequence_error_states_the_length_contract(facade: Any) -> None:
    res = await _raw(facade, "find_blat_alignments", {"sequence": "ACGTACGT"})
    env = res.structured_content
    assert env["error_code"] == "invalid_input" and env["field"] == "sequence"
    assert "20" in env["message"] and "expects a string" not in env["message"]
    assert env["allowed_values"] == ["20..30000 characters"]
