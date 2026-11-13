"""Curated UCSC track allowlist, assemblies, coordinate helpers and limits.

Only the tracks below can be queried (never arbitrary tracks or track hubs). Every
name was resolved against ``/list/tracks`` for hg38 and hg19 on 2026-11-13; none
carries UCSC's ``protectedData`` (restricted) flag. Two are non-commercial terms
(SpliceVarDB, Varaico) and are labelled as such: public hosting here does not imply a
right to redistribute them.

Coordinates: machine fields are UCSC-native **0-based, half-open** ``[start, end)``;
``display`` strings are 1-based, fully closed (``chr4:3,074,877-3,074,940``), the
form the Genome Browser shows. Assemblies are never lifted over.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ucsc_link.exceptions import InvalidInputError

Assembly = Literal["hg38", "hg19"]
AssemblyArg = Literal["hg38", "hg19", "GRCh38", "GRCh37"]
ASSEMBLY_ALIASES: dict[str, Assembly] = {
    "hg38": "hg38",
    "GRCh38": "hg38",
    "hg19": "hg19",
    "GRCh37": "hg19",
}

#: Region/record bounds. UCSC's own cap is 1,000,000 items; MCP needs far less.
MAX_FEATURE_WINDOW_BP = 2_000_000
MAX_SIGNAL_WINDOW_BP = 5_000
UPSTREAM_ITEM_CAP = 5_000
DEFAULT_LIMIT = 100
MAX_LIMIT = 1_000
MAX_SEQUENCE_BP = 10_000
DEFAULT_SEARCH_LIMIT = 5
MAX_SEARCH_LIMIT = 50
MAX_RESPONSE_CHARS = 80_000

UCSC_CITATION = (
    "Perez G, Barber GP, Benet-Pages A, et al. The UCSC Genome Browser database: "
    "2025 update. Nucleic Acids Res. 2025;53(D1):D1243-D1249. doi:10.1093/nar/gkae974"
)
UCSC_TERMS_URL = "https://genome.ucsc.edu/conditions.html"
OPEN_TERMS = (
    "Freely available for any use per UCSC Conditions of Use; cite UCSC and the "
    "original data provider."
)


@dataclass(frozen=True)
class TrackSpec:
    """One allowlisted track: what it is, where it exists, and how it may be used."""

    track: str
    label: str
    category: str
    kind: Literal["feature", "signal"]
    assemblies: dict[str, str]
    provider: str
    terms: str
    non_commercial: bool = False
    citation: str = UCSC_CITATION
    prose_fields: tuple[str, ...] = ()
    drop_fields: tuple[str, ...] = ("bin",)
    note: str | None = None
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def max_window_bp(self) -> int:
        """Widest interval a single call may request for this track."""
        return MAX_SIGNAL_WINDOW_BP if self.kind == "signal" else MAX_FEATURE_WINDOW_BP


TRACKS: dict[str, TrackSpec] = {
    spec.track: spec
    for spec in (
        TrackSpec(
            "knownGene",
            "GENCODE genes/transcripts",
            "genes",
            "feature",
            {"hg38": "GENCODE V50", "hg19": "GENCODE V50lift37"},
            "GENCODE (EMBL-EBI / Wellcome Sanger) via UCSC",
            OPEN_TERMS,
        ),
        TrackSpec(
            "ncbiRefSeqSelect",
            "RefSeq Select + MANE transcripts",
            "genes",
            "feature",
            {"hg38": "RefSeq Select and MANE", "hg19": "RefSeq Select and MANE"},
            "NCBI RefSeq via UCSC",
            OPEN_TERMS,
        ),
        TrackSpec(
            "rmsk",
            "RepeatMasker repeats",
            "repeats",
            "feature",
            {"hg38": "RepeatMasker", "hg19": "RepeatMasker"},
            "RepeatMasker (A. Smit) / Repbase-Dfam via UCSC",
            OPEN_TERMS,
        ),
        TrackSpec(
            "genomicSuperDups",
            "Segmental duplications (>=1 kb, >=90% identity)",
            "repeats",
            "feature",
            {"hg38": "Segmental Dups", "hg19": "Segmental Dups"},
            "Eichler lab (University of Washington) via UCSC",
            OPEN_TERMS,
            citation="Bailey JA, et al. Recent segmental duplications in the human genome. "
            "Science. 2002;297(5583):1003-7. PMID:12169732",
        ),
        TrackSpec(
            "phyloP100way",
            "PhyloP basewise conservation, 100 vertebrates",
            "conservation",
            "signal",
            {"hg38": "Cons 100 Verts (phyloP100way)"},
            "UCSC multiz100way / PHAST phyloP",
            OPEN_TERMS,
            note="hg38 name; on hg19 the same track is phyloP100wayAll (either name is accepted).",
        ),
        TrackSpec(
            "phyloP100wayAll",
            "PhyloP basewise conservation, 100 vertebrates",
            "conservation",
            "signal",
            {"hg19": "100 Vert. Cons (phyloP100wayAll)"},
            "UCSC multiz100way / PHAST phyloP",
            OPEN_TERMS,
            note="hg19 name; on hg38 the same track is phyloP100way (either name is accepted).",
        ),
        TrackSpec(
            "cCREregistry",
            "ENCODE4 candidate cis-regulatory elements (cCREs)",
            "regulation",
            "feature",
            {"hg38": "ENCODE4 cCREs (Registry v4, 2024)"},
            "ENCODE Consortium (SCREEN) via UCSC",
            OPEN_TERMS,
            citation="ENCODE Project Consortium, Moore JE, et al. Expanded encyclopaedias of "
            "DNA elements in the human and mouse genomes. Nature. 2020;583:699-710. "
            "PMID:32728249",
        ),
        TrackSpec(
            "encodeCcreCombined",
            "ENCODE3 cCREs (archival; prefer cCREregistry)",
            "regulation",
            "feature",
            {"hg38": "ENCODE3 cCREs"},
            "ENCODE Consortium via UCSC",
            OPEN_TERMS,
            note="Frozen ENCODE3 release kept for reproducibility; ENCODE4 is cCREregistry.",
            drop_fields=("bin", "description"),  # templated "<accession> <label>" duplicate
        ),
        TrackSpec(
            "spliceVarDb",
            "SpliceVarDB experimentally assayed splicing variants",
            "variants",
            "feature",
            {"hg38": "SpliceVarDB (Nov 2024)"},
            "SpliceVarDB (splicevardb.org) via UCSC",
            "Academic / non-commercial use (SpliceVarDB terms, CC BY-NC); link out to "
            "splicevardb.org for full records. Not cleared for commercial redistribution.",
            non_commercial=True,
            citation="Sullivan PJ, et al. SpliceVarDB: A comprehensive database of "
            "experimentally validated human splicing variants. Am J Hum Genet. "
            "2024;111(10):2164-2175. PMID:39226898",
        ),
        TrackSpec(
            "varaico",
            "Varaico literature-mined variants",
            "variants",
            "feature",
            {"hg38": "Varaico Variants", "hg19": "Varaico Variants"},
            "Varaico (varaico.com) via UCSC",
            "Some rights reserved: non-commercial use, distribution and reproduction with "
            "credit to the original authors (Varaico terms). Automated text mining; many "
            "variants may be mapped to unintended positions.",
            non_commercial=True,
            citation="Varaico, https://varaico.com (literature-mined variants; cite the "
            "original publications in each record).",
            prose_fields=("author", "title", "journal"),
            # Verbatim article snippets/mentions are dropped (the outlink has them).
            drop_fields=(
                "bin",
                "geneSnippets",
                "variantSnippets",
                "geneOrigStrs",
                "variantOrigStrs",
            ),
        ),
    )
}


def resolve_assembly(value: str) -> Assembly:
    """Map an accepted assembly name or alias to its UCSC database name."""
    try:
        return ASSEMBLY_ALIASES[value]
    except KeyError:
        raise InvalidInputError(
            "Unknown assembly. Use hg38 (GRCh38) or hg19 (GRCh37); assemblies are never "
            "lifted over.",
            field="assembly",
            allowed_values=sorted(ASSEMBLY_ALIASES),
        ) from None


#: Same conservation track, assembly-specific UCSC names: accept either on both.
TRACK_ALIASES: dict[tuple[str, str], str] = {
    ("hg19", "phyloP100way"): "phyloP100wayAll",
    ("hg38", "phyloP100wayAll"): "phyloP100way",
}


def track_for(assembly: Assembly, track: str) -> TrackSpec:
    """Return the allowlisted spec, or an invalid_input naming this assembly's tracks."""
    spec = TRACKS.get(TRACK_ALIASES.get((assembly, track), track))
    if spec is None or assembly not in spec.assemblies:
        available = [name for name, s in TRACKS.items() if assembly in s.assemblies]
        hint = f" {spec.note}" if spec is not None and spec.note else ""
        raise InvalidInputError(
            f"Track is not available on {assembly}.{hint} Tracks for {assembly}: "
            f"{', '.join(available)}.",
            field="track",
            allowed_values=available,
        )
    return spec


def normalize_chrom(chrom: str) -> str:
    """Accept ``chr17``/``17``/``X``/``MT``; return the UCSC ``chr``-prefixed name."""
    stem = chrom.strip()
    if stem.lower().startswith("chr"):
        stem = stem[3:]
    if stem.upper() in {"M", "MT"}:
        return "chrM"
    if stem.upper() in {"X", "Y"}:
        return "chr" + stem.upper()
    return "chr" + stem


def display(chrom: str, start: int, end: int) -> str:
    """1-based closed browser string for a 0-based half-open interval."""
    return f"{chrom}:{start + 1:,}-{end:,}"


def browser_url(assembly: str, chrom: str, start: int, end: int) -> str:
    """Genome Browser link for the interval (a link-out, never fetched here)."""
    return (
        f"https://genome.ucsc.edu/cgi-bin/hgTracks?db={assembly}&position={chrom}:{start + 1}-{end}"
    )


def description_url(assembly: str, track: str) -> str:
    """Track description page (attribution, methods, terms)."""
    return f"https://genome.ucsc.edu/cgi-bin/hgTrackUi?db={assembly}&g={track}"
