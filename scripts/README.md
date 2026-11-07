# Repair catalog ingestion

```sh
python3 scripts/build_catalog.py
# The identical pipeline can use an EC2/local data directory on another disk:
python3 scripts/build_catalog.py --data-dir /mnt/catalog/work --release-root /mnt/catalog/releases
```

`build_catalog.py` downloads and combines iFixit, English WikiMed, and the U.S.
military First Aid manual. It pins uv 0.13.5 and Python 3.12.12; the source scripts
pin libzim 4.1.0, lxml 6.1.3, and PyMuPDF 1.28.2. Its default work cache is
`catalog-data/build`, with completed snapshots in `catalog-data/releases/<pipeline hash>`.
The fingerprint covers the toolchain pins and all eight pipeline input files:
`build_catalog.py`, `ingest_ifixit.py`, `enrich_search.py`, `search-vocabulary.json`,
`ingest_wikimed.py`, `ingest_first_aid.py`, `merge_catalogs.py`, and `prepare_search.py`.
An unchanged completed release is reused without downloading or scanning its
corpus. A lock prevents overlapping builds; failed builds cannot replace a
published release. EC2 downloads the same public sources and builds its own
catalog; no local corpus, SQLite database, or response cache is uploaded.

Pinned inputs:

| Source | Archive | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| iFixit | [ifixit_en_all_2025-12.zim](https://download.kiwix.org/zim/ifixit/ifixit_en_all_2025-12.zim) | 3,570,695,757 | `9bb5f3408707fd0300c6ef47a425c3217187f144cd9154a928183816aa72eead` |
| WikiMed | [wikipedia_en_medicine_maxi_2026-10.zim](https://download.kiwix.org/zim/wikipedia/wikipedia_en_medicine_maxi_2026-10.zim) | 1,864,210,320 | `9a1570f2f346aa18694ee520e0483f19b7447f69e0970e2f727c53ccf0dcc7d6` |
| First Aid | [UsArmyFirstAidManual.pdf](https://archive.org/download/UsArmyFirstAidManualFm4-25.11/UsArmyFirstAidManual.pdf) | 2,621,944 | `95fb01c941cdb385c152d045081ae011b59805931524e03192e953dc4e57af2f` |

The Kiwix inputs require the pinned official Metalink size/SHA-256 and libzim's
internal checksum. The PDF requires its pinned hashes/size, Archive.org source
metadata, and successful parsing of all 227 pages. These are separate source
proofs: a PDF never receives a fabricated ZIM-checksum flag. The combined
`sources[]` metadata retains each proof, attribution, license, and source count;
its top-level `archiveSha256` is explicitly an aggregate digest of source pins,
not the checksum of another archive.

The builder keeps source caches separate:

- `<data-dir>/runtime/`: iFixit articles, media, and aliases. `source-build.json`
  records the ingestion/enrichment/vocabulary input key for reuse.
- `<data-dir>/wikimed/runtime/`: WikiMed articles and redirect-name aliases;
  its raw ZIM, inventory, and media attribution remain beside that runtime.
- `<data-dir>/first-aid/runtime/`: rough First Aid chapter entries and page
  images. `first-aid/source/` preserves the original PDF, every page's extracted
  text, full outline, and document/provenance metadata.

`merge_catalogs.py` builds a fresh stage, hardlinks article/media files, retains
distinct source licenses, and remaps shared alias groups after deterministic
sorting. It rejects incomplete sources, conflicting IDs/categories, symlinks,
missing files, and unequal media with a colliding name. Source caches and release
storage must share a filesystem; article/media files are not duplicated during
the merge. `prepare_search.py` then rebuilds postings for the combined item
positions. The completed runtime, index, and constituent-source receipt are
renamed into the immutable release directory. Source/download/indexing failures leave
the currently served release intact.

WikiMed is the English Wikipedia medicine selection, distinct from the separately
named MDWiki archive. Full main articles, clinical warnings, and references are
retained; navigation/executable markup is removed and links/images become local.
Redirect titles supply aliases without inferred disease equivalences. Text keeps
CC BY-SA 4.0 attribution and original/history links. Images retain separate
licensing; archive paths and encountered file-description links are recorded.
The original archive and inventory retain provenance.

First Aid is **23 December 2002, Change 1 dated 15 July 2004**, in the public domain
as U.S. federal government material. Entries follow PDF chapter/bookmark
boundaries and deliberately retain rough page text with rendered page images.
This preserves source material for the planned LLM restructuring and custom
entry pages. WikiMed and First Aid use `health` / **Health & medicine**, separate
from iFixit's `medical` / **Medical equipment** category.

Combined source counts, index size, and search measurements will be recorded
after the real build. ARM64 ingestion and deployment of this combined release
have not yet been verified here. The measurements below describe earlier
iFixit-only releases.

Use `CATALOG_DIR=<printed release path> go run .` to serve a completed snapshot.
`scripts/validate_catalog.py` is an explicit, optional full audit; deployment and
the build wrapper never invoke it. It currently describes the older iFixit-only
catalog. Actual English-content filtering remains pending. Never rebuild a source
runtime while a server serves it; serve an immutable release instead.

The iFixit stage runs `enrich_search.py` before merging. It downloads
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

Tests use tiny generated fixtures and the pinned native dependencies:

```sh
uv run --python 3.12.12 --with libzim==4.1.0 --with lxml==6.1.3 --with pymupdf==1.28.2 python -m unittest discover -s scripts -p 'test_*.py'
node --test scripts/test_search_worker.cjs
```

For isolated source work, the three `ingest_*.py` scripts accept `--data-dir` and
`--download-only` / `--build-only`; the canonical builder handles all required
download, extraction, alias, merge, and posting stages automatically.
`bench_search.cjs` compares actual worker computation against a saved
baseline without opening a browser; its numbers are not browser end-to-end latency.

Historical inverted-index measurements on the 103,473-item iFixit catalog (Linux x64, Intel
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

Historical alias-stage measurements on the 103,473-item iFixit catalog (Node v26.12.0, isolated
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

The tracked [catalog summary](catalog-summary.json) records the historical iFixit
source, item/category counts, index size, and completed audit scope; generated corpus
files and the detailed quality report stay local and ignored by Git.

The iFixit source is the complete English `ifixit_en_all_2025-12.zim` archive listed by
the [Kiwix catalog](https://browse.library.kiwix.org/#lang=eng&category=iFixit).
The official Metalink size and SHA-256 and libzim's internal checksum are checked
before extraction. Downloads and extracted media can resume after interruption.

For each ZIM source cache, generated data stays below the ignored data directory:

- `raw/`: original ZIM, Metalink, and source references.
- `catalog.sqlite`: normalized item inventory, source category graph, and provenance.
- `runtime/`: source browser index, full sanitized article JSON/gzip,
  and content-addressed local images.
- `manifest.json`: source checksums, counts, transformations, and license details.
- `quality-report.json`, when present: a historical explicitly requested audit;
  the build and deployment do not produce one.

`runtime/manifest.json` is written last, with `runtimeComplete: true`, after all
indexed articles and media have been processed. `index-preview.json`, when
present, is only an ingestion benchmark artifact; it must never be published as
the finished catalog. Stop local catalog servers before rebuilding their data.

For iFixit, category assignment follows the source Device category graph; title fallback
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
and share-alike. Each article carries the author/source/license; transformation
details remain in the source manifest. This ingestion retrieves documents for reading and search;
it does not train a model. Missing external manuals remain external source links.
