"""Sequence tools on the deployment's own gfServer: find_blat_alignments, predict_pcr_products."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field, StrictInt

from ucsc_link.blat import (
    DEFAULT_BLAT_LIMIT,
    DEFAULT_MAX_PRODUCT_BP,
    DEFAULT_MIN_IDENTITY,
    DEFAULT_PCR_LIMIT,
    MAX_BLAT_BP,
    MAX_BLAT_LIMIT,
    MAX_PCR_LIMIT,
    MAX_PRIMER_BP,
    MAX_PRODUCT_BP,
    MAX_PRODUCT_SEQUENCE_BP,
    MIN_BLAT_BP,
    MIN_PRIMER_BP,
)
from ucsc_link.mcp.annotations import READ_ONLY_OPEN_WORLD
from ucsc_link.mcp.envelope import McpErrorContext, ToolReturn, run_mcp_tool
from ucsc_link.mcp.next_commands import cmd
from ucsc_link.mcp.service_adapters import get_ucsc_service
from ucsc_link.tracks import MAX_FEATURE_WINDOW_BP, AssemblyArg

if TYPE_CHECKING:
    from fastmcp import FastMCP

_AVAILABILITY = (
    "Runs on this deployment's own UCSC gfServer; an assembly that is not deployed answers "
    "upstream_unavailable (get_diagnostics -> blat shows what is live). "
)
Assembly = Annotated[
    AssemblyArg,
    Field(description="Genome assembly (default hg38). Never lifted over."),
]
Primer = Annotated[
    str,
    Field(
        min_length=MIN_PRIMER_BP,
        max_length=MAX_PRIMER_BP,
        pattern=rf"^[ACGTacgt]{{{MIN_PRIMER_BP},{MAX_PRIMER_BP}}}$",
        description=f"{MIN_PRIMER_BP}-{MAX_PRIMER_BP} nt, A/C/G/T only, written 5'->3'.",
    ),
]


def _annotate(assembly: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gene/exon context for the best hit or product region."""
    return [
        cmd(
            "get_region_annotations",
            assembly=assembly,
            chrom=row["chrom"],
            start=row["start"],
            end=row["end"],
            track="ncbiRefSeqSelect",
        )
        for row in rows[:1]
        if row["end"] - row["start"] <= MAX_FEATURE_WINDOW_BP
    ] or [cmd("get_diagnostics")]


def register_blat_tools(mcp: FastMCP) -> None:
    """Register the BLAT and in-silico PCR tools on a FastMCP instance."""

    @mcp.tool(
        name="find_blat_alignments",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,
        tags={"sequence", "blat"},
        description=(
            "Align one DNA sequence to the genome with BLAT (as UCSC web BLAT): where does "
            "this sequence come from? Hits sorted by UCSC score, each with 0-based "
            "half-open start/end + 1-based display, strand, identity %, query_coverage %, "
            "blocks (several blocks = spliced, e.g. cDNA across exons). `total`/`truncated` "
            f"are honest. {_AVAILABILITY}"
            "Signature: find_blat_alignments(sequence, assembly=, min_identity=, limit=)."
        ),
    )
    async def find_blat_alignments(
        sequence: Annotated[
            str,
            Field(
                min_length=MIN_BLAT_BP,
                max_length=MAX_BLAT_BP + 5_000,
                pattern=r"^(>[^\n]*\n)?[ACGTNacgtn\s]+$",
                description=f"DNA, {MIN_BLAT_BP}-{MAX_BLAT_BP:,} bases of A/C/G/T/N (other "
                "IUPAC codes refused). Whitespace and one optional FASTA header line are "
                "ignored; exactly one sequence per call.",
                examples=[
                    "AAAACAGCAGAACTTTCCTTAATGTCATTTTCAGCAAAACTAGTATCTTCCTTTATTTCACCATCATC"
                    "TAACAGGTCATCAGGTGTCTCAGAACAAACCT"
                ],
            ),
        ],
        assembly: Assembly = "hg38",
        min_identity: Annotated[
            StrictInt,
            Field(
                ge=0,
                le=100,
                description=f"Drop hits below this identity % (default {DEFAULT_MIN_IDENTITY}, "
                "gfClient's default).",
            ),
        ] = DEFAULT_MIN_IDENTITY,
        limit: Annotated[
            StrictInt,
            Field(ge=1, le=MAX_BLAT_LIMIT, description=f"Max hits (default {DEFAULT_BLAT_LIMIT})."),
        ] = DEFAULT_BLAT_LIMIT,
    ) -> ToolReturn:
        args = {"assembly": assembly, "min_identity": min_identity, "limit": limit}

        async def call() -> dict[str, Any]:
            payload = await get_ucsc_service().blat.blat(assembly, sequence, min_identity, limit)
            payload["_meta"] = {"next_commands": _annotate(payload["assembly"], payload["results"])}
            return payload

        return await run_mcp_tool(
            "find_blat_alignments",
            call,
            context=McpErrorContext("find_blat_alignments", arguments=args),
        )

    @mcp.tool(
        name="predict_pcr_products",
        annotations=READ_ONLY_OPEN_WORLD,
        output_schema=None,
        tags={"sequence", "pcr"},
        description=(
            "In-silico PCR (as UCSC In-Silico PCR): every genomic product a primer pair "
            "amplifies up to max_product_size. Each product: location (0-based half-open + "
            "1-based display), size, strand, per-primer mismatch counts and the product "
            f"sequence (first {MAX_PRODUCT_SEQUENCE_BP:,} bp; primers uppercase). total == 1 "
            "means a single genomic product; total 0 means no product. Primers need >= 15 "
            f"perfect 3' bases. {_AVAILABILITY}"
            "Signature: predict_pcr_products(forward_primer, reverse_primer, assembly=, "
            "max_product_size=, limit=)."
        ),
    )
    async def predict_pcr_products(
        forward_primer: Annotated[Primer, Field(examples=["ATGAAGGCCTTCGAGTCCCTCAAGTCCTTC"])],
        reverse_primer: Annotated[Primer, Field(examples=["CTCACGGTCGGTGCAGCGGC"])],
        assembly: Assembly = "hg38",
        max_product_size: Annotated[
            StrictInt,
            Field(
                ge=50,
                le=MAX_PRODUCT_BP,
                description=f"Largest product in bp (default {DEFAULT_MAX_PRODUCT_BP:,}).",
            ),
        ] = DEFAULT_MAX_PRODUCT_BP,
        limit: Annotated[
            StrictInt,
            Field(
                ge=1, le=MAX_PCR_LIMIT, description=f"Max products (default {DEFAULT_PCR_LIMIT})."
            ),
        ] = DEFAULT_PCR_LIMIT,
    ) -> ToolReturn:
        args = {
            "forward_primer": forward_primer,
            "reverse_primer": reverse_primer,
            "assembly": assembly,
            "max_product_size": max_product_size,
            "limit": limit,
        }

        async def call() -> dict[str, Any]:
            payload = await get_ucsc_service().blat.pcr(
                assembly, forward_primer, reverse_primer, max_product_size, limit
            )
            payload["_meta"] = {"next_commands": _annotate(payload["assembly"], payload["results"])}
            return payload

        return await run_mcp_tool(
            "predict_pcr_products",
            call,
            context=McpErrorContext("predict_pcr_products", arguments=args),
        )
