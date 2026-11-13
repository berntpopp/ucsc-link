# CLAUDE.md

**Read [AGENTS.md](AGENTS.md) first** — it is the authoritative contributor and agent guide.

Essentials:

- Read-only MCP server over the UCSC Genome Browser JSON API: curated hg38/hg19 track
  allowlist + bounded reference sequence. Live calls only; no data snapshot.
- Data plane returns plain dicts and raises typed errors; `run_mcp_tool` owns the envelope.
- 0-based half-open machine coordinates + 1-based `display`; never lift over.
- Every upstream call goes through the process-wide `RateLimiter` — respect it in live tests.
- BLAT / in-silico PCR run on a self-hosted Kent gfServer (deploy-time fetch, SHA-256 pinned);
  never bypass UCSC's human check. See docs/deployment.md#blat-and-in-silico-pcr.
- FastMCP 3.x is post-training-cutoff: verify symbols against the installed package.
- Gate: `make ci-local` (serialize it on shared machines). Never `gh release create`.
