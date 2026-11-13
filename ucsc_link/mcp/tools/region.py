"""Data tools: list_tracks, search_genome, get_region_annotations, get_reference_sequence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field, StrictInt

from ucsc_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from ucsc_link.mcp.envelope import McpErrorContext, ToolReturn, run_mcp_tool
from ucsc_link.mcp.next_commands import cmd
from ucsc_link.mcp.service_adapters import get_ucsc_service
from ucsc_link.tracks import (
    DEFAULT_LIMIT,
    DEFAULT_SEARCH_LIMIT,
    MAX_FEATURE_WINDOW_BP,
    MAX_LIMIT,
    MAX_SEARCH_LIMIT,
    MAX_SEQUENCE_BP,
    MAX_SIGNAL_WINDOW_BP,
    AssemblyArg,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

TrackName = Literal[
    "knownGene",
    "ncbiRefSeqSelect",
    "rmsk",
    "genomicSuperDups",
    "phyloP100way",
    "phyloP100wayAll",
    "cCREregistry",
    "encodeCcreCombined",
    "spliceVarDb",
    "varaico",
]

Assembly = Annotated[
    AssemblyArg,
    Field(
        description="Genome assembly: hg38 (=GRCh38) or hg19 (=GRCh37). Never lifted over.",
        examples=["hg38"],
    ),
]
Chrom = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.]+$",
        description="Chromosome, UCSC style (chr17, chrX, chrM); '17'/'X'/'MT' are accepted.",
        examples=["chr4"],
    ),
]
Start = Annotated[
    StrictInt,
    Field(
        ge=0,
        description="0-based start (first base minus one). 1-based position P is start=P-1.",
        examples=[3074800],
    ),
]
End = Annotated[
    StrictInt,
    Field(
        ge=1,
        description="0-based exclusive end (= the 1-based last base). Must be > start.",
        examples=[3075000],
    ),
]


def _region_next(args: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Split a truncated window in two; otherwise offer the bases of a small window."""
    start, end = args["start"], args["end"]
    if payload.get("truncated") and end - start > 1:
        mid = start + (end - start) // 2
        return [
            cmd("get_region_annotations", **{**args, "end": mid}),
            cmd("get_region_annotations", **{**args, "start": mid}),
        ]
    steps: list[dict[str, Any]] = []
    if end - start <= MAX_SEQUENCE_BP:
        steps.append(
            cmd(
                "get_reference_sequence",
                assembly=args["assembly"],
                chrom=args["chrom"],
                start=start,
                end=end,
            )
        )
    steps.append(cmd("list_tracks", assembly=args["assembly"]))
    return steps


def register_region_tools(mcp: FastMCP) -> None:
    """Register the data tools on a FastMCP instance."""

    @mcp.tool(
        name="list_tracks",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,
        tags={"tracks"},
        description=(
            "List the curated UCSC tracks this server can query, with the assemblies each "
            "exists on, window limits, provider attribution and terms of use "
            "(non_commercial flags SpliceVarDB and Varaico). "
            "Signature: list_tracks(assembly=)."
        ),
    )
    async def list_tracks(
        assembly: Annotated[
            AssemblyArg | None,
            Field(description="Only tracks available on this assembly (default: all)."),
        ] = None,
    ) -> ToolReturn:
        async def call() -> dict[str, Any]:
            payload = get_ucsc_service().list_tracks(assembly)
            payload["_meta"] = {
                "next_commands": [cmd("search_genome", assembly=assembly or "hg38", query="BRCA1")]
            }
            return payload

        return await run_mcp_tool(
            "list_tracks",
            call,
            context=McpErrorContext("list_tracks", arguments={"assembly": assembly}),
        )

    @mcp.tool(
        name="search_genome",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,
        tags={"search"},
        description=(
            "Find where a gene or transcript lies on an assembly (UCSC position search "
            "over MANE, HGNC, GENCODE and RefSeq). Returns 0-based half-open start/end "
            "plus a 1-based display string, ready for get_region_annotations. UCSC search "
            "gives no strand: get_region_annotations(track='ncbiRefSeqSelect') does. "
            "Signature: search_genome(assembly, query, limit=)."
        ),
    )
    async def search_genome(
        assembly: Assembly,
        query: Annotated[
            str,
            Field(
                min_length=1,
                max_length=100,
                # No control/zero-width/bidi code points: UCSC redirects such queries away.
                pattern=r"^[^\x00-\x1f\x7f-\x9f\u200b-\u200d\u2060\u202a-\u202e\u2066-\u2069\ufeff]+$",
                description="Gene symbol, transcript accession or term, e.g. BRCA1, SHH, "
                "NM_007294.4, ENST00000357654.9.",
                examples=["BRCA1"],
            ),
        ],
        limit: Annotated[
            StrictInt,
            Field(
                ge=1,
                le=MAX_SEARCH_LIMIT,
                description=f"Max matches to return (default {DEFAULT_SEARCH_LIMIT}).",
            ),
        ] = DEFAULT_SEARCH_LIMIT,
    ) -> ToolReturn:
        args = {"assembly": assembly, "query": query, "limit": limit}

        async def call() -> dict[str, Any]:
            payload = await get_ucsc_service().search_genome(assembly, query, limit)
            steps = [
                cmd(
                    "get_region_annotations",
                    assembly=assembly,
                    chrom=hit["chrom"],
                    start=hit["start"],
                    end=hit["end"],
                    track="ncbiRefSeqSelect",
                )
                for hit in payload["results"][:1]
                if hit["end"] - hit["start"] <= MAX_FEATURE_WINDOW_BP
            ]
            payload["_meta"] = {"next_commands": steps or [cmd("list_tracks")]}
            return payload

        return await run_mcp_tool(
            "search_genome", call, context=McpErrorContext("search_genome", arguments=args)
        )

    @mcp.tool(
        name="get_region_annotations",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,
        tags={"region"},
        description=(
            "Return records of one allowlisted UCSC track overlapping a window "
            "[start, end) on an explicit assembly: repeats (rmsk), segmental duplications "
            "(genomicSuperDups), phyloP conservation, ENCODE cCREs, GENCODE/RefSeq "
            "transcripts, SpliceVarDB/Varaico variants. Native UCSC fields are kept and "
            "each record gains a normalized `position`. `total` is exact (null only past "
            f"the upstream item cap); `truncated` is honest. Windows: <= "
            f"{MAX_FEATURE_WINDOW_BP:,} bp, signal tracks <= {MAX_SIGNAL_WINDOW_BP:,} bp. "
            "Signature: get_region_annotations(assembly, chrom, start, end, track, limit=)."
        ),
    )
    async def get_region_annotations(
        assembly: Assembly,
        chrom: Chrom,
        start: Start,
        end: End,
        track: Annotated[
            TrackName,
            Field(
                description="UCSC track name from list_tracks. phyloP is phyloP100way on "
                "hg38 and phyloP100wayAll on hg19 (either name works on both); "
                "cCREregistry/encodeCcreCombined/spliceVarDb are hg38-only.",
                examples=["rmsk"],
            ),
        ],
        limit: Annotated[
            StrictInt,
            Field(
                ge=1,
                le=MAX_LIMIT,
                description=f"Max records to return (default {DEFAULT_LIMIT}).",
            ),
        ] = DEFAULT_LIMIT,
    ) -> ToolReturn:
        args = {
            "assembly": assembly,
            "chrom": chrom,
            "start": start,
            "end": end,
            "track": track,
            "limit": limit,
        }

        async def call() -> dict[str, Any]:
            payload = await get_ucsc_service().region_annotations(
                assembly, chrom, start, end, track, limit
            )
            payload["_meta"] = {"next_commands": _region_next(args, payload)}
            return payload

        return await run_mcp_tool(
            "get_region_annotations",
            call,
            context=McpErrorContext("get_region_annotations", arguments=args),
        )

    @mcp.tool(
        name="get_reference_sequence",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,
        tags={"sequence"},
        description=(
            f"Return reference DNA for [start, end) (<= {MAX_SEQUENCE_BP:,} bp) on an "
            "explicit assembly. Soft-masked: lowercase bases are repeat-masked. strand '-' "
            "returns the reverse complement. "
            "Signature: get_reference_sequence(assembly, chrom, start, end, strand=)."
        ),
    )
    async def get_reference_sequence(
        assembly: Assembly,
        chrom: Chrom,
        start: Start,
        end: End,
        strand: Annotated[
            Literal["+", "-"],
            Field(
                description="'+' for the reference strand (default), '-' for reverse complement."
            ),
        ] = "+",
    ) -> ToolReturn:
        args = {"assembly": assembly, "chrom": chrom, "start": start, "end": end, "strand": strand}

        async def call() -> dict[str, Any]:
            payload = await get_ucsc_service().reference_sequence(
                assembly, chrom, start, end, strand
            )
            payload["_meta"] = {
                "next_commands": [
                    cmd(
                        "get_region_annotations",
                        assembly=assembly,
                        chrom=chrom,
                        start=start,
                        end=end,
                        track="rmsk",
                    )
                ]
            }
            return payload

        return await run_mcp_tool(
            "get_reference_sequence",
            call,
            context=McpErrorContext("get_reference_sequence", arguments=args),
        )
