"""Live UCSC JSON API checks (opt-in: UCSC_LINK_LIVE_INTEGRATION=1 make test-integration).

A handful of calls through the real rate limiter (1 request/s). Verifies the recorded-fixture
assumptions still hold upstream: allowlisted tracks exist, coordinates are 0-based, an
unknown chromosome is still answered with 200 + [] (hence the service-side check).
"""

from __future__ import annotations

import os

import pytest

from ucsc_link.api.client import UcscClient
from ucsc_link.config import UcscSettings
from ucsc_link.service import UcscService
from ucsc_link.tracks import TRACKS

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("UCSC_LINK_LIVE_INTEGRATION") != "1",
        reason="set UCSC_LINK_LIVE_INTEGRATION=1 to call the live UCSC API",
    ),
]


async def test_live_region_sequence_and_search() -> None:
    client = UcscClient(UcscSettings())
    service = UcscService(client)
    try:
        rmsk = await service.region_annotations("hg38", "chr4", 3074800, 3075000, "rmsk", 10)
        assert any(r["repName"] == "(CAG)n" for r in rmsk["results"])
        seq = await service.reference_sequence("hg38", "chr4", 3074876, 3074882, "+")
        assert seq["sequence"].lower() == "cagcag"
        hits = await service.search_genome("hg38", "BRCA1", 5)
        mane = [h for h in hits["results"] if h["track"] == "mane"]
        assert mane and mane[0]["start"] == 43044294
        raw = await client.get_json(
            "/getData/track",
            {"genome": "hg38", "track": "rmsk", "chrom": "chrZZ", "start": 0, "end": 100},
        )
        assert raw.get("rmsk") == []
    finally:
        await client.aclose()


async def test_live_allowlisted_tracks_exist_on_their_assemblies() -> None:
    client = UcscClient(UcscSettings())
    try:
        for spec in TRACKS.values():
            for assembly in spec.assemblies:
                data = await client.get_json(
                    "/list/schema", {"genome": assembly, "track": spec.track}
                )
                assert data.get("track") == spec.track, (assembly, spec.track)
    finally:
        await client.aclose()
