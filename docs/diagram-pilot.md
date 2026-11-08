# Eight-subject diagram pilot

The pilot tested arm, laptop, phone, car, stove, toilet, refrigerator, and jeans.
It publishes one reviewed phone diagram; the other seven retain their immediate
lists. The existing bicycle diagram is unchanged. This is an experiment, not a
claim of automatic coverage across the catalog.

| Subject | Catalog-backed title/device matches | Result |
| --- | ---: | --- |
| Arm | 48 | List only; final mapping response failed validation |
| Laptop | 2,524 | List only; no acceptable component mapping |
| Phone | 1,545 | Screen, buttons, and speaker mapped and reviewed |
| Car | 119 | List only; no acceptable component mapping |
| Stove | 79 | List only; initial highlights included body panels; correction failed validation |
| Toilet | 36 | List only; final mapping response failed validation |
| Refrigerator | 332 | List only; source art lacks sufficient separate assemblies |
| Jeans | 57 | List only; candidate geometry and crop were unsuitable |

The phone's proposed camera region was part of its earpiece. The review record
omits that geometry while retaining the camera list option. Symptoms and hidden
parts never receive invented geometry. Approved artwork requires matching source,
raw mapping, compiler version, and final artifact hashes. A source or rendering
change requires renewed review.

## Build and repeat

`scripts/diagram-pilot.json` declares the eight experimental subjects and their
catalog domains. `prepare` reads compact catalog rows, not raw guide files or
media. Domain constraints avoid collisions such as CAR-T medicine versus cars.
The source finder, compiler, schemas, queues, and renderer accept data-driven
subjects; the pilot does not encode source tree positions into application code.

```sh
# Verify the 21 pinned originals without network access.
python3 scripts/find_diagram_sources.py verify

# Fetch missing originals from their pinned upstream URLs; changed bytes fail.
python3 scripts/find_diagram_sources.py fetch

# Reuse this machine's validated taxonomy/mapping caches, with no AI requests.
python3 scripts/diagram_pilot.py run --offline

# Rebuild only reviewed public artifacts from prepared inputs and responses.
python3 scripts/diagram_pilot.py compile --offline
python3 scripts/diagram_pilot.py report --offline
```

The complete command also accepts `--runtime-dir` for a locally or cloud-built
catalog. Build dependencies are Python 3.12+, Inkscape for curve/transform bounds,
and librsvg's `rsvg-convert` for offline previews. No browser is launched.
Without `--offline`, missing cache entries call the existing OpenRouter model
using the private API key. Separate taxonomy/mapping caches include source/input,
model, actual prompt, schema, configuration, compiler version, and image hashes.
Complete validated responses are cached before publication decisions; invalid
responses never activate. Forty requests are permitted by the runner, but this
eight-subject experiment reaches at most eight simultaneous requests.

Caches and diagnostic receipts are in `catalog-data/build/diagram-pilot/` and
are not committed. Repeating this machine's full offline run took 14.25 seconds,
made zero API calls, and preserved registry, credits, and approved art bytes.
Eight taxonomy responses and five final mapping responses were reused; three
invalid mapping attempts remain uncached and list-only. A fresh machine must
generate its own responses before regenerating the pilot. Existing committed
public artifacts can be served without those build caches.

## Source discovery and quality

Twenty-one original candidates are pinned with author, source, revision, license,
and byte hashes in [the source directory](references/specifiers/). The selected
pool contains manually inspected source choices. An independent test of the AI's
search queries inspected 73 candidates, rejected 57, and retained 16 structurally
valid proposals. Some survivors were still unrelated illustrations. These
proposals did not replace the curated pins.

MediaWiki requests use a serial cached queue, identifiable User-Agent, `maxlag`,
and backoff. Structural gates reject raster wrappers, active/external resources,
unsupported licenses, and excessive complexity. Shape count alone cannot prove
that an SVG is a useful repair diagram.

Bounding boxes and source IDs helped mapping but were insufficient alone. The
image-assisted follow-up included source-color and monochrome previews. Small
features were still misidentified, and monochrome flattening obscured some
assemblies. Visual review remains required before scaling. Three responses in
the final correction pass failed the strict output gate; no more paid retries
were run. The experiment made 36 known API calls, including exploratory and
corrective passes. The 33 accepted responses reported **$0.058605967**; three
failed responses returned no usable usage information, so this excludes any
charges for those failures.

## Runtime and measurements

`assets/specifier-pilot.js` supplies the small resident registry. Lists appear
synchronously. Diagrams use same-origin, content-hashed JSON fetched at most once
per cached asset. Prefix prefetch and post-catalog idle warming reduce cold
loads; there is no claim of zero network latency. Fetches are capped at two in
flight. Unsupported or failed art stays list-only, with no spinner or empty box.
Failed artwork can be retried by refreshing.

Lazy insertion preserves the connected list, its scroll position, selection, and
native input focus. A stale response cannot replace a newer subject. Only mapped
regions highlight; a list-only symptom does not dim every physical region.
Go prepares JSON gzip and ETags at startup; hashed artifacts are immutable.
Static credits are generated from the same approved manifest on the unlinked
`/credits` page, with notices retained in diagram metadata.

| Local measurement | Result |
| --- | ---: |
| Resident pilot registry | 8,621 bytes / 2,445 gzip |
| Phone diagram JSON | 8,958 bytes / 1,832 gzip |
| Cached phone mount, synthetic DOM p95 | 0.96 ms, 57 nodes |
| Worker preparation on the 207,691-item catalog | 550 ms |
| 76 part-query probe, worker computation p95 | 4.06 ms |
| Catalog requests in that probe | 1, none per query |

The registry and generic loader add about 4.1 KB of compressed initial JavaScript
versus the previous specifier module. The actual Go-served phone response is
1,824 gzip bytes; warm loopback full-response p95 was 0.91 ms across eight samples.
Those loopback samples exclude browser decoding, layout, paint, and internet
latency and do not establish a performance improvement.

The synthetic DOM mount excludes browser layout and paint. The query probe
measures the existing worker, not end-to-end input latency. Two new proposed
queries (`stove timer`, `jeans waistband`) returned no worker results and were
held from the public list. Four existing empty queries were recorded without
changing their baseline options. Measurements and receipts live under
`catalog-data/benchmarks/diagram-pilot/` and the ignored build directory.

Validation passed 118 JavaScript checks, 19 diagram/credit Python checks, ten
source-finder checks, and `go test ./...`. Source originals and source bytes are
unchanged; their original whitespace is deliberately retained with the SHA pins.
