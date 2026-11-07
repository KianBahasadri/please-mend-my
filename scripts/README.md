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
The ingestion/build/enrichment scripts and vocabulary pins are fingerprinted; an unchanged completed release is reused
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
alias strings in every row. The existing worker combines those groups with title,
device and category text; exact title/model matches outrank inherited aliases.
Browser searches issue no external requests. This stage adds aliases/ancestry;
stemming, fuzzy matching and a full inverted index remain separate proposals.

For an unpublished completed local core snapshot only:

```sh
python3 scripts/enrich_search.py --data-dir catalog-data
python3 -m unittest discover -s scripts -p 'test_*.py'
node --test scripts/test_search_worker.cjs
```

Never enrich a snapshot while a server serves it. Production enrichment runs only
inside the unpublished build; download/enrichment failure leaves the live release
unchanged. `bench_search.cjs` compares actual worker computation against a saved
baseline without opening a browser; its numbers are not browser end-to-end latency.

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
