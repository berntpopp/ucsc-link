# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
