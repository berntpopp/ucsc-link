"""UCSC data plane: validate regions, query allowlisted tracks, shape honest payloads.

Returns plain dicts and raises typed :mod:`ucsc_link.exceptions` errors; the MCP
envelope owns ``success``/``_meta``. Distinguishes an empty interval (success,
``total: 0``) from an unknown chromosome (``not_found`` -- UCSC itself answers 200 with
no rows for ``chrZZ``), a non-allowlisted or unavailable track (``invalid_input`` /
``not_found``) and an upstream failure (``upstream_unavailable`` / ``rate_limited``).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote

from ucsc_link.api.client import UcscClient
from ucsc_link.blat import BlatService
from ucsc_link.config import BlatSettings
from ucsc_link.exceptions import InvalidInputError, NotFoundError, UpstreamSchemaError
from ucsc_link.mcp.untrusted_content import DEFAULT_MAX_OBJECTS as MAX_UNTRUSTED_OBJECTS
from ucsc_link.mcp.untrusted_content import fence_untrusted_text
from ucsc_link.tracks import (
    MAX_RESPONSE_CHARS,
    MAX_SEQUENCE_BP,
    TRACKS,
    UPSTREAM_ITEM_CAP,
    Assembly,
    TrackSpec,
    browser_url,
    description_url,
    display,
    normalize_chrom,
    resolve_assembly,
    track_for,
)

_POSITION_KEYS = (("chromStart", "chromEnd"), ("txStart", "txEnd"), ("genoStart", "genoEnd"))
_NUCLEOTIDES = re.compile(r"^[ACGTNacgtn]*$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
_POSITION = re.compile(r"^(chr[0-9A-Za-z_]+):(\d+)-(\d+)$")
#: Curated position-search tracks (genes only; never public hubs or help docs).
SEARCH_TRACKS = ("mane", "hgnc", "knownGene", "ncbiRefSeqSelect", "ncbiRefSeqCurated")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _attribution(spec: TrackSpec, assembly: str) -> dict[str, Any]:
    return {
        "provider": spec.provider,
        "terms": spec.terms,
        "non_commercial": spec.non_commercial,
        "citation": spec.citation,
        "description_url": description_url(assembly, spec.track),
    }


def track_summary(spec: TrackSpec, assembly: str | None = None) -> dict[str, Any]:
    """The allowlist entry as served by list_tracks and region payloads."""
    summary: dict[str, Any] = {
        "track": spec.track,
        "label": spec.label,
        "category": spec.category,
        "kind": spec.kind,
        "assemblies": dict(spec.assemblies),
        "max_window_bp": spec.max_window_bp,
        "attribution": _attribution(spec, assembly or next(iter(spec.assemblies))),
    }
    if spec.note:
        summary["note"] = spec.note
    return summary


def _record_position(record: dict[str, Any], fallback_chrom: str) -> dict[str, Any] | None:
    """Normalized 0-based half-open position of one native UCSC record."""
    for start_key, end_key in (*_POSITION_KEYS, ("start", "end")):
        start, end = record.get(start_key), record.get(end_key)
        if isinstance(start, int) and isinstance(end, int):
            chrom = str(record.get("chrom") or record.get("genoName") or fallback_chrom)
            position: dict[str, Any] = {"chrom": chrom, "start": start, "end": end}
            if record.get("strand") in ("+", "-"):
                position["strand"] = record["strand"]
            position["display"] = display(chrom, start, end)
            return position
    return None


def _ints(value: Any) -> list[int]:
    return [int(x) for x in str(value).split(",") if x]


def _exons(record: dict[str, Any]) -> list[dict[str, int]] | None:
    """genePred exonStarts/exonEnds (or bigGenePred blocks) as exons in transcript order."""
    try:
        if "exonStarts" in record:
            starts, ends = _ints(record["exonStarts"]), _ints(record["exonEnds"])
        else:  # bigGenePred/bed12: block offsets relative to chromStart
            origin = int(record["chromStart"])
            starts = [origin + offset for offset in _ints(record["chromStarts"])]
            ends = [s + size for s, size in zip(starts, _ints(record["blockSizes"]), strict=True)]
    except KeyError, ValueError, TypeError:
        return None
    if not starts or len(starts) != len(ends):
        return None
    pairs = list(zip(starts, ends, strict=True))
    if record.get("strand") == "-":
        pairs.reverse()  # exon 1 is the transcript's 5' end
    return [{"exon": n, "start": s, "end": e} for n, (s, e) in enumerate(pairs, 1)]


def _shape_record(
    spec: TrackSpec, assembly: str, raw: dict[str, Any], chrom: str
) -> dict[str, Any]:
    record = {k: v for k, v in raw.items() if k not in spec.drop_fields}
    position = _record_position(record, chrom)
    if ("exonStarts" in record or "chromStarts" in record) and (exons := _exons(record)):
        record["exons"] = exons
    other = (record.get("otherChrom"), record.get("otherStart"), record.get("otherEnd"))
    if isinstance(other[0], str) and isinstance(other[1], int) and isinstance(other[2], int):
        record["other_position"] = {
            "chrom": other[0],
            "start": other[1],
            "end": other[2],
            "display": display(other[0], other[1], other[2]),
        }
    record_id = f"{assembly}:{spec.track}:{record.get('name') or (position or {}).get('display')}"
    for key in spec.prose_fields:
        if isinstance(record.get(key), str) and record[key]:
            record[key] = fence_untrusted_text(
                record[key], source=f"ucsc:{spec.track}", record_id=record_id
            ).model_dump(mode="json")
    if position is not None:
        record["position"] = position
    return record


def _signal_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    values = [r["value"] for r in records if isinstance(r.get("value"), int | float)]
    if not values:
        return {"bases_with_data": 0}
    return {
        "bases_with_data": len(values),
        "mean": round(sum(values) / len(values), 4),
        "min": min(values),
        "max": max(values),
    }


class UcscService:
    """Allowlisted, bounded access to UCSC tracks, sequence and gene-position search."""

    def __init__(self, client: UcscClient, blat: BlatService | None = None) -> None:
        """Wrap a configured :class:`UcscClient` (and the BLAT backend, default: none)."""
        self.client = client
        self.blat = blat or BlatService(BlatSettings())

    async def chromosomes(self, assembly: Assembly) -> dict[str, int]:
        """Chromosome name -> size for the assembly (cached by the client)."""
        data = await self.client.get_json("/list/chromosomes", {"genome": assembly})
        chroms = data.get("chromosomes")
        if not isinstance(chroms, dict) or not chroms:
            raise UpstreamSchemaError("UCSC chromosome list was malformed.", field="chromosomes")
        return {str(k): int(v) for k, v in chroms.items()}

    async def _region(
        self, assembly: Assembly, chrom: str, start: int, end: int, max_width: int
    ) -> dict[str, Any]:
        if start < 0 or end <= start:
            raise InvalidInputError(
                "Need 0 <= start < end (0-based half-open: start is the first base minus "
                "one, end is the last base).",
                field="end" if start >= 0 else "start",
            )
        name = normalize_chrom(chrom)
        sizes = await self.chromosomes(assembly)
        if name not in sizes:
            raise NotFoundError(
                f"Chromosome is not part of {assembly}. Use UCSC names such as chr1..chr22, "
                "chrX, chrY, chrM.",
                field="chrom",
                recovery_action="reformulate_input",
            )
        if end > sizes[name]:
            raise InvalidInputError(
                f"end exceeds the {assembly} length of {name} ({sizes[name]:,} bp).",
                field="end",
            )
        if end - start > max_width:
            raise InvalidInputError(
                f"Interval is {end - start:,} bp; the maximum for this call is "
                f"{max_width:,} bp. Split it into smaller windows.",
                field="end",
            )
        return {
            "chrom": name,
            "start": start,
            "end": end,
            "coordinates": "0-based half-open",
            "display": display(name, start, end),
            "browser_url": browser_url(assembly, name, start, end),
        }

    @staticmethod
    def _assembly_fields(requested: str) -> tuple[Assembly, dict[str, Any]]:
        assembly = resolve_assembly(requested)
        fields: dict[str, Any] = {"assembly": assembly}
        if requested != assembly:
            fields["assembly_requested"] = requested
        return assembly, fields

    def list_tracks(self, assembly: str | None) -> dict[str, Any]:
        """The curated allowlist, optionally only what one assembly offers."""
        db = resolve_assembly(assembly) if assembly else None
        results = [
            track_summary(spec, db)
            for spec in TRACKS.values()
            if db is None or db in spec.assemblies
        ]
        return {
            "results": results,
            "total": len(results),
            "returned": len(results),
            "truncated": False,
        }

    async def region_annotations(
        self, assembly: str, chrom: str, start: int, end: int, track: str, limit: int
    ) -> dict[str, Any]:
        """Records of one allowlisted track overlapping ``[start, end)``."""
        db, payload = self._assembly_fields(assembly)
        spec = track_for(db, track)
        if spec.track != track:
            payload["track_requested"] = track
        region = await self._region(db, chrom, start, end, spec.max_window_bp)
        data = await self.client.get_json(
            "/getData/track",
            {
                "genome": db,
                "track": spec.track,
                "chrom": region["chrom"],
                "start": start,
                "end": end,
                "maxItemsOutput": UPSTREAM_ITEM_CAP,
            },
        )
        raw = data.get(spec.track)
        if not isinstance(raw, list):
            raise UpstreamSchemaError("UCSC track payload was malformed.", field=spec.track)
        capped = bool(data.get("maxItemsLimit"))
        total = None if capped else len(raw)
        # Response-Envelope v1.1: at most 128 fenced objects per result.
        fence_cap = MAX_UNTRUSTED_OBJECTS // len(spec.prose_fields) if spec.prose_fields else limit
        keep = min(limit, fence_cap)
        records = [
            _shape_record(spec, db, r, region["chrom"]) for r in raw[:keep] if isinstance(r, dict)
        ]
        payload.update(
            track=track_summary(spec, db),
            region=region,
            results=records,
            total=total,
            returned=len(records),
            truncated=capped or len(raw) > len(records),
            data_time=data.get("dataTime"),
            track_type=data.get("trackType"),
            retrieved_at=_now(),
        )
        if capped:
            payload["truncation_reason"] = (
                f"upstream_item_cap: more than {UPSTREAM_ITEM_CAP:,} items; narrow the interval"
            )
        elif payload["truncated"]:
            payload["truncation_reason"] = "limit" if keep == limit else "untrusted_text_limit"
        if spec.kind == "signal":
            payload["summary"] = _signal_summary(raw)
        while records and len(json.dumps(payload)) > MAX_RESPONSE_CHARS:
            del records[max(1, len(records) // 10) * -1 :]
            payload.update(returned=len(records), truncated=True, truncation_reason="response_size")
        return payload

    async def reference_sequence(
        self, assembly: str, chrom: str, start: int, end: int, strand: str
    ) -> dict[str, Any]:
        """Reference DNA for ``[start, end)`` (soft-masked: lowercase = repeat-masked)."""
        db, payload = self._assembly_fields(assembly)
        region = await self._region(db, chrom, start, end, MAX_SEQUENCE_BP)
        params: dict[str, Any] = {
            "genome": db,
            "chrom": region["chrom"],
            "start": start,
            "end": end,
        }
        if strand == "-":
            params["revComp"] = 1
        data = await self.client.get_json("/getData/sequence", params)
        dna = data.get("dna")
        if not isinstance(dna, str) or not _NUCLEOTIDES.match(dna) or len(dna) != end - start:
            raise UpstreamSchemaError("UCSC sequence payload was malformed.", field="dna")
        payload.update(
            region=region,
            strand=strand,
            sequence=dna,
            length=len(dna),
            soft_masked=True,
            retrieved_at=_now(),
        )
        return payload

    async def search_genome(self, assembly: str, query: str, limit: int) -> dict[str, Any]:
        """Gene/term -> positions from UCSC's curated gene-position search."""
        db, payload = self._assembly_fields(assembly)
        term = query.strip()
        if not term:
            raise InvalidInputError("query must be a non-empty gene symbol or term.", field="query")
        data = await self.client.get_json("/search", {"search": term, "genome": db})
        hits: list[dict[str, Any]] = []
        exact: set[int] = set()
        wanted = term.upper()
        seen: set[tuple[str, str, int, int]] = set()
        for group in data.get("positionMatches") or []:
            track = group.get("trackName") if isinstance(group, dict) else None
            if track not in SEARCH_TRACKS:
                continue
            for match in group.get("matches") or []:
                hit = self._search_hit(db, str(track), match)
                if hit is None:
                    continue
                key = (hit["track"], hit.get("id", ""), hit["start"], hit["end"])
                if key not in seen:
                    seen.add(key)
                    hits.append(hit)
                    symbol = str(match.get("posName") or "").split(" (")[0]
                    hit_id = str(hit.get("id", ""))
                    if wanted in {symbol.upper(), hit_id.upper(), hit_id.split(".")[0].upper()}:
                        exact.add(id(hit))
        # Exact symbol/accession hits first, then UCSC's looser text matches.
        hits.sort(key=lambda h: (id(h) not in exact, SEARCH_TRACKS.index(h["track"])))
        results = hits[:limit]
        payload.update(
            results=results,
            exact_matches=len(exact),
            total=len(hits),
            returned=len(results),
            truncated=len(hits) > len(results),
            coordinates="0-based half-open (display is 1-based)",
            searched_tracks=list(SEARCH_TRACKS),
            retrieved_at=_now(),
        )
        return payload

    @staticmethod
    def _search_hit(assembly: str, track: str, match: Any) -> dict[str, Any] | None:
        if not isinstance(match, dict):
            return None
        found = _POSITION.match(str(match.get("position", "")))
        if found is None:
            return None
        chrom, first, last = found.group(1), int(found.group(2)), int(found.group(3))
        start = first - 1  # UCSC position strings are 1-based, fully closed
        hit: dict[str, Any] = {"track": track, "chrom": chrom, "start": start, "end": last}
        raw_id = unquote(str(match.get("hgFindMatches") or ""))
        if _SAFE_ID.match(raw_id):
            hit["id"] = raw_id
        record_id = f"{assembly}:{track}:{hit.get('id') or display(chrom, start, last)}"
        for key, source_key in (("label", "posName"), ("description", "description")):
            text = match.get(source_key)
            if isinstance(text, str) and text:
                hit[key] = fence_untrusted_text(
                    text, source=f"ucsc:{track}", record_id=record_id
                ).model_dump(mode="json")
        hit["display"] = display(chrom, start, last)
        hit["browser_url"] = browser_url(assembly, chrom, start, last)
        return hit
