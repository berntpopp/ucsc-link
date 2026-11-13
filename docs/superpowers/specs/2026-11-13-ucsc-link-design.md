# ucsc-link design record (2026-11-13)

> Historical record — the v0.1.0 scope decision. The code and `get_server_capabilities` are
> authoritative for current behaviour.

Source issue: berntpopp/genefoundry-router#191 (approved 2026-11-13).

## Scope (MVP)

- Live calls to the official UCSC JSON API (`https://api.genome.ucsc.edu`). No data snapshot:
  some UCSC tracks carry use restrictions, so nothing is stored beyond a short in-process TTL
  cache.
- Curated track allowlist for hg38/hg19 — never arbitrary tracks or track hubs.
- Six tools: `list_tracks`, `search_genome`, `get_region_annotations`,
  `get_reference_sequence`, `get_server_capabilities`, `get_diagnostics`.
- Machine coordinates are UCSC-native 0-based half-open; every region/record also carries a
  1-based `display` string. Assemblies are explicit (aliases GRCh38/GRCh37 accepted) and never
  lifted over.

## Track allowlist research

Resolved against `/list/tracks?trackLeavesOnly=1` for hg38 and hg19 and the track description
pages in the UCSC kent source tree (`src/hg/makeDb/trackDb`):

| Track | hg38 | hg19 | Terms |
|---|---|---|---|
| `knownGene` (GENCODE V50 / V50lift37) | yes | yes | open |
| `ncbiRefSeqSelect` | yes | yes | open |
| `rmsk` | yes | yes | open |
| `genomicSuperDups` | yes | yes | open |
| `phyloP100way` / `phyloP100wayAll` | hg38 name | hg19 name | open |
| `cCREregistry` (ENCODE4) | yes | no | open |
| `encodeCcreCombined` (ENCODE3, archival) | yes | no | open |
| `spliceVarDb` | yes | no | academic / CC BY-NC |
| `varaico` | yes | yes | non-commercial, credit authors |

No allowlisted track carries the API's `protectedData` flag (33 hg38 tracks do, e.g. DECIPHER;
none were taken). The SpliceAI tracks in the same "Splicing Impact" container are Illumina
academic-only and were not taken.

Observed upstream behaviour that shaped the code:

- An unknown chromosome returns HTTP 200 with an empty list, so chromosome names and sizes are
  validated against `/list/chromosomes` first (`not_found` vs an honest empty interval).
- An unknown track returns HTTP 400 `can not find track=...` (`not_found`).
- A capped response returns HTTP 206 with `maxItemsLimit: true`; the server requests at most
  5,000 items, so `total` is exact below that and `null` above it.
- `/search` position strings are 1-based fully closed; they are converted to 0-based starts.

## Rate limit

The API help recommends ≤1 hit/second (no strict limit); `conditions.html` limits program-driven
use to 1 hit per 15 s and 5,000/day. 15 s spacing would make an agent session take minutes per
question, so the default is 1/s + 5,000/UTC day, env-configurable to the strict reading, with
an in-process cache, backoff on 429/503 (honouring `Retry-After`) and a typed `rate_limited`
error with `retry_after_s` when the queue wait would exceed `max_wait_s`.

## Left out

hs1, track hubs, schema-inspection tool, liftover (vep-link owns it), bulk export, response
modes (records are already bounded; a response-size budget trims honestly).
