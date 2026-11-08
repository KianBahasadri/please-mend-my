# Offline guide restructuring

The build chooses a validated `catalog-data/build/stage3_cache/<id>.json` for
each processed guide; other entries retain their original HTML JSON. Both
formats are pre-gzipped. This choice happens before publishing an immutable
catalog, with no cache lookups, AI calls, or format routing in the Go handler.
The existing handler still reads a guide on its first prefetch and caches a
bounded working set in memory.

Before publication, `prepare_search.py` adds each validated guide's `symptoms`
and `materials` to that document's inverted postings. It reads only IDs recorded
in `manifest.structuredGuides.itemIds`, with no raw-guide content scan. The same
normalization, Porter stems, and typo rules apply to those words. Numeric-bearing
field text is retained as sparse `guideLiterals` metadata for the worker's
existing literal identifier check; model punctuation remains significant.
Five-field catalog rows and shared aliases remain unchanged. Queries need no
guide fetches or external services.

The browser dispatches v2/steps payloads to a text-only renderer, with tools,
materials, safety notes, symptoms, checkable steps, local images, and copyable
commands. Existing sanitized HTML uses the legacy renderer. Source credits and
licenses remain, and source-provided further references share the metadata row.
The first ranked guide renders inline while the hero remains visible and
editable; ranks two through five are prefetched and two through six are available
as alternatives. Both formats share this path. Automatic answers do not move
focus, and the current answer stays visible until its replacement is ready.

## Inputs and validation

`scripts/structured_guides.py` owns the strict JSON schema shared by the model,
processor, cache validator, and build overlay. Authoritative identity, title,
source URL, dates, provenance, credits, and license come from the original guide,
never the model. Only source-provided HTTP(S) references and local `/media/`
assets can enter generated steps. Generated strings are rendered as text,
including code; generated HTML is rejected.

Stage 2 extracts labeled source sections and removes structural navigation.
It preserves substantive text, quantities, negation, and warnings. Oversized
sources fail explicitly instead of being silently truncated. Stage 3 runs
bounded asynchronous OpenRouter calls to `qwen/qwen3.6-35b-a3b`, with
[`response_format.json_schema` and strict provider parameters](https://openrouter.ai/docs/guides/features/structured-outputs).
The runner defaults to 40 concurrent requests, using a dedicated HTTP thread
pool so Python's smaller default executor cannot limit it. Concurrency is bounded
at 128; transient rate-limit/server failures use bounded retry backoff. This
scheduler setting does not alter generation cache keys.
It independently validates returned JSON and source-bound assets before caching.
A schema-valid output still requires content review; it is not proof of factual
or clinical correctness, and dated First Aid source material remains dated.

SHA-256 keys cover the complete raw guide, model, prompt version, schema, and
request/cleaner configuration. Valid results live in `stage3_cache/objects/<sha>.json`;
`stage3_cache/<id>.json` activates one result for publication. Failed responses
do not become active entries. Complete schema-valid model responses are also
saved to `stage3_cache/responses/<sha>.json` before postprocessing. Their source,
model, prompt, schema, and request configuration are checked before offline reuse.
Unsupported or duplicate optional references/images are discarded; generated
prose and safety instructions are preserved. Partial or schema-invalid responses
are never cached for publication. Cache hits make no API calls and incur no model
charge; local reads and validation still take time. Source or configuration
changes invalidate the key. Malformed or stale active entries fall back to v1
during the build. A cache change during publication aborts the new snapshot.
Failed reprocessing leaves the previous valid activation available; changed raw
source still fails publication validation. Bump `PROMPT_VERSION` when changing
the semantic contract. Request counts include failed/retried calls. Usage
receipts include rejected responses when the provider returns numeric accounting;
`unpricedApiCalls` explicitly counts calls without a reported charge.

## Systematic catalog batch

`select` reads the source indexes, keeps guides/articles, and first validates
available cached results against their original sources. It then fills the
remaining quota by deterministic round-robin across observed source/category
strata. No subject allowlist or guide-body corpus scan is used. The current
authorization caps this batch at 10,000 unique articles, including reused results.
Pass each original source runtime separately; selected entries resolve directly
to those originals without copying another merged corpus:

```sh
DATA=catalog-data/build
python3 scripts/restructure_catalog.py select \
  --runtime-dir "$DATA/runtime" --runtime-dir "$DATA/wikimed/runtime" \
  --runtime-dir "$DATA/first-aid/runtime" --runtime-dir "$DATA/appropedia/runtime" \
  --runtime-dir "$DATA/wikihow/runtime" --cache-dir "$DATA/stage3_cache" \
  --limit 10000 --max-input-chars 200000 --output "$DATA/articles-10000/selection.json"
python3 scripts/restructure_catalog.py run --runtime-dir "$DATA" \
  --manifest "$DATA/articles-10000/selection.json" --cache-dir "$DATA/stage3_cache" \
  --max-input-chars 200000 --concurrency 128 --min-free-gib 2 \
  --output "$DATA/articles-10000/result.json"
```

Run these under the shared bounded-job wrapper; concurrent remote workers still
share one capped local process tree. `--dry-run` validates cache reuse and input
sizes without model calls. A two-GiB disk reserve plus the concurrent response
working set is required before starting new paid requests. Source oversize is
reported without truncation. Per-entry receipts and an append-only usage journal
live beside the result in `result.progress/`, preserving completion/failure costs
across interruption. Response and activation caches remain reusable on rerun.
Regenerate selection paths on EC2 using its own source runtimes; the corpus stays
on the machine that ingested it. Publish only a completed validated overlay, then
rebuild symptom/material postings before selecting the new immutable release.

## Repeatable pilot

Use the original legacy release as input. For the current pinned catalog:

```sh
RAW_CATALOG=catalog-data/releases/f55559d274f784f9e90e11cc3b1bb9182dbc2b37abd16c236c8727873d6411c3
python3 scripts/restructure_catalog.py pilot --runtime-dir "$RAW_CATALOG"
python3 scripts/restructure_catalog.py prepare --runtime-dir "$RAW_CATALOG" --max-input-chars 200000
python3 scripts/restructure_catalog.py run --runtime-dir "$RAW_CATALOG" --max-input-chars 200000 --dry-run
python3 scripts/restructure_catalog.py run --runtime-dir "$RAW_CATALOG" --max-input-chars 200000 --concurrency 40
```

The API key stays in `OPENROUTER_API_KEY`, outside Git. The process environment
takes priority; otherwise the runner reads only that key from the private root
`.env` (mode 0600), with literal parsing and no shell execution. `--env-file`
selects another private file. The explicit 200,000
character limit retains the pilot's longest source without truncation.
Pilot selection uses the
tracked `scripts/pilot-topics.json` rules, reading the index and only selected
guide files. `catalog-data/build/pilot100.json` pins the index, selector, source
hashes, and 100 unique IDs: 25 each in emergency/first aid, software/OS,
everyday/handyman, and consumer hardware. Missing preferred examples are reported
as gaps; actual in-domain source entries fill the quota. The processor never
fabricates missing source guides. Prepared inputs and processing receipts stay
under the ignored build directory.

Rerun the same command to reuse successful entries and retry failures. Separate
raw inputs, validated output, and immutable releases remain available; no
published snapshot is modified in place.

## Build and serve locally

The canonical pinned builder applies the overlay after merging source catalogs
and before preparing the browser index. The standalone overlay also rebuilds
postings in its unpublished stage, so both paths include symptoms and materials.
Their release keys include the active cache fingerprint and indexer code, so
new processed output cannot be hidden by release reuse.
`--stage3-cache` overrides the default `<data-dir>/stage3_cache`.

An existing raw release can receive the overlay without reingesting sources:

```sh
python3 scripts/overlay_guides.py --source-runtime "$RAW_CATALOG"
```

The command prints the new immutable release path. Set `CATALOG_DIR` to that
path and rebuild/restart the local Go server to use the current renderer:

```sh
go build -o /tmp/please-mend-my-local .
CATALOG_DIR=<printed-release-path> ADDR=127.0.0.1:8080 /tmp/please-mend-my-local
```

A new overlay hardlinks unchanged guide/media files and atomically replaces
processed JSON/gzip, the prepared index/gzip, and its own metadata. It enumerates filenames once to build
the snapshot, without auditing guide/media contents. Repeating an unchanged
overlay reuses its completed release. An empty active cache returns the original
release immediately. The original legacy release must remain the base for later
overlays. EC2 can run the same commands against its own downloaded source build;
no local corpus needs uploading. Deployment remains paused.

## Checks

```sh
python3 -m unittest discover -s scripts -p 'test_structured_contract.py'
python3 -m unittest discover -s scripts -p 'test_overlay_guides.py'
python3 -m unittest discover -s scripts -p 'test_restructure_catalog.py'
python3 -m unittest discover -s scripts -p 'test_build_catalog.py'
node --test scripts/test_structured_guides.cjs
go test ./...
```

Fixtures exercise schema/provenance, cache freshness, failed responses, hardlink
safety, immutable publication, both rendering formats, copy/checklist controls,
and cached/pending navigation. Node DOM tests do not measure browser layout,
painting, or input-to-paint latency.

## Local pilot result

The completed pilot contains 100 validated v2 guides, 25 in each group. Exact
requested examples missing from current sourced titles are PipeWire
troubleshooting, Wi-Fi disconnections, Windows boot repair, and leaking pipes;
the manifest records those gaps and uses available in-domain entries. Generated
prose remains pilot material, with original source dates and attribution.

The local server uses this immutable overlay:

```text
catalog-data/releases/ac9690ea345a3db059df79616f8eb4bcc4204299193eb24a3e275dbf87d34faf
```

It serves 100 structured guides and 207,591 legacy entries. The original
`f55559d2…` release remains the raw input; do not feed the processed snapshot back
into Stage 2. All 100 served JSON/gzip pairs were checked against the snapshot,
one unprocessed guide retained its original response, and embedded assets match
the checkout. An unchanged processor run reused all 100 entries with zero API
calls in 2.79 seconds. The first pass made 100 calls including its initial smoke
entry. Ten rejected responses were not retained by the original runner and
required ten further calls. The runner now saves complete schema-valid responses
before postprocessing. No production deployment was performed.

Measured sizes and local HTTP timings:

| Probe | Original | Structured |
| --- | ---: | ---: |
| All 100 guide JSON payloads | 8,690,840 bytes | 1,105,979 bytes |
| All 100 pre-gzipped guide payloads | 1,934,726 bytes | 424,703 bytes |
| 12-guide HTTP sample, total gzip | 300,472 bytes | 52,820 bytes |
| Sample median of per-guide warm medians | 0.248 ms | 0.256 ms |
| Sample median of per-guide warm p95 | 0.310 ms | 0.320 ms |

Each sampled guide had one first request and ten warm requests. Timings include
local HTTP through receipt of the complete compressed body; they exclude browser
parse, layout, and paint. They show similar warm response time, not a demonstrated
runtime speedup. These initial guide-only measurements used the original
9,965,255-byte gzip index, SHA-256
`806b19edc9f55c34750f63bcca949fcf3457b614a58286e8ad5aaccfa6f0048d`.
The symptom/material index below replaces that index.

Validation passed: 107 Python tests, 61 Node hero/search/renderer tests, and
`go test ./...`. Detailed receipts and benchmark JSON are local under
`catalog-data/build/` and `catalog-data/benchmarks/structured/`.

## Symptom and material search

The current local snapshot indexes `symptoms` and `materials` from all 100
validated v2 guides. Both the canonical builder and standalone overlay run the
same prepared-index stage before immutable publication. The overlay records its
processed IDs and the new index checksum, and includes the indexer source in its
release fingerprint. Raw and previous snapshots remain unchanged.

Checks against the actual browser worker and locally served index confirm:

- `throat swelling` returns Anaphylaxis first; previously it returned no matches.
- `hives`, `impending doom`, and `shortness of breath` include Anaphylaxis.
- `mineral oil` and `mineral oils` include the squeaky-door-hinge guide.
- `antihistamines H1` and `antihistamines H2` include Anaphylaxis; `H3` and `H1+`
  do not turn into those literal material identifiers.
- Galaxy S23/S24 result IDs and ordering match the prior index.

The worker fetched the index once, then made no requests while searching.
The new index has SHA-256
`d08fca7fe28df791f928593b1068688ac9fcc030836b43f9161f31ad1b64459f`.
It retains 207,691 rows and 52,398 alias groups, with 190,709 terms and 55,947 stem
entries. Only the 100 processed guide JSON files were read for keyword enrichment;
unprocessed guide content was not scanned.

| Probe | Before | With symptoms/materials |
| --- | ---: | ---: |
| Index JSON | 33,169,530 bytes | 33,184,073 bytes |
| Index gzip | 9,965,255 bytes | 9,972,214 bytes |
| Worker preparation | 528.8 ms | 511.5 ms |
| Prepared JS heap growth | 108.634 MiB | 108.663 MiB |
| Prepared external memory growth | 16.182 MiB | 16.196 MiB |
| 27-query suite, warm p95 | 3.176 ms | 3.163 ms |
| Common repair subset, warm p95 | 3.278 ms | 3.461 ms |

The compressed index grew by 6,959 bytes (0.07%). These isolated Node worker
measurements use the existing multisource benchmark, with three warmup rounds
and ten measured rounds; they exclude network and browser paint. Startup varies
between runs, so the preparation difference is not a demonstrated speedup.
The tested symptom/material examples had warm worker p95 at most 0.143 ms.
Detailed comparison and served-index query receipts are under
`catalog-data/benchmarks/symptoms/`.

Validation after this change: 118 Python tests, 66 Node tests, and
`go test ./...` pass. Rebuild and restart the local server after worker edits;
refresh existing browser tabs to load the new worker and resident index.
