"""Response-Envelope Standard v1.2: provenance on every success, checked against the schema.

The schema is vendored from berntpopp/genefoundry-envelope, tag v1.2.0.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.test_blat import BRCA1_100, FX, _layout
from tests.test_blat_tools import HTT_FWD, HTT_REV
from ucsc_link.blat import BlatService
from ucsc_link.mcp.capabilities import TOOLS
from ucsc_link.service import UcscService

CONFORMANCE = pathlib.Path(__file__).parent / "conformance"
SCHEMA = Draft202012Validator(json.loads((CONFORMANCE / "envelope-v1.2.schema.json").read_text()))
REGION = {"assembly": "hg38", "chrom": "chr4", "start": 3074876, "end": 3074940}
#: One successful call per data tool, answered by the recorded fixtures.
DATA_CALLS: dict[str, dict[str, Any]] = {
    "list_tracks": {"assembly": "hg38"},
    "search_genome": {"assembly": "hg38", "query": "BRCA1"},
    "get_region_annotations": {**REGION, "track": "rmsk"},
    "get_reference_sequence": REGION,
    "find_blat_alignments": {"sequence": BRCA1_100, "assembly": "hg38"},
    "predict_pcr_products": {"forward_primer": HTT_FWD, "reverse_primer": HTT_REV},
}
OPS_TOOLS = {"get_server_capabilities", "get_diagnostics"}  # outside the v1.2 rule
SNAPSHOT_TIME = 1_700_000_000  # 2023-11-14T22:13:20Z


def _errors(envelope: Any) -> list[str]:
    return [f"{e.json_path}: {e.message}" for e in SCHEMA.iter_errors(envelope)]


@pytest.fixture
def blat(ucsc_service: UcscService, tmp_path: pathlib.Path) -> None:
    """Stand-in kent binaries that replay recorded output; the .2bit is dated."""
    psl, fa = (FX / "hg38_brca1_exon.psl").read_text(), (FX / "hg38_htt_pcr.fa").read_text()
    settings = _layout(
        tmp_path,
        f"cat > \"$8\" <<'EOF'\n{psl}EOF",
        gf_pcr=f"cat > \"$7\" <<'EOF'\n{fa}EOF",
    )
    os.utime(tmp_path / "data" / "hg38" / "hg38.2bit", (SNAPSHOT_TIME, SNAPSHOT_TIME))
    ucsc_service.blat = BlatService(settings)


def test_vendored_schema_is_the_v1_2_0_release() -> None:
    digest = hashlib.sha256((CONFORMANCE / "envelope-v1.2.schema.json").read_bytes()).hexdigest()
    assert digest == (CONFORMANCE / "envelope-v1.2.schema.sha256").read_text().strip()
    assert digest == "c3c47e20d7dd189aac2568fc963b5514d7a5ac2c3e5f21d126f42c96d1ad1ae3"


def test_every_data_tool_has_a_call() -> None:
    assert set(DATA_CALLS) == set(TOOLS) - OPS_TOOLS


@pytest.mark.parametrize("tool", DATA_CALLS)
async def test_data_tool_success_is_a_v1_2_envelope(
    tool: str, facade: Any, call_tool: Any, blat: None
) -> None:
    envelope = await call_tool(facade, tool, DATA_CALLS[tool])

    assert envelope["success"] is True, envelope
    assert _errors(envelope) == []
    meta = envelope["_meta"]
    assert meta["source"] == "ucsc"
    assert all(isinstance(meta[key], str) and meta[key] for key in ("data_version", "retrieved_at"))


async def test_error_is_a_valid_envelope(facade: Any, call_tool: Any) -> None:
    envelope = await call_tool(facade, "get_reference_sequence", {**REGION, "chrom": "chrNope"})
    assert envelope["success"] is False and _errors(envelope) == []


async def test_data_version_is_the_track_data_time_or_live(facade: Any, call_tool: Any) -> None:
    track = await call_tool(facade, "get_region_annotations", DATA_CALLS["get_region_annotations"])
    sequence = await call_tool(facade, "get_reference_sequence", REGION)
    assert track["_meta"]["data_version"] == track["data_time"] == "2022-10-18T23:39:32"
    assert sequence["_meta"]["data_version"] == "live"


async def test_a_cache_hit_keeps_the_time_of_the_fetch_that_filled_it(
    facade: Any, call_tool: Any, ucsc_service: UcscService
) -> None:
    await call_tool(facade, "get_reference_sequence", REGION)
    cache = ucsc_service.client._cache
    for key, (expiry, value, _) in list(cache.items()):
        cache[key] = (expiry, value, float(SNAPSHOT_TIME))
    requests = ucsc_service.client.upstream_requests

    again = await call_tool(facade, "get_reference_sequence", REGION)

    assert ucsc_service.client.upstream_requests == requests
    assert again["_meta"]["retrieved_at"] == "2023-11-14T22:13:20Z"
    assert again["result"]["retrieved_at"] == again["_meta"]["retrieved_at"]  # deprecated copy


async def test_blat_reports_the_assembly_and_the_snapshot_download_time(
    facade: Any, call_tool: Any, blat: None
) -> None:
    meta = (await call_tool(facade, "find_blat_alignments", DATA_CALLS["find_blat_alignments"]))[
        "_meta"
    ]
    assert (meta["data_version"], meta["retrieved_at"]) == ("hg38", "2023-11-14T22:13:20Z")
