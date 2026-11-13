"""Allowlist lookup, assembly aliases, chromosome naming and coordinate display."""

from __future__ import annotations

import pytest

from ucsc_link.exceptions import InvalidInputError
from ucsc_link.tracks import (
    TRACKS,
    display,
    normalize_chrom,
    resolve_assembly,
    track_for,
)


@pytest.mark.parametrize(
    ("alias", "db"), [("GRCh38", "hg38"), ("hg19", "hg19"), ("GRCh37", "hg19")]
)
def test_assembly_aliases_resolve_without_liftover(alias: str, db: str) -> None:
    assert resolve_assembly(alias) == db


def test_unknown_assembly_is_invalid_input() -> None:
    with pytest.raises(InvalidInputError) as info:
        resolve_assembly("hs1")
    assert info.value.extra["field"] == "assembly"


def test_track_missing_on_assembly_names_the_alternatives() -> None:
    with pytest.raises(InvalidInputError) as info:
        track_for("hg19", "phyloP100way")
    assert "phyloP100wayAll" in info.value.extra["allowed_values"]
    assert "phyloP100wayAll" in info.value.message


def test_non_allowlisted_track_is_rejected() -> None:
    with pytest.raises(InvalidInputError):
        track_for("hg38", "decipherSnvs")


def test_non_commercial_tracks_are_labelled() -> None:
    assert TRACKS["spliceVarDb"].non_commercial and TRACKS["varaico"].non_commercial
    assert not TRACKS["rmsk"].non_commercial


@pytest.mark.parametrize(
    ("raw", "chrom"),
    [("17", "chr17"), ("chr17", "chr17"), ("x", "chrX"), ("MT", "chrM"), ("chrM", "chrM")],
)
def test_normalize_chrom(raw: str, chrom: str) -> None:
    assert normalize_chrom(raw) == chrom


def test_display_converts_half_open_zero_based_to_one_based_closed() -> None:
    # UCSC item genoStart=3074876 genoEnd=3074940 covers bases 3,074,877..3,074,940.
    assert display("chr4", 3074876, 3074940) == "chr4:3,074,877-3,074,940"
    # A single base at 0-based 0 is base 1.
    assert display("chr1", 0, 1) == "chr1:1-1"
