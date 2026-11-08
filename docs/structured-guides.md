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
The existing visible-card prefetch and synchronous cached-open path apply to
both formats; current results stay visible until requested content is ready.

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
the semantic contract. Request counts include failed/retried calls; numerical
usage receipts describe accepted responses and may omit charges for rejected
responses.

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
catalog-data/releases/9104e9fe31ef28bba495b721b5cda0b7e534977d8f7ce49764681d5bce296874
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
runtime speedup. The full 207,691-entry search index stays at 9,965,255 gzip bytes,
SHA-256 `806b19edc9f55c34750f63bcca949fcf3457b614a58286e8ad5aaccfa6f0048d`.
Search behavior and its memory/startup work are unchanged.

Validation passed: 107 Python tests, 61 Node hero/search/renderer tests, and
`go test ./...`. Detailed receipts and benchmark JSON are local under
`catalog-data/build/` and `catalog-data/benchmarks/structured/`.
