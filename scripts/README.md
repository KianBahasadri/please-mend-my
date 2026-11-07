# Repair catalog ingestion

```sh
python3 scripts/build_catalog.py              # same pinned, immutable build locally or on EC2
uv run scripts/ingest_ifixit.py                 # download, verify, and build
uv run scripts/ingest_ifixit.py --build-only    # reuse the complete download
uv run scripts/ingest_ifixit.py --download-only
uv run scripts/ingest_ifixit.py --reclassify-only # refresh source-graph categories only
```

`build_catalog.py` pins uv 0.13.5 and Python 3.12.12; the ingestion script pins
libzim/lxml and the archive revision, size, and SHA-256. Its default work cache is
`catalog-data/build`, with completed snapshots in `catalog-data/releases/<pipeline hash>`.
The ingestion/build/enrichment/search scripts and vocabulary pins are fingerprinted; an unchanged completed release is reused
without scanning its corpus. Interrupted downloads/extraction can resume, failed
builds cannot publish a release, and a filesystem lock prevents overlapping builds.
The same command runs on EC2 with `--data-dir` and `--release-root` pointing to
server directories. EC2 downloads directly from public sources; it needs no local
catalog upload. The existing local `catalog-data/runtime` is unaffected.

Use `CATALOG_DIR=<printed release path> go run .` to serve a completed snapshot.
`scripts/validate_catalog.py` is an explicit, optional full audit; deployment and
the build wrapper never invoke it. Actual English-content filtering remains pending.

The canonical build also runs `enrich_search.py` before publication. It downloads
21 explicitly mapped Wikidata object classes at pinned revisions, caches their
source JSON in `raw/wikidata`, and extracts English labels/aliases. Exact iFixit
device/category paths anchor those classes; guides inherit them through the saved
category graph. A small curated vocabulary covers common repair names. There are
no guessed entity-name matches. The index retains separate Wikidata CC0 provenance
and the original iFixit attribution/licence.

Search terms live in shared groups referenced by item position, avoiding repeated
alias strings in every row. After enrichment, `prepare_search.py` builds sorted
word dictionaries and compact delta-varint posting lists for titles, devices,
categories and those shared alias groups. A second prepared dictionary connects
words with the same [Porter stem](https://tartarus.org/martin/PorterStemmer/def.txt),
so inflections such as `battery`/`batteries` and `repair`/`repairing` share matches.
Numeric identifiers are never stemmed.

The browser worker intersects posting lists and retains every match, sending only
the visible range to the page. It supports word prefixes while typing and bounded
typo matching through a compact word trie: one Levenshtein edit for 5–7 letters,
two for words of at least 8 letters. Typo recovery applies to ASCII letter words
when literal/prefix/stem lookup or the combined result is empty. Exact/model matches rank ahead of expanded
matches; numeric-bearing query chunks remain literal. All dictionaries and
postings are prepared before publication. Queries use only the browser's loaded
data, with no server or external search requests.

For an unpublished completed local core snapshot only:

```sh
python3 scripts/enrich_search.py --data-dir catalog-data
python3 scripts/prepare_search.py --data-dir catalog-data
python3 -m unittest discover -s scripts -p 'test_*.py'
node --test scripts/test_search_worker.cjs
```

Never enrich a snapshot while a server serves it. Production enrichment runs only
inside the unpublished build; download/enrichment failure leaves the live release
unchanged. `bench_search.cjs` compares actual worker computation against a saved
baseline without opening a browser; its numbers are not browser end-to-end latency.

Inverted-index measurements on the 103,473-item catalog (Linux x64, Intel
i7-13650HX, Node v26.12.0; isolated processes running the actual worker):

| Probe | Aliases + linear scan | Inverted + stems + typos |
| --- | ---: | ---: |
| Compressed index | 2,724,228 bytes | 3,577,878 bytes (+31.3%) |
| Common 10 queries, warm p95 | 9.55 ms | 1.93 ms |
| All 19 queries, warm p50 / p95 | 8.32 / 9.82 ms | 0.20 / 1.57 ms |
| Initial query pass, p95 | 20.96 ms | 5.60 ms |
| Worker preparation | 203 ms | 224 ms |
| Prepared JS heap growth | 59.05 MiB | 54.37 MiB |
| Prepared external memory growth (includes ArrayBuffers) | 0.40 MiB | 4.73 MiB |

The common subset has 100 timed queries; the expanded suite has 190, after three
warmup rounds. Initial-pass results precede warmups but may share earlier terms.
Separate fresh-process/worker probes measure each typo as the very first query:
`laptpp batery` takes 8.50 ms, `refrxxerator` 9.56 ms, and `noresultzzzz` 5.73 ms
(baseline 21.33, 21.39 and 19.72 ms). Preparation excludes index transfer and JSON
parsing; memory includes parsed catalog data and settles GC identically for both
builds. These are worker CPU measurements, not browser input-to-paint timings.
Single-letter queries still enumerate many matches: warm p95 is 11.13 ms for
`a` and 14.85 ms for `r`; the result list remains complete.

The prepared dictionary has 44,326 terms and 5,258 nonidentity stem mappings.
`laptop batteries` improves from 6 to 2,364 matches, `repairing laptop` from 15 to
428, and `laptpp batery` from 0 to 2,356. `Samsung Galaxy S23` and `S24` retain
53 and 50 matches respectively. Matching now uses words/prefixes rather than
arbitrary embedded substrings: `air` no longer matches the middle of `repair`,
and `washer` no longer matches inside `dishwasher`. This explains the deliberate
`macbook air` count change from 507 to 470 and `washer` from 824 to 679.

Reproduce a comparison against a saved prior worker/index:

```sh
node --expose-gc scripts/bench_search.cjs BEFORE.json BEFORE-worker.js AFTER.json assets/search-worker.js
```

Earlier alias-stage measurements on the 103,473-item catalog (Node v26.12.0, isolated
processes, 30 warmup searches followed by 100 measured searches each):

| Probe | Before | With aliases |
| --- | ---: | ---: |
| Compressed index | 2,598,468 bytes | 2,724,228 bytes (+4.8%) |
| Worker search p50 | 6.88 ms | 6.74 ms |
| Worker search p95 | 8.59 ms | 8.56 ms |
| Worker preparation | 188 ms | 219 ms |
| Prepared JS heap growth | 58.47 MiB | 59.06 MiB |

99,208 items receive extra terms through 5,213 nonempty shared groups. For example,
`macbook notebook battery` changes from 0 to 155 matches, `laptop battery` from 68
to 2,356, and `cellphone` from 0 to 9,689. The exact `Samsung Galaxy S23` query
remains at 53 matches. The benchmark permits one index request at startup and no
requests while searching. These are workstation Node measurements; browser/mobile
latency and memory will differ.

The tracked [catalog summary](catalog-summary.json) records the verified source,
full item/category counts, index size, and completed audit scope; generated corpus
files and the detailed quality report stay local and ignored by Git.

The source is the complete English `ifixit_en_all_2025-12.zim` archive listed by
the [Kiwix catalog](https://browse.library.kiwix.org/#lang=eng&category=iFixit).
The official Metalink size and SHA-256 and libzim's internal checksum are checked
before extraction. Downloads and extracted media can resume after interruption.

Generated data stays in the Git-ignored `catalog-data/` directory:

- `raw/`: original ZIM, Metalink, and source references.
- `catalog.sqlite`: normalized item inventory, source category graph, and provenance.
- `runtime/`: complete compact browser index, full sanitized article JSON/gzip,
  and content-addressed local images.
- `manifest.json`: source checksums, counts, transformations, and license details.
- `quality-report.json`: complete JSON/gzip, HTML, link/media, and database checks.

`runtime/manifest.json` is written last, with `runtimeComplete: true`, after all
indexed articles and media have been processed. `index-preview.json`, when
present, is only an ingestion benchmark artifact; it must never be published as
the finished catalog. Stop local catalog servers before rebuilding their data.

Category assignment follows the source Device category graph; title fallback
and unresolved items are counted separately in the manifest. Specific Software
and Smartwatch source roots classify as Technology rather than inheriting generic
Skills/Apparel categories. Category metadata can be refreshed without re-extracting
images or article content; stop catalog servers before using `--reclassify-only`.
The index includes
canonical repair guides, device/topic pages, and useful articles. Redirects are
resolved, while user profiles and offline error/navigation pages remain in the
original archive rather than becoming search results.

Source content and adapted article HTML retain [iFixit's CC BY-NC-SA 3.0
license](https://www.ifixit.com/Info/Licensing): attribution, noncommercial use,
and share-alike. Each article carries the author/source/license and identifies
formatting changes. This ingestion retrieves documents for reading and search;
it does not train a model. Missing external manuals remain external source links.
