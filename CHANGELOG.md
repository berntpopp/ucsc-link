# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-11-19

### Added

- `search_genome`: every hit carries `exact`; with no exact symbol/accession match the result
  says so in `note` (loose matches hit gene descriptions, so a disease name such as "cystic
  fibrosis" returned GUCY2C first) and `next_commands` route to `hgnc_resolve_symbol` and
  `mondo_resolve_disease` instead of annotating a weak hit.
- `invalid_input` errors carry `field_errors` (`[{field, reason}]`) next to `field`.
- Guard tests: hint targets name real tools/arguments; backticked description fields are
  returned; description lead (rule D1, Tool-Schema Documentation v1.1) checked strict.

### Changed

- `find_blat_alignments` / `predict_pcr_products` descriptions now lead with what they return
  (D1). `scripts/surface.py` re-vendored from genefoundry-router v0.13.1.

## [0.4.0] - 2026-11-18

### Changed

- Upgrade FastMCP 3.4 → 4.2.2 and MCP SDK 1.x → 2.x; supports the 2026-07-28 sessionless
  protocol era alongside the legacy handshake.
- Not-found guard Layer 3 ported to `get_request_handler`/`add_request_handler` (SDK 2 removed
  `request_handlers`); the resource/prompt backstop raises `MCPError(INVALID_PARAMS)` so
  callers keep `-32602`.

### Security

- SDK 2 echoes an unknown JSON-RPC method name in the METHOD_NOT_FOUND `data`; a low-level
  middleware strips it (both protocol eras). New `mcp.server.runner` log lines that echo the
  method are scrubbed.

## [0.3.0] - 2026-11-13

### Added

- `find_blat_alignments`: BLAT one DNA sequence (20-25,000 bp, A/C/G/T/N, one optional FASTA
  header) on a self-hosted UCSC Kent `gfServer`. Hits carry 0-based half-open coordinates +
  1-based `display`, strand, UCSC web `score` and `identity` (kent `pslScore` /
  `pslCalcMilliBad`), `query_coverage`, `blocks` (spliced alignments), `alt_or_patch`; the
  payload adds `primary_total`.
- `predict_pcr_products`: in-silico PCR (gfPcr defaults) for a 15-50 nt primer pair with
  `max_product_size` (default 4,000, max 10,000): location, size, strand, per-primer mismatch
  counts and the product sequence (first 1,000 bp). Both tools point `next_commands` at the
  gene/exon annotation of the best hit/product.
- BLAT availability is honest: an assembly without a deployed gfServer answers
  `upstream_unavailable` naming the configured ones; `get_server_capabilities.blat` lists
  configured assemblies, limits, score formula, Kent licence and citation;
  `get_diagnostics.blat` probes each gfServer live.
- `ucsc-link-blat-fetch`: deploy-time download of the Kent binaries (frozen
  `linux.x86_64.v479`) and `hg38.2bit` / `hg19.2bit`, verified against pinned SHA-256 (+ UCSC
  md5) and installed atomically. The Kent tools are never shipped in the image (free for
  academic/non-profit/personal use; commercial use needs a Kent Informatics licence).
- Deployed overlay: run-once `ucsc-blat-fetch` init -> hardened internal-only
  `ucsc-gfserver-hg38` (`-stepSize=5`, one server for BLAT and PCR; measured 2.53 GB peak RSS,
  ~70 s start) -> app mounting the `ucsc-blat` volume read-only. hg19 is configuration only.
- Tracks: `mane` (MANE Select/Plus Clinical, with strand-aware exons), `encBlacklist`,
  `grcExclusions`, GIAB `alldifficultregions` and `alllowmapandsegdupregions`,
  `simpleRepeat`, `cpgIslandExt`.
- `make test-blat`: real Kent tools end to end on a 10 kb synthetic genome (runs in CI);
  opt-in acceptance against a real hg38 gfServer.

## [0.2.0] - 2026-11-13

### Added

- `search_genome` ranks exact symbol/accession hits first and reports `exact_matches`;
  UCSC's looser text matches follow. Default `limit` is now 5 (was 10).
- Transcript records (`knownGene`, `ncbiRefSeqSelect`) gain `exons`, numbered in transcript
  order (minus-strand aware); native `exonStarts`/`exonEnds` are kept.
- `genomicSuperDups` records gain `other_position` (partner copy, with 1-based `display`).
- An oversized-window error offers the first allowed slice in `_meta.next_commands`.

### Fixed

- Parallel identical calls share one upstream request (single-flight cache), so cold
  parallel calls no longer spend extra UCSC rate-limit slots.
- A `search_genome` query containing control/zero-width/bidi characters is now
  `invalid_input` (field `query`) instead of a misleading `upstream_unavailable`.
- Conformance workflow waits for `/mcp` readiness (fleet template).

## [0.1.0] - 2026-11-13

### Added

- First release (berntpopp/genefoundry-router#191). MCP server over the official UCSC
  Genome Browser JSON API with six read-only tools: `list_tracks`, `search_genome`,
  `get_region_annotations`, `get_reference_sequence`, `get_server_capabilities`,
  `get_diagnostics`.
- Curated track allowlist for hg38/hg19: `knownGene`, `ncbiRefSeqSelect`, `rmsk`,
  `genomicSuperDups`, `phyloP100way` (hg38) / `phyloP100wayAll` (hg19), `cCREregistry`,
  `encodeCcreCombined`, `spliceVarDb`, `varaico`, each with provider attribution and terms;
  SpliceVarDB and Varaico are flagged `non_commercial`.
- One coordinate convention: 0-based half-open machine fields plus 1-based `display`
  strings; explicit assemblies (GRCh38/GRCh37 aliases), never lifted over.
- Bounded output with honest `total`/`truncated`; unknown chromosome is `not_found`, an empty
  interval is a success with `total: 0`.
- Process-wide politeness limiter (default 1 request/s + 5,000/UTC day, configurable to the
  strict 1 per 15 s reading), in-process TTL cache, backoff on 429/5xx, honest User-Agent,
  `rate_limited` errors with `retry_after_s`.
- Upstream prose fenced as `untrusted_text` (Response-Envelope v1.1).
- Fleet container hardening, conformance/behaviour probes, container release workflow.
