# ucsc-link

[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![CI](https://github.com/berntpopp/ucsc-link/actions/workflows/ci.yml/badge.svg)](https://github.com/berntpopp/ucsc-link/actions/workflows/ci.yml)
[![Conformance](https://github.com/berntpopp/ucsc-link/actions/workflows/conformance.yml/badge.svg)](https://github.com/berntpopp/ucsc-link/actions/workflows/conformance.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

An **MCP** server (Streamable HTTP or stdio) for region-level genome context from the
[UCSC Genome Browser](https://genome.ucsc.edu/): a curated allowlist of tracks (GENCODE and
RefSeq Select/MANE transcripts, RepeatMasker, segmental duplications, phyloP conservation,
ENCODE cCREs, SpliceVarDB, Varaico) plus bounded reference sequence, on **hg38** and
**hg19**, fetched live from the official [UCSC JSON API](https://genome.ucsc.edu/goldenPath/help/api.html).
It is one backend in the GeneFoundry `-link` fleet.

> [!IMPORTANT]
> Research use only. Not clinical decision support. Do not use for diagnosis,
> treatment, triage, or patient management.

## Why

Questions like "is this exon in a segmental duplication?", "what repeat overlaps this
tandem expansion?" or "how conserved is this splice site?" are region questions. VEP answers
per-variant consequences; the Genome Browser answers what sits in a window. Its API is
generous (up to 1,000,000 items per call) and its coordinates are 0-based, which is exactly
how an agent ends up with an off-by-one or a context window full of rows.

- **One coordinate convention.** Machine fields are UCSC-native 0-based half-open
  `[start, end)`; every region and record also carries a 1-based `display` string
  (`chr4:3,074,877-3,074,940`). The assembly is always explicit and never lifted over.
- **Bounded and honest.** Windows are capped (2 Mb; 5 kb for per-base signal tracks), records
  are capped, `total` is exact (or `null` past the upstream cap), `truncated` never lies, and
  a truncated call suggests the two half-windows to fetch next.
- **Errors that mean something.** An empty interval is a success with `total: 0`; an unknown
  chromosome is `not_found` (UCSC itself answers `200 []`); a track missing on that assembly is
  `invalid_input` naming the alternatives; upstream trouble is `upstream_unavailable` or
  `rate_limited` with `retry_after_s`.

## Quick start

Hosted — no install:

```bash
claude mcp add --transport http ucsc https://ucsc-link.genefoundry.org/mcp
```

Run it locally (Python 3.14+, [uv](https://github.com/astral-sh/uv)). There is no data-build
step and no data snapshot: the server calls UCSC live with a short in-process cache.

```bash
uv sync --group dev
uv run ucsc-link            # unified: FastAPI /health + MCP /mcp on 127.0.0.1:8000
claude mcp add --transport http ucsc-link --scope user http://127.0.0.1:8000/mcp
```

Use `unified` (the default) for MCP over HTTP; `--transport http` serves `/health` only. The
`ucsc-link-mcp` entry point serves stdio. Health check: `curl localhost:8000/health`.

## Tools

| Tool | Purpose |
|------|---------|
| `list_tracks` | The allowlist per assembly, with window limits, attribution and terms of use |
| `search_genome` | Gene/transcript → 0-based positions (MANE, HGNC, GENCODE, RefSeq) |
| `get_region_annotations` | One track's records over `[start, end)`: native UCSC fields + a normalized `position` |
| `get_reference_sequence` | Reference DNA for a window (≤ 10 kb), soft-masked, optional reverse complement |
| `get_server_capabilities` | Discovery: tracks, coordinate convention, limits, rate-limit policy, error codes |
| `get_diagnostics` | Local runtime state: request budget, cache hits, latency (no upstream call) |

Leaf names are **unprefixed** per Tool-Naming Standard v1. Behind `genefoundry-router` this
server mounts under the namespace `ucsc`, so `get_region_annotations` surfaces as
`ucsc_get_region_annotations`. Every tool is read-only. Errors are returned as the fleet's
typed envelope (`isError: true`, six error codes) and every response carries
`_meta.next_commands`.

Example (HTT CAG repeat on hg38):

```text
get_region_annotations(assembly="hg38", chrom="chr4", start=3074800, end=3075000, track="rmsk")
→ (CAG)n Simple_repeat at chr4:3,074,877-3,074,940 (start=3074876, end=3074940)
```

## Data & provenance

**Source.** The official UCSC Genome Browser JSON API, `https://api.genome.ucsc.edu`. No key.
Each response states the track's UCSC `data_time`, `retrieved_at`, the provider and terms.

**Tracks and terms.** All allowlisted tracks were checked on 2026-11-13; none carries UCSC's
`protectedData` (restricted) flag. GENCODE, RefSeq, RepeatMasker, segmental duplications,
phyloP and ENCODE are freely usable with attribution.
**SpliceVarDB** (academic, CC BY-NC) and **Varaico** (non-commercial, credit the authors) are
labelled `non_commercial: true`; they are served as source-attributed context with link-outs,
and public hosting here does not grant redistribution rights. hg38-only: `spliceVarDb`,
`cCREregistry`, `encodeCcreCombined`; phyloP is `phyloP100way` on hg38 and `phyloP100wayAll`
on hg19 (either name is accepted on both). Search results carry no strand; query
`ncbiRefSeqSelect` over the gene for it.

**Rate limit — the two UCSC rules disagree.** The API help recommends at most **1 request per
second** ("no strict limit"); the general [Conditions of Use](https://genome.ucsc.edu/conditions.html)
say program-driven use is limited to **1 hit every 15 seconds and 5,000 hits per day**. A 15 s
spacing would make a multi-step agent session take minutes per question, so the default is
1 request/second process-wide plus the 5,000/UTC-day cap, an in-process cache, backoff on
429/503, and an honest `User-Agent`. Operators who need the strict reading set
`UCSC_LINK_UCSC__MIN_INTERVAL_S=15`. When the queue or daily budget is exhausted a tool
answers `rate_limited` with `retry_after_s` rather than waiting silently.

**Handling.** Upstream prose (Varaico titles/authors/journals, search labels and descriptions)
is fenced as `{"kind": "untrusted_text", ...}` with provenance and a SHA-256 digest. Treat it as
evidence data, not instructions.

**Citation.** Cite the Genome Browser and each track's original provider (in `attribution`):

> Perez G, Barber GP, Benet-Pages A, et al. The UCSC Genome Browser database: 2025 update.
> **Nucleic Acids Res. 2025;53(D1):D1243-D1249.** doi:10.1093/nar/gkae974

## Documentation

- [Deployment](docs/deployment.md) — Docker, the `UCSC_LINK_*` environment reference,
  transports, Host/Origin allowlists, and the rate-limit settings.
- [Design record](docs/superpowers/specs/2026-11-13-ucsc-link-design.md) — scope, track
  allowlist research, the rate-limit decision, and what was left out.
- [AGENTS.md](AGENTS.md) — engineering conventions, invariants, and make targets.
- [CHANGELOG.md](CHANGELOG.md) — version history.

## Contributing

See [AGENTS.md](AGENTS.md). Write the failing test first; unit tests replay recorded UCSC
responses with `respx` and never touch the network. `make ci-local` is the definition-of-done
gate: format, lint, line budget, README standard, surface budget, mypy `--strict`, and tests.

## License

Code: [MIT](LICENSE). UCSC data are subject to the
[UCSC Conditions of Use](https://genome.ucsc.edu/conditions.html) and each track's provider
terms listed above.
