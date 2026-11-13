"""BLAT / in-silico PCR data plane: parsers on recorded real kent output, input validation,
and the subprocess boundary (timeout, failure, garbage output, concurrency cap).

The parsers are fed the exact bytes ``gfClient -nohead`` and ``gfPcr`` produced against a
hg38 ``gfServer -stepSize=5`` on 2026-11-13 (tests/fixtures/blat/). The subprocess tests
drive the real asyncio path with tiny shell stand-ins for gfClient/gfPcr.
"""

from __future__ import annotations

import asyncio
import pathlib
import stat
import time

import pytest

from ucsc_link.blat import (
    BlatService,
    clean_primer,
    clean_sequence,
    parse_pcr_fasta,
    parse_psl,
)
from ucsc_link.config import BlatSettings
from ucsc_link.exceptions import (
    InvalidInputError,
    UpstreamSchemaError,
    UpstreamUnavailableError,
)

FX = pathlib.Path(__file__).parent / "fixtures" / "blat"
BRCA1_100 = (
    "AAAACAGCAGAACTTTCCTTAATGTCATTTTCAGCAAAACTAGTATCTTCCTTTATTTCACCATCATCTAACAGG"
    "TCATCAGGTGTCTCAGAACAAACCT"
)


# -- parsers on recorded real output ------------------------------------------------------


def test_psl_rows_become_hits_with_ucsc_score_and_identity() -> None:
    hits = parse_psl((FX / "hg38_brca1_exon.psl").read_text())
    exact, mismatched, reverse = hits
    assert exact["chrom"] == "chr17" and exact["start"] == 43092000 and exact["end"] == 43092100
    assert exact["display"] == "chr17:43,092,001-43,092,100" and exact["strand"] == "+"
    assert exact["identity"] == 100.0 and exact["score"] == 100
    assert exact["query_coverage"] == 100.0 and exact["block_count"] == 1
    # pslScore = matches + repMatches/2 - misMatches - gaps; 97 - 3 = 94.
    assert mismatched["score"] == 94 and mismatched["mismatches"] == 3
    assert mismatched["identity"] == 97.0
    assert reverse["strand"] == "-" and reverse["identity"] == 100.0


def test_spliced_psl_identity_ignores_target_introns() -> None:
    # Two 50-bp blocks 1 kb apart: an mRNA-style alignment (UCSC web isMrna=TRUE).
    row = "100\t0\t0\t0\t0\t0\t1\t1000\t+\tq\t100\t0\t100\tchr1\t5000\t1000\t2100\t2\t50,50,\t0,50,\t1000,2050,"
    (hit,) = parse_psl(row + "\n")
    assert hit["identity"] == 100.0 and hit["score"] == 99  # one target gap costs 1
    assert hit["blocks"] == [{"start": 1000, "end": 1050}, {"start": 2050, "end": 2100}]
    assert hit["span"] == 1100


def test_garbage_psl_is_an_upstream_schema_error() -> None:
    with pytest.raises(UpstreamSchemaError):
        parse_psl("not\ta\tpsl\n")


def test_pcr_fasta_becomes_products_with_primer_checks() -> None:
    (product,) = parse_pcr_fasta((FX / "hg38_htt_pcr.fa").read_text())
    assert product["chrom"] == "chr4" and product["start"] == 3074846
    assert product["end"] == 3075093 and product["size"] == 247
    assert product["display"] == "chr4:3,074,847-3,075,093" and product["strand"] == "+"
    assert product["sequence"].startswith("ATGAAGGCCTTCGAGTCCCTCAAGTCCTTCcagcag")
    assert len(product["sequence"]) == 247 and product["sequence_truncated"] is False
    assert product["forward_primer"] == {
        "sequence": "ATGAAGGCCTTCGAGTCCCTCAAGTCCTTC",
        "mismatches": 0,
    }
    assert product["reverse_primer"]["mismatches"] == 0


def test_long_pcr_product_sequence_is_bounded_honestly() -> None:
    body = "A" * 20 + "C" * 2960 + "T" * 20
    text = f">chr1:101+3100 3000bp {'A' * 20} {'A' * 20}\n{body}\n"
    (product,) = parse_pcr_fasta(text, max_sequence_bp=1000)
    assert product["size"] == 3000 and len(product["sequence"]) == 1000
    assert product["sequence_truncated"] is True


def test_garbage_pcr_output_is_an_upstream_schema_error() -> None:
    with pytest.raises(UpstreamSchemaError):
        parse_pcr_fasta(">nonsense header\nACGT\n")


# -- input validation (schema <=> runtime) -----------------------------------------------


def test_sequence_accepts_one_fasta_record_and_whitespace() -> None:
    raw = f">my query\n{BRCA1_100[:50]}\n  {BRCA1_100[50:].lower()}\n"
    assert clean_sequence(raw) == BRCA1_100


@pytest.mark.parametrize(
    "raw",
    [
        "ACGT" * 4,  # 16 bases < 20
        "A" * 25_001,
        f">a\n{BRCA1_100}\n>b\n{BRCA1_100}\n",  # two records
        BRCA1_100 + "R",  # IUPAC ambiguity other than N
        BRCA1_100 + "U",
        "",
    ],
)
def test_sequence_outside_the_contract_is_invalid_input(raw: str) -> None:
    with pytest.raises(InvalidInputError) as info:
        clean_sequence(raw)
    assert info.value.extra["field"] == "sequence"


def test_sequence_allows_n() -> None:
    assert clean_sequence("N" * 5 + BRCA1_100[:20]) == "NNNNN" + BRCA1_100[:20]


@pytest.mark.parametrize(
    "raw",
    ["ACGT" * 3, "A" * 51, "ACGTNACGTNACGTNACGTN", "-ACGTACGTACGTACGTA", "ACGTACGTACGTACGTA\n"],
)
def test_primer_outside_the_contract_is_invalid_input(raw: str) -> None:
    with pytest.raises(InvalidInputError) as info:
        clean_primer(raw, "forward_primer")
    assert info.value.extra["field"] == "forward_primer"


# -- the subprocess boundary --------------------------------------------------------------


def _layout(tmp_path: pathlib.Path, gf_client: str, gf_pcr: str = "exit 0") -> BlatSettings:
    """A data dir with stand-in kent binaries and an hg38 2bit placeholder."""
    bin_dir = tmp_path / "data" / "bin"
    bin_dir.mkdir(parents=True)
    for name, body in (("gfClient", gf_client), ("gfPcr", gf_pcr), ("gfServer", "exit 0")):
        exe = bin_dir / name
        exe.write_text(f"#!/bin/sh\n{body}\n")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "data" / "hg38").mkdir()
    (tmp_path / "data" / "hg38" / "hg38.2bit").write_bytes(b"x")
    return BlatSettings(
        servers={"hg38": "gfserver-hg38:8000"},
        data_dir=str(tmp_path / "data"),
        timeout_s=0.5,
        max_concurrency=2,
        tmp_dir=str(tmp_path),
    )


async def test_unconfigured_assembly_is_upstream_unavailable_naming_configured_ones() -> None:
    service = BlatService(BlatSettings())
    with pytest.raises(UpstreamUnavailableError) as info:
        await service.blat("hg38", BRCA1_100, 90, 10)
    assert info.value.retryable is False and info.value.extra["field"] == "assembly"
    assert info.value.extra["allowed_values"] == []


async def test_missing_data_on_disk_is_retryable_unavailable(tmp_path: pathlib.Path) -> None:
    settings = BlatSettings(servers={"hg38": "gfserver:8000"}, data_dir=str(tmp_path))
    with pytest.raises(UpstreamUnavailableError) as info:
        await BlatService(settings).blat("hg38", BRCA1_100, 90, 10)
    assert info.value.retryable is True


async def test_blat_runs_gfclient_and_parses_its_psl(tmp_path: pathlib.Path) -> None:
    psl = (FX / "hg38_brca1_exon.psl").read_text()
    # gfClient -nohead -minScore -minIdentity host port seqDir in.fa out.psl: out is $8.
    settings = _layout(tmp_path, f"cat > \"$8\" <<'EOF'\n{psl}EOF")
    out = await BlatService(settings).blat("GRCh38", BRCA1_100, 90, 2)
    assert out["assembly"] == "hg38" and out["assembly_requested"] == "GRCh38"
    assert out["total"] == 3 and out["returned"] == 2 and out["truncated"] is True
    assert out["results"][0]["score"] == 100  # sorted by score
    assert out["query"]["length"] == 100


async def test_subprocess_timeout_is_retryable_unavailable(tmp_path: pathlib.Path) -> None:
    settings = _layout(tmp_path, "sleep 5")
    started = time.monotonic()
    with pytest.raises(UpstreamUnavailableError) as info:
        await BlatService(settings).blat("hg38", BRCA1_100, 90, 10)
    assert info.value.retryable is True and time.monotonic() - started < 3


async def test_subprocess_failure_does_not_echo_stderr(tmp_path: pathlib.Path) -> None:
    settings = _layout(tmp_path, "echo 'secret /internal/path' >&2; exit 255")
    with pytest.raises(UpstreamUnavailableError) as info:
        await BlatService(settings).blat("hg38", BRCA1_100, 90, 10)
    assert "secret" not in info.value.message and "/internal" not in info.value.message


async def test_garbage_gfclient_output_is_schema_error(tmp_path: pathlib.Path) -> None:
    settings = _layout(tmp_path, 'echo "garbage" > "$8"')
    with pytest.raises(UpstreamSchemaError):
        await BlatService(settings).blat("hg38", BRCA1_100, 90, 10)


async def test_concurrency_is_capped(tmp_path: pathlib.Path) -> None:
    settings = _layout(tmp_path, 'sleep 0.2; : > "$8"')
    service = BlatService(settings)
    started = time.monotonic()
    results = await asyncio.gather(*(service.blat("hg38", BRCA1_100, 90, 10) for _ in range(4)))
    # 4 calls, 2 slots, 0.2 s each -> at least two waves.
    assert time.monotonic() - started >= 0.4
    assert all(r["total"] == 0 and r["results"] == [] for r in results)


async def test_pcr_runs_gfpcr_and_reports_an_honest_empty_result(tmp_path: pathlib.Path) -> None:
    settings = _layout(tmp_path, "exit 0", gf_pcr=': > "$7"')
    out = await BlatService(settings).pcr(
        "hg38", "ATGAAGGCCTTCGAGTCCCTCAAG", "CTCACGGTCGGTGCAGCGGC", 4000, 10
    )
    assert out["total"] == 0 and out["results"] == [] and out["truncated"] is False


async def test_pcr_parses_recorded_products(tmp_path: pathlib.Path) -> None:
    fa = (FX / "hg38_htt_pcr.fa").read_text()
    settings = _layout(tmp_path, "exit 0", gf_pcr=f"cat > \"$7\" <<'EOF'\n{fa}EOF")
    out = await BlatService(settings).pcr(
        "hg38", "ATGAAGGCCTTCGAGTCCCTCAAGTCCTTC", "CTCACGGTCGGTGCAGCGGC", 4000, 10
    )
    assert out["total"] == 1 and out["results"][0]["size"] == 247


def test_alt_and_fix_contigs_are_flagged_and_excluded_from_primary_total() -> None:
    text = (
        ">chr1_GL383519v1_alt:41124+41373 250bp ATAGGGAATCATGGTTCCCC CATGCTCTTTGCCTCAGAGG\n"
        + "ATAGGGAATCATGGTTCCCC"
        + "A" * 210
        + "CCTCTGAGGCAAAGAGCATG\n"
        ">chr1:155236101+155236350 250bp ATAGGGAATCATGGTTCCCC CATGCTCTTTGCCTCAGAGG\n"
        + "ATAGGGAATCATGGTTCCCC"
        + "A" * 210
        + "CCTCTGAGGCAAAGAGCATG\n"
    )
    alt, primary = parse_pcr_fasta(text)
    assert alt["alt_or_patch"] is True and primary["alt_or_patch"] is False
    row = "100\t0\t0\t0\t0\t0\t0\t0\t+\tq\t100\t0\t100\tchr17_KI270909v1_alt\t9\t0\t100\t1\t100,\t0,\t0,"
    assert parse_psl(row)[0]["alt_or_patch"] is True


async def test_payload_counts_products_on_the_primary_assembly(tmp_path: pathlib.Path) -> None:
    fa = (FX / "hg38_htt_pcr.fa").read_text()
    alt = fa.replace(">chr4:", ">chr4_GL000257v2_alt:")
    settings = _layout(tmp_path, "exit 0", gf_pcr=f"cat > \"$7\" <<'EOF'\n{alt}{fa}EOF")
    out = await BlatService(settings).pcr(
        "hg38", "ATGAAGGCCTTCGAGTCCCTCAAGTCCTTC", "CTCACGGTCGGTGCAGCGGC", 4000, 10
    )
    assert out["total"] == 2 and out["primary_total"] == 1
    # The primary-assembly product leads (and so drives next_commands), whatever gfPcr's order.
    assert out["results"][0]["alt_or_patch"] is False
