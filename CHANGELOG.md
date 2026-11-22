# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.2] - 2026-11-22

### Fixed

- Deploy: `docker/docker-compose.npm.yml` now passes the router's `validate-deployed-overlay`
  (Deploy-Contract Standard v1, #12): the `ucsc-blat` volume declares its production name
  `ucsc-blat-hg38-kent-v479-r2` (C5), so a Compose project rename can no longer start gfServer
  on an empty volume; every service reads `${GF_IMAGE:-${UCSC_LINK_IMAGE:?...}}` (C3; `UCSC_LINK_IMAGE`
  stays required: Compose resolves the nested `:?` even when `GF_IMAGE` is set); `ucsc_link_internal_net` declares the registry subnet
  `${GF_INTERNAL_SUBNET:-10.251.12.0/28}` (C6).

## [0.6.1] - 2026-11-22

### Fixed

- Deploy: `ucsc-gfserver-hg38` sets `memswap_limit` equal to its memory limit, so the
  hg38 index can no longer be swapped out (production had 1.8 GiB of it in swap; BLAT and
  isPcr paged it back in from disk). The 3 GB limit already covers the measured 2.53 GB
  peak (#10).

## [0.6.0] - 2026-11-20

### Added

- `search_genome` also reads UCSC's matches on fallback gene tracks, after the curated ones:
  `ncbiRefSeqPredicted` (hg38; NCBI `LOC…` ids such as `LOC124909477`) and GENCODE V38 Basic
  (`wgEncodeGencodeBasicV38` on hg38, `wgEncodeGencodeBasicV38lift37` on hg19; legacy
  clone-based names such as `RP11-173H9.2`). UCSC already returned these matches; the
  curated-track filter dropped them, so the search answered with an empty result
  (genefoundry-router#351).
- When no curated track knows the name, fallback hits that name the query gain `gene`,
  `strand`, `transcripts` and `has_cds` from their own track (at most two extra upstream
  calls per search; one call covers all transcript variants of a gene). `has_cds` describes
  that track's transcripts of the gene only and is not a gene biotype; `note` says so
  whenever the field is present. If the detail call fails, the hit is returned without
  these fields.
- Fallback hits are dropped when a curated track has an exact hit, so results and counts
  for genes the curated tracks know are unchanged.
- After a GENCODE fallback hit `next_commands` suggests
  `get_region_annotations(track='knownGene')`, which shows the same transcript; after a
  predicted-RefSeq hit no region step is suggested (no allowlisted track shows the record).
- `searched_tracks` lists the fallback tracks of the assembly; `get_server_capabilities`
  lists them as `fallback_search_tracks`. Tool names, descriptions and input schemas are
  unchanged.

## [0.5.1] - 2026-11-19

### Fixed

- Deploy: the image pre-creates `/opt/blat/hg38` (gfServer's `working_dir`) owned by uid 10001, so `ucsc-blat-fetch` can write `hg38.2bit` on a fresh `ucsc-blat` volume instead of failing with `Permission denied` on a root-owned directory Docker created.

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
