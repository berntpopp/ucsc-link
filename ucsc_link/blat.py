"""Self-hosted BLAT and in-silico PCR over UCSC Kent ``gfServer`` (data plane).

UCSC's hosted hgBlat/hgPcr sit behind a human check, so a deployment runs its own
``gfServer`` per assembly (see docs/deployment.md). ``gfServer`` returns only coarse
tile hits; ``gfClient`` / ``gfPcr`` extend them against the same ``.2bit`` read locally,
so this process runs them as subprocesses: argument lists (never a shell), a strict
wall-clock timeout, a concurrency semaphore, and per-call scratch files. Kent's stderr
is logged (truncated), never echoed to the caller.

Scores follow UCSC's web BLAT exactly (kent ``pslScore`` / ``pslCalcMilliBad`` with
``isMrna=TRUE``)::

    score    = matches + repMatches // 2 - misMatches - qNumInsert - tNumInsert
    milliBad = 1000 * (misMatches + qNumInsert + round(3 * ln(1 + max(0, qSpan - tSpan))))
               // (matches + repMatches + misMatches)
    identity = 100 - milliBad / 10

Returns plain dicts with UCSC-native 0-based half-open coordinates plus 1-based ``display``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import os
import re
import signal
import tempfile
from asyncio.subprocess import DEVNULL, PIPE
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ucsc_link.config import BlatSettings
from ucsc_link.exceptions import InvalidInputError, UpstreamSchemaError, UpstreamUnavailableError
from ucsc_link.tracks import browser_url, display, resolve_assembly

logger = logging.getLogger(__name__)

MIN_BLAT_BP = 20
MAX_BLAT_BP = 25_000
MIN_PRIMER_BP = 15
MAX_PRIMER_BP = 50
DEFAULT_MAX_PRODUCT_BP = 4_000
MAX_PRODUCT_BP = 10_000
MAX_PRODUCT_SEQUENCE_BP = 1_000
DEFAULT_BLAT_LIMIT = 10
MAX_BLAT_LIMIT = 100
DEFAULT_PCR_LIMIT = 10
MAX_PCR_LIMIT = 50
DEFAULT_MIN_IDENTITY = 90
#: Lower than gfClient's default 30 so a 20-bp query can still report a perfect hit.
MIN_SCORE = 20
#: A result file larger than this is refused rather than parsed (tmpfs-bounded anyway).
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
KENT_LICENCE = (
    "BLAT/gfServer/gfClient/gfPcr (UCSC Kent tools) are free for academic, non-profit "
    "and personal use; commercial use requires a licence from Kent Informatics "
    "(https://kentinformatics.com). Binaries are fetched at deploy time, not shipped."
)
BLAT_CITATION = (
    "Kent WJ. BLAT--the BLAST-like alignment tool. Genome Res. 2002;12(4):656-64. PMID:11932250"
)

_HEADER = re.compile(r"^>[^\n]*\n")
_WHITESPACE = re.compile(r"\s+")
_DNA = re.compile(r"^[ACGTN]+$")
_PRIMER = re.compile(rf"^[ACGTacgt]{{{MIN_PRIMER_BP},{MAX_PRIMER_BP}}}$")
_PCR_HEADER = re.compile(r"^>(\S+):(\d+)([+-])(\d+) (\d+)bp ([ACGTacgt]+) ([ACGTacgt]+)$")
_CHROM = re.compile(r"^[A-Za-z0-9_.]{1,64}$")
_COMPLEMENT = str.maketrans("ACGTacgt", "TGCAtgca")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean_sequence(raw: str) -> str:
    """One DNA sequence (optionally one FASTA header line) -> uppercase bases.

    Whitespace and line breaks are ignored; only A/C/G/T/N are accepted; a second
    record is refused (exactly one query per call).
    """
    text = raw.strip()
    if text.startswith(">"):
        text = _HEADER.sub("", text + "\n", count=1)
    bases = _WHITESPACE.sub("", text).upper()
    if ">" in bases:
        raise InvalidInputError(
            "Give exactly one sequence; multi-record FASTA is not supported.", field="sequence"
        )
    if not _DNA.match(bases):
        raise InvalidInputError(
            "sequence must be DNA: A, C, G, T or N only (other IUPAC codes and U are refused).",
            field="sequence",
        )
    if not MIN_BLAT_BP <= len(bases) <= MAX_BLAT_BP:
        raise InvalidInputError(
            f"sequence has {len(bases):,} bases; BLAT accepts {MIN_BLAT_BP}-{MAX_BLAT_BP:,} "
            "(the UCSC web BLAT range).",
            field="sequence",
        )
    return bases


def clean_primer(raw: str, field: str) -> str:
    """A primer of 15-50 A/C/G/T bases (5'->3'), uppercased."""
    if not _PRIMER.match(raw):
        raise InvalidInputError(
            f"{field} must be {MIN_PRIMER_BP}-{MAX_PRIMER_BP} bases of A, C, G or T, 5'->3' "
            "(degenerate bases are not supported).",
            field=field,
        )
    return raw.upper()


def _ints(value: str) -> list[int]:
    return [int(x) for x in value.split(",") if x]


def parse_psl(text: str) -> list[dict[str, Any]]:
    """``gfClient -nohead`` PSL lines -> hits (UCSC web score and identity)."""
    hits = []
    for line in text.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        try:
            if len(cols) != 21 or not _CHROM.match(cols[13]) or cols[8] not in ("+", "-"):
                raise ValueError
            match, mis, rep, n_count, q_ins, _, t_ins, _ = (int(c) for c in cols[:8])
            q_size, q_start, q_end = int(cols[10]), int(cols[11]), int(cols[12])
            t_start, t_end = int(cols[15]), int(cols[16])
            sizes, t_starts = _ints(cols[18]), _ints(cols[20])
            blocks = [{"start": s, "end": s + n} for s, n in zip(t_starts, sizes, strict=True)]
        except ValueError:
            raise UpstreamSchemaError("gfClient output was not valid PSL.", field="psl") from None
        aligned = match + rep + mis
        size_dif = max(0, (q_end - q_start) - (t_end - t_start))
        milli_bad = (
            1000 * (mis + q_ins + round(3 * math.log(1 + size_dif))) // aligned if aligned else 0
        )
        chrom = cols[13]
        hits.append(
            {
                "chrom": chrom,
                "start": t_start,
                "end": t_end,
                "strand": cols[8],
                "display": display(chrom, t_start, t_end),
                "score": match + rep // 2 - mis - q_ins - t_ins,
                "identity": round(100 - milli_bad / 10, 1),
                "query_start": q_start,
                "query_end": q_end,
                "query_size": q_size,
                "query_coverage": round(100 * aligned / q_size, 1) if q_size else 0.0,
                "matches": match,
                "mismatches": mis,
                "rep_matches": rep,
                "n_count": n_count,
                "span": t_end - t_start,
                "block_count": len(blocks),
                "blocks": blocks,
            }
        )
    return hits


def _mismatches(expected: str, observed: str) -> int:
    return sum(a != b for a, b in zip(expected.upper(), observed.upper(), strict=True))


def parse_pcr_fasta(
    text: str, *, max_sequence_bp: int = MAX_PRODUCT_SEQUENCE_BP
) -> list[dict[str, Any]]:
    """``gfPcr`` FASTA (``>chrom:start{+,-}end Nbp fwd rev``) -> products."""
    products: list[dict[str, Any]] = []
    for record in text.split(">")[1:]:
        header, _, body = record.partition("\n")
        found = _PCR_HEADER.match(">" + header.strip())
        sequence = _WHITESPACE.sub("", body)
        if found is None or not _CHROM.match(found.group(1)):
            raise UpstreamSchemaError("gfPcr output was not valid FASTA.", field="products")
        chrom, first, strand, last = found.group(1, 2, 3, 4)
        start, end, size = int(first) - 1, int(last), int(found.group(5))
        fwd, rev = found.group(6).upper(), found.group(7).upper()
        if len(sequence) != size or size != end - start or size < len(fwd) + len(rev):
            raise UpstreamSchemaError("gfPcr output was inconsistent.", field="products")
        rev_site = sequence[-len(rev) :].translate(_COMPLEMENT)[::-1]
        products.append(
            {
                "chrom": chrom,
                "start": start,
                "end": end,
                "strand": strand,
                "display": display(chrom, start, end),
                "size": size,
                "forward_primer": {
                    "sequence": fwd,
                    "mismatches": _mismatches(fwd, sequence[: len(fwd)]),
                },
                "reverse_primer": {"sequence": rev, "mismatches": _mismatches(rev, rev_site)},
                "sequence": sequence[:max_sequence_bp],
                "sequence_truncated": size > max_sequence_bp,
            }
        )
    return products


class BlatService:
    """BLAT / isPcr against the configured per-assembly gfServers."""

    def __init__(self, settings: BlatSettings) -> None:
        """Bind settings and the process-wide subprocess slot semaphore."""
        self.settings = settings
        self._slots = asyncio.Semaphore(settings.max_concurrency)

    @property
    def configured(self) -> list[str]:
        """Assemblies with a gfServer endpoint configured."""
        return sorted(self.settings.servers)

    def _paths(self, assembly: str) -> tuple[str, str, Path]:
        """(host, port, seqDir) for an assembly, or a clear unavailability error."""
        endpoint = self.settings.servers.get(assembly)  # type: ignore[call-overload]
        if endpoint is None:
            raise UpstreamUnavailableError(
                f"BLAT/isPcr is not deployed for {assembly} on this server (configured: "
                f"{', '.join(self.configured) or 'none'}). Use UCSC's web BLAT/In-Silico PCR "
                "interactively instead.",
                retryable=False,
                recovery_action="switch_tool",
                field="assembly",
                allowed_values=self.configured,
            )
        host, port = str(endpoint).rsplit(":", 1)
        return host, port, Path(self.settings.data_dir) / assembly

    def _binary(self, name: str, seq_dir: Path, assembly: str) -> str:
        exe = Path(self.settings.data_dir) / "bin" / name
        if not exe.is_file() or not (seq_dir / f"{assembly}.2bit").is_file():
            raise UpstreamUnavailableError(
                "BLAT data is not on disk yet (first-run download still running or failed); "
                "retry in a few minutes or check get_diagnostics.",
                retryable=True,
            )
        return str(exe)

    async def _run(self, args: list[str], output: Path) -> str:
        """Run one kent client with the slot/timeout policy; return its output file."""
        async with self._slots:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=DEVNULL,
                stdout=PIPE,
                stderr=PIPE,
                env={},
                cwd=output.parent,
                start_new_session=True,  # own process group: a timeout kills every child
            )
            try:
                _, err = await asyncio.wait_for(proc.communicate(), self.settings.timeout_s)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
                raise UpstreamUnavailableError(
                    f"{Path(args[0]).name} did not finish within "
                    f"{self.settings.timeout_s:g} s; try a shorter query.",
                    retryable=True,
                ) from None
        if proc.returncode != 0:
            logger.warning("kent client failed", extra={"rc": proc.returncode, "stderr": err[:300]})
            raise UpstreamUnavailableError(
                "The BLAT server could not be reached or rejected the query (gfServer may "
                "still be loading the genome); retry shortly or check get_diagnostics.",
                retryable=True,
            )
        if not output.is_file() or output.stat().st_size > MAX_OUTPUT_BYTES:
            raise UpstreamSchemaError("BLAT produced no readable result.", field="output")
        return output.read_text(encoding="ascii", errors="replace")

    @staticmethod
    def _page(payload: dict[str, Any], rows: list[dict[str, Any]], limit: int) -> None:
        payload.update(
            results=rows[:limit],
            total=len(rows),
            returned=min(len(rows), limit),
            truncated=len(rows) > limit,
            coordinates="0-based half-open (display is 1-based)",
            retrieved_at=_now(),
        )

    async def blat(
        self, assembly: str, sequence: str, min_identity: int, limit: int
    ) -> dict[str, Any]:
        """Align one DNA sequence; hits sorted by UCSC score."""
        db = resolve_assembly(assembly)
        bases = clean_sequence(sequence)
        host, port, seq_dir = self._paths(db)
        exe = self._binary("gfClient", seq_dir, db)
        with tempfile.TemporaryDirectory(dir=self.settings.tmp_dir) as tmp:
            query, out = Path(tmp) / "query.fa", Path(tmp) / "out.psl"
            query.write_text(f">query\n{bases}\n", encoding="ascii")
            text = await self._run(
                [
                    exe,
                    "-nohead",
                    f"-minScore={MIN_SCORE}",
                    f"-minIdentity={min_identity}",
                    host,
                    port,
                    str(seq_dir),
                    str(query),
                    str(out),
                ],
                out,
            )
        hits = sorted(parse_psl(text), key=lambda h: (-h["score"], -h["identity"]))
        for hit in hits:
            hit["browser_url"] = browser_url(db, hit["chrom"], hit["start"], hit["end"])
        payload: dict[str, Any] = {"assembly": db}
        if assembly != db:
            payload["assembly_requested"] = assembly
        payload["query"] = {"length": len(bases), "min_identity": min_identity}
        self._page(payload, hits, limit)
        return payload

    async def pcr(
        self, assembly: str, forward: str, reverse: str, max_product_size: int, limit: int
    ) -> dict[str, Any]:
        """In-silico PCR: every product <= max_product_size the primer pair amplifies."""
        db = resolve_assembly(assembly)
        fwd = clean_primer(forward, "forward_primer")
        rev = clean_primer(reverse, "reverse_primer")
        host, port, seq_dir = self._paths(db)
        exe = self._binary("gfPcr", seq_dir, db)
        with tempfile.TemporaryDirectory(dir=self.settings.tmp_dir) as tmp:
            out = Path(tmp) / "products.fa"
            text = await self._run(
                [exe, f"-maxSize={max_product_size}", host, port, str(seq_dir), fwd, rev, str(out)],
                out,
            )
        products = parse_pcr_fasta(text)
        for product in products:
            product["browser_url"] = browser_url(
                db, product["chrom"], product["start"], product["end"]
            )
        payload: dict[str, Any] = {"assembly": db}
        if assembly != db:
            payload["assembly_requested"] = assembly
        payload["primers"] = {
            "forward": fwd,
            "reverse": rev,
            "max_product_size": max_product_size,
            "matching": "3' end needs >= 15 perfect bases; >= 2 matches per mismatch over the "
            "last 18 (gfPcr defaults, as UCSC In-Silico PCR)",
        }
        self._page(payload, products, limit)
        return payload

    async def status(self) -> dict[str, Any]:
        """Live per-assembly availability (data on disk + ``gfServer status``)."""
        report: dict[str, Any] = {}
        for db in self.configured:
            host, port, seq_dir = self._paths(db)
            exe = Path(self.settings.data_dir) / "bin" / "gfServer"
            data = exe.is_file() and (seq_dir / f"{db}.2bit").is_file()
            ready = False
            if data:
                proc = await asyncio.create_subprocess_exec(
                    str(exe), "status", host, port, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL
                )
                try:
                    ready = await asyncio.wait_for(proc.wait(), 5) == 0
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
            report[db] = {"data_on_disk": data, "server_ready": ready, "available": ready}
        return report
