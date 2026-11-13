"""End-to-end BLAT / in-silico PCR with the REAL pinned Kent binaries on a tiny genome.

``make test-blat`` fetches gfServer/gfClient/gfPcr/faToTwoBit through
``ucsc-link-blat-fetch`` (SHA-256 verified), builds a 2bit from the checked-in synthetic
``tests/fixtures/blat/tiny.fa`` (seeded random, 10 kb, no third-party sequence), starts a
real ``gfServer -stepSize=5`` and drives both MCP tools through it. The genome is filed
as "hg38" only so the production code path is exercised unchanged. CI runs this on every
push; without ``UCSC_LINK_KENT_BIN`` it skips.
"""

from __future__ import annotations

import os
import pathlib
import socket
import subprocess
import time
from collections.abc import Iterator
from typing import Any

import pytest

from ucsc_link.blat import BlatService
from ucsc_link.config import BlatSettings
from ucsc_link.service import UcscService

pytestmark = pytest.mark.blat

KENT = os.environ.get("UCSC_LINK_KENT_BIN")
FASTA = pathlib.Path(__file__).parents[1] / "fixtures" / "blat" / "tiny.fa"
_RC = str.maketrans("ACGT", "TGCA")


def _genome() -> dict[str, str]:
    chroms: dict[str, str] = {}
    for record in FASTA.read_text().split(">")[1:]:
        name, _, body = record.partition("\n")
        chroms[name.strip()] = body.replace("\n", "")
    return chroms


def _rc(seq: str) -> str:
    return seq.translate(_RC)[::-1]


@pytest.fixture(scope="module")
def blat(tmp_path_factory: pytest.TempPathFactory) -> Iterator[BlatService]:
    if not KENT:
        pytest.skip("set UCSC_LINK_KENT_BIN (make test-blat) to run the real Kent tools")
    kent = pathlib.Path(KENT).resolve()
    data = tmp_path_factory.mktemp("blat")
    (data / "bin").symlink_to(kent)
    (data / "hg38").mkdir()
    subprocess.run([kent / "faToTwoBit", FASTA, data / "hg38" / "hg38.2bit"], check=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = subprocess.Popen(
        [kent / "gfServer", "start", "127.0.0.1", str(port), "-stepSize=5", "hg38.2bit"],
        cwd=data / "hg38",
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            status = [kent / "gfServer", "status", "127.0.0.1", str(port)]
            if subprocess.run(status, capture_output=True, check=False).returncode == 0:
                break
            time.sleep(0.1)
        else:
            pytest.fail("gfServer did not become ready")
        yield BlatService(
            BlatSettings(
                servers={"hg38": f"127.0.0.1:{port}"},
                data_dir=str(data),
                timeout_s=30,
                tmp_dir=str(tmp_path_factory.mktemp("scratch")),
            )
        )
    finally:
        server.terminate()
        server.wait(timeout=10)


async def test_exact_query_maps_back_with_full_identity(blat: BlatService) -> None:
    query = _genome()["chrA"][1000:1120]
    out = await blat.blat("hg38", query, 90, 10)
    top = out["results"][0]
    assert (top["chrom"], top["start"], top["end"], top["strand"]) == ("chrA", 1000, 1120, "+")
    assert top["identity"] == 100.0 and top["score"] == 120 and top["query_coverage"] == 100.0


async def test_reverse_complement_maps_to_minus_strand(blat: BlatService) -> None:
    query = _rc(_genome()["chrA"][1000:1120])
    top = (await blat.blat("hg38", query, 90, 10))["results"][0]
    assert (top["start"], top["end"], top["strand"]) == (1000, 1120, "-")


async def test_mismatches_lower_identity_and_score(blat: BlatService) -> None:
    seq = list(_genome()["chrA"][1000:1120])
    for i in (20, 60, 100):
        seq[i] = "A" if seq[i] != "A" else "C"
    top = (await blat.blat("hg38", "".join(seq), 90, 10))["results"][0]
    assert top["mismatches"] == 3 and top["identity"] == 97.5 and top["score"] == 114


async def test_spliced_query_is_one_hit_with_two_blocks(blat: BlatService) -> None:
    chrom = _genome()["chrB"]
    top = (await blat.blat("hg38", chrom[1000:1060] + chrom[2500:2560], 90, 10))["results"][0]
    assert top["block_count"] == 2 and top["identity"] == 100.0
    assert top["blocks"] == [{"start": 1000, "end": 1060}, {"start": 2500, "end": 2560}]


async def test_pcr_product_location_size_and_primers(blat: BlatService) -> None:
    chrom = _genome()["chrB"]
    fwd, rev = chrom[500:522], _rc(chrom[798:820])
    out = await blat.pcr("hg38", fwd, rev, 4000, 10)
    assert out["total"] == 1
    product = out["results"][0]
    assert (product["chrom"], product["start"], product["end"]) == ("chrB", 500, 820)
    assert product["size"] == 320 and product["strand"] == "+"
    assert product["sequence"].upper() == chrom[500:820]
    assert product["forward_primer"]["mismatches"] == product["reverse_primer"]["mismatches"] == 0


async def test_swapped_primers_give_the_minus_strand_product(blat: BlatService) -> None:
    chrom = _genome()["chrB"]
    fwd, rev = chrom[500:522], _rc(chrom[798:820])
    product = (await blat.pcr("hg38", rev, fwd, 4000, 10))["results"][0]
    assert (product["start"], product["end"], product["strand"]) == (500, 820, "-")
    assert product["forward_primer"]["mismatches"] == product["reverse_primer"]["mismatches"] == 0


async def test_primers_on_different_chromosomes_give_no_product(blat: BlatService) -> None:
    genome = _genome()
    out = await blat.pcr("hg38", genome["chrA"][100:122], _rc(genome["chrB"][300:322]), 4000, 10)
    assert out["total"] == 0 and out["results"] == [] and out["truncated"] is False


async def test_tools_end_to_end(
    blat: BlatService, facade: Any, call_tool: Any, ucsc_service: UcscService
) -> None:
    ucsc_service.blat = blat
    query = _genome()["chrA"][3000:3200]
    out = await call_tool(facade, "find_blat_alignments", {"sequence": f">q\n{query}\n"})
    assert out["success"] is True and out["results"][0]["start"] == 3000
    diag = await call_tool(facade, "get_diagnostics", {})
    assert diag["blat"]["assemblies"]["hg38"] == {
        "data_on_disk": True,
        "server_ready": True,
        "available": True,
    }
