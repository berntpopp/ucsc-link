"""Opt-in acceptance against a REAL hg38 gfServer (a deployment or a local one).

    UCSC_LINK_LIVE_BLAT=127.0.0.1:17779 UCSC_LINK_BLAT__DATA_DIR=/path/with/bin+hg38 \
        uv run pytest tests/integration/test_blat_live_hg38.py -m blat

Expectations were read from the hg38 reference (UCSC JSON API) on 2026-11-13.
"""

from __future__ import annotations

import os

import pytest

from ucsc_link.blat import BlatService
from ucsc_link.config import BlatSettings

pytestmark = pytest.mark.blat

ENDPOINT = os.environ.get("UCSC_LINK_LIVE_BLAT")
#: hg38 chr17:43,092,001-43,092,100 (0-based 43092000-43092100), inside BRCA1's large exon.
BRCA1_100 = (
    "AAAACAGCAGAACTTTCCTTAATGTCATTTTCAGCAAAACTAGTATCTTCCTTTATTTCACCATCATCTAACAGG"
    "TCATCAGGTGTCTCAGAACAAACCT"
)
HTT_FWD, HTT_REV = "ATGAAGGCCTTCGAGTCCCTCAAGTCCTTC", "CTCACGGTCGGTGCAGCGGC"


@pytest.fixture
def blat() -> BlatService:
    if not ENDPOINT:
        pytest.skip("set UCSC_LINK_LIVE_BLAT=host:port of an hg38 gfServer")
    return BlatService(
        BlatSettings(
            servers={"hg38": ENDPOINT},
            data_dir=os.environ.get("UCSC_LINK_BLAT__DATA_DIR", "/opt/blat"),
        )
    )


async def test_brca1_exon_sequence_maps_to_its_locus(blat: BlatService) -> None:
    top = (await blat.blat("hg38", BRCA1_100, 90, 5))["results"][0]
    assert (top["chrom"], top["start"], top["end"]) == ("chr17", 43092000, 43092100)
    assert top["identity"] == 100.0 and top["strand"] == "+"


async def test_sequence_with_mismatches_keeps_its_locus(blat: BlatService) -> None:
    seq = list(BRCA1_100)
    for i in (10, 40, 70):
        seq[i] = {"A": "C", "C": "G", "G": "T", "T": "A"}[seq[i]]
    top = (await blat.blat("hg38", "".join(seq), 90, 5))["results"][0]
    assert top["start"] == 43092000 and top["identity"] == 97.0 and top["mismatches"] == 3


async def test_htt_exon1_cag_primers_give_one_247_bp_product(blat: BlatService) -> None:
    out = await blat.pcr("hg38", HTT_FWD, HTT_REV, 4000, 10)
    assert out["total"] == 1
    product = out["results"][0]
    assert (product["chrom"], product["start"], product["end"]) == ("chr4", 3074846, 3075093)
    assert product["size"] == 247 and "cagcagcag" in product["sequence"]


async def test_primers_from_different_genes_give_no_product(blat: BlatService) -> None:
    out = await blat.pcr("hg38", HTT_FWD, "AGGTTTGTTCTGAGACACCTGATG", 4000, 10)
    assert out["total"] == 0 and out["results"] == []
