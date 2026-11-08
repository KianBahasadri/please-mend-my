# Noun-backed catalog releases

`python3 scripts/build_catalog.py` runs the same pinned build locally and on EC2.
It downloads its own inputs and publishes a complete immutable release; corpus
files are never uploaded from a developer machine. Production deployment is a
separate, currently paused operation.

The build stages are:

1. Seed a disposable noun database from pinned Open English WordNet and wordfreq.
   Keep common-noun senses, synonyms, hypernyms, part relations, and frequency.
2. Link the merged compact index, including iFixit, WikiMed, and First Aid, to
   generic nouns and literal device models. Source categories constrain domains
   so medical abbreviations do not become vehicle matches. Keep unlinked rows
   and uncovered nouns in a build-only `gaps.json` report.
3. Build archetypes for nouns with actionable guides. Derive parts from WordNet
   relations and components supported by linked titles; retain reviewed art
   mappings. Unsupported diagrams use lists. All nouns use this same mechanism.
4. Rebuild search postings and package the resident registry, guide files, hashed
   artwork, and source credits. Stage files before an atomic release rename.

Build databases and gap reports stay under `catalog-data/build/`, outside public
routes. Disposable SQLite uses journal/synchronous OFF and is replaced on rebuild.
The pipeline reads compact index rows and targeted category tables rather than
opening every guide JSON. Python 3.12.12, uv 0.13.5, data pins, scripts, configuration,
and enrichment caches participate in the release fingerprint.

`index.json` contains the same registry as `archetypes.json`: version, revision,
filled noun IDs, aliases, parts, model names, optional art URLs, and provenance.
Only linked guide IDs receive noun aliases in search postings. Numeric model
identifiers retain literal matching, without stemming or typo expansion. Part
aliases require document-level component evidence.

The worker sends the registry as a JSON string once with its ready message;
the page parses it once, avoiding a nested structured clone. `specifiers.js`
builds synchronous alias/model maps; typing needs no dictionary request or
external API. The parts list mounts immediately. Geometry comes from a separate
same-origin, content-addressed JSON file, with no empty SVG area or spinner while
unavailable. Approved bicycle and phone geometry are build inputs, not embedded
catalog modules. New art still requires source, mapping, compiler, and visual
review gates; noun coverage does not imply diagram coverage.

The Go server prepares `/api/catalog` and `/api/archetypes` once at startup.
`/api/art/<sha256>.json` verifies the pinned body and prepared gzip on its first
read, then shares the bounded 64 MiB guide response cache. Artwork is immutable;
registry responses revalidate. Catalog paths remain confined by `os.OpenRoot`.
The build generates diagram credits from the same registry. Go verifies their
manifest hash and inserts them once into the unlinked `/credits` page.

Verification:

```sh
python3 scripts/run_bounded.py -- go test ./...
python3 scripts/run_bounded.py -- uv run --python 3.12.12 --with libzim==4.1.0 --with lxml==6.1.3 --with pymupdf==1.28.2 --with wordfreq==3.1.1 python -W error::ResourceWarning -m unittest discover -s scripts -p 'test_*.py'
python3 scripts/run_bounded.py -- node --test --test-concurrency=1 scripts/test_*.cjs
python3 scripts/run_bounded.py -- node --expose-gc scripts/bench_search.cjs
```

Run these serially. The Linux/systemd user-service wrapper enforces a 2 GiB
memory cap for the complete process tree, two CPU cores, a single shared job
lock, and an 8 GiB available-RAM reserve. It refuses to start if the reserve is
unavailable, and stops its own service if available RAM falls below that floor.
Go builds use one package at a time; Node test files use one worker. Never run
an uncapped corpus build or multiple full test suites on the workstation.

The benchmark's default input is the locally selected release (or `CATALOG_DIR`).
It reports resident bytes, preparation time, memory, synchronous registry lookup,
and actual worker search time. It makes one catalog request and no requests per
search. These measurements exclude browser layout, painting, and internet transfer.

## Local verification receipt

Release `18b15619…` preserves 207,691 catalog rows, 679,729 media files, and the
100 validated structured guides. Its build seeds 57,566 noun senses, links
171,186 items across all three sources, and publishes 12,026 filled senses and
12,310 model names with actionable guides. The 36,505 unlinked rows and 45,540
unfilled senses remain in the build-only report. Linking and part extraction
are deterministic heuristics; these counts do not imply human-curated coverage.
Only the two reviewed drawings (bicycle and phone) have geometry. Other filled
nouns use source-supported lists, or no panel when no part evidence exists.

The cached-source build completed in 107 seconds, with an observed cgroup peak
of 1,536 MiB inside its 2 GiB cap. Available system RAM remained around 24 GiB.
An unchanged rerun reused its receipt in under one second. No Qwen batch or
corpus upload was used. The repeat command is:

```sh
python3 scripts/run_bounded.py -- python3 scripts/build_catalog.py --source-runtime /path/to/completed/source-release
```

Both comparisons below used the actual catalog and the same two-CPU/memory
limits. The after run also clones/parses the worker transport and prepares the
page's registry. Times are Node computation/file parsing, not browser paint or
internet transfer; small query-time differences are not a causal speedup claim.

| Measurement | Before | After |
| --- | ---: | ---: |
| Index JSON bytes | 33184073 | 38413254 |
| Index gzip bytes | 9972214 | 11111137 |
| Worker preparation | 537 ms | 654 ms |
| File read → page registry ready | 822 ms | 1217 ms |
| Prepared heap | 110.3 MiB | 135.5 MiB |
| Query p50 | 0.49 ms | 0.57 ms |
| Query p95 | 3.44 ms | 3.25 ms |

The resident registry is 4,347,054 JSON bytes / 676,937 gzip bytes, already
included in the index. Its initial setup measured 202 ms (about
35 ms parsing plus 165 ms configuring); synchronous lookup p95 was
0.002 ms. The first unoptimized object-transport probe measured 316 ms setup.
This adds upfront bytes, memory, and preparation; searches still make one initial
catalog request and no requests per query. `high blood pressure` now finds 72
matches versus seven, while S23/S24 and strict numeric probes retain their totals.
Actual probes rank the loose bicycle pedal guide first for `bike pedal`, and
`Broken arm` first for both `arm broken` and `arm it's broken`. Approved bicycle
geometry matches its old build fixture exactly. Alias ranks select the intended
bicycle/body-arm/telephone senses; source ancestry maps MacBook Air to laptop.

The final gate passed 168 Python tests, 131 JavaScript checks, and `go test ./...`.
The local binary serves all new endpoints and generated credits as an
unprivileged user. Its transient user unit is `please-mend-my-local.service`,
with a separate 768 MiB cap; measured resident memory was about 100 MiB.
`systemctl --user restart please-mend-my-local` restarts that selected release;
`systemctl --user stop please-mend-my-local` releases port 8080. No browser was
opened and production was unchanged. Detailed receipts remain in the ignored
`catalog-data/benchmarks/noun-release/` directory.
