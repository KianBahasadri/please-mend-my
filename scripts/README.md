# Repair catalog ingestion

For offline AI restructuring, the source-backed 100-entry pilot, strict v2
validation, and static guide overlays, see [the restructuring pipeline](../docs/structured-guides.md).

```sh
python3 scripts/build_catalog.py
# The identical pipeline can use an EC2/local data directory on another disk:
python3 scripts/build_catalog.py --data-dir /mnt/catalog/work --release-root /mnt/catalog/releases
```

The [noun-backed release pipeline](../docs/catalog-pipeline.md) covers Stage 0–4,
all merged sources, build-only coverage reports, resident noun/model/part metadata,
and reviewed, content-addressed diagrams. On the workstation, run heavy jobs
serially through the memory limiter:

```sh
python3 scripts/run_bounded.py -- python3 scripts/build_catalog.py
```

This defaults to 2 GiB (up to `--memory-mib 8192`, matching prod RAM) and two CPU cores, and reserves 8 GiB available
RAM. Run JavaScript checks with `--test-concurrency=1`; never run full suites in
parallel. The wrapper requires a Linux systemd user service manager and refuses
to fall back to an uncapped command.

`build_catalog.py` downloads and combines iFixit, English WikiMed, the U.S.
military First Aid manual, Appropedia and wikiHow Large. It pins uv 0.13.5 and Python 3.12.12; the source scripts
pin libzim 4.1.0, lxml 6.1.3, and PyMuPDF 1.28.2. Its default work cache is
`catalog-data/build`, with completed snapshots in `catalog-data/releases/<pipeline hash>`.
The fingerprint covers the toolchain pins, active structured-guide cache, and
all pipeline scripts, noun-source and artwork configurations, pinned art inputs,
and the credits generator. OEWN and wordfreq pins participate in the noun seed
receipt; noun/link inputs participate in the release key.
An unchanged completed release is reused without downloading or scanning its
corpus. A lock prevents overlapping builds; failed builds cannot replace a
published release. EC2 downloads the same public sources and builds its own
catalog; no local corpus, SQLite database, or response cache is uploaded.

SVG batch experiments use the same local metadata and reusable source tools:

```sh
python3 scripts/run_bounded.py --memory-mib 8192 -- uv run --python 3.12.12 python scripts/diagram_batch.py run --limit 10 --concurrency 128 --discovery-workers 16 --geometry-workers 2 --work-dir catalog-data/build/diagram-batch-10
```

`OPENROUTER_API_KEY` is read from the ignored root `.env`. `--offline` replays
validated response and source caches without upstream calls. `prepare`,
`taxonomy`, `discover`, `digest`, `map`, `compile`, and `verify` resume individual phases;
`--phase secondary` selects the coverage-gated follow-up. Outputs, source pins,
geometry, contact sheets, accounting, and `report.json` stay in
`catalog-data/build/diagram-batch-10/`. Numbered candidate images and independent
per-part VLM checks are pinned in their cache identities. A processed noun or
automated visual pass is not an accepted SVG. Report the 10-item pass rate and
cost before expanding; a larger run needs authorization. The batch does not
mutate a served release or bypass visual approval. Historical 300-item evidence
remains in `catalog-data/build/diagram-batch-300/`.

Pinned inputs:

| Source | Archive | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| iFixit | [ifixit_en_all_2025-12.zim](https://download.kiwix.org/zim/ifixit/ifixit_en_all_2025-12.zim) | 3,570,695,757 | `9bb5f3408707fd0300c6ef47a425c3217187f144cd9154a928183816aa72eead` |
| WikiMed | [wikipedia_en_medicine_maxi_2026-10.zim](https://download.kiwix.org/zim/wikipedia/wikipedia_en_medicine_maxi_2026-10.zim) | 1,864,210,320 | `9a1570f2f346aa18694ee520e0483f19b7447f69e0970e2f727c53ccf0dcc7d6` |
| First Aid | [UsArmyFirstAidManual.pdf](https://archive.org/download/UsArmyFirstAidManualFm4-25.11/UsArmyFirstAidManual.pdf) | 2,621,944 | `95fb01c941cdb385c152d045081ae011b59805931524e03192e953dc4e57af2f` |
| Appropedia | [appropedia_en_all_maxi_2026-02.zim](https://download.kiwix.org/zim/other/appropedia_en_all_maxi_2026-02.zim) | 581,641,351 | `dcf200ba723c27397e1c9a2e4891ff91fa13993df1fa285315190d8525a53469` |
| wikiHow Large | [wikihowAll.csv](https://huggingface.co/datasets/Lajvi/wikihowAll/resolve/5e44fed0e204c27ad985e6292e55e311504d4fe5/wikihowAll.csv) | 619,172,672 | `8ebf5bd485275af895aa83ea3e55c416fe76aa4fefc58e0b9e6d344852bde5aa` |

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
- `<data-dir>/appropedia/runtime/`: full English sustainability/DIY articles,
  redirect aliases and local images; original ZIM retained in `raw/`.
- `<data-dir>/wikihow/runtime/`: streamed full CSV articles, mapped to original
  rows in `raw/wikihowAll.csv`; malformed-width rows go to build-only `quarantine.jsonl`
  and are counted in the manifest. No images or per-row author metadata exist in this source.

`merge_catalogs.py` builds a fresh stage, hardlinks article/media files, retains
distinct source licenses, and remaps shared alias groups after deterministic
sorting. It rejects incomplete sources, conflicting IDs/categories, symlinks,
missing files, and unequal media with a colliding name. Source caches and release
storage must share a filesystem; article/media files are not duplicated during
the merge. `prepare_search.py` then rebuilds postings for the combined item
positions. The completed runtime, index, and constituent-source receipt are
renamed into the immutable release directory. Source/download/indexing failures leave
the currently served release intact. Published runtime directories use mode 0755
so the unprivileged web service can traverse them; linked source files are not
modified to achieve this.

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

Historical three-source verification (before noun-backed and expanded releases):
`f55559d274f784f9e90e11cc3b1bb9182dbc2b37abd16c236c8727873d6411c3`:

| Source | Entries | Media files |
| --- | ---: | ---: |
| iFixit | 103,473 | 575,002 |
| WikiMed | 104,200 | 104,500 |
| First Aid | 18 | 227 |
| Combined | 207,691 | 679,729 |

Its prepared index is 33,169,530 bytes, or 9,965,255 bytes compressed, with SHA-256
`806b19edc9f55c34750f63bcca949fcf3457b614a58286e8ad5aaccfa6f0048d`.
It contains 190,591 surface terms and 55,904 stem mappings. A repeated canonical
build reused the completed release through its metadata receipt. Local HTTP
checks opened a guide and image from each source, retained distinct licenses and
credits, and executed searches with one index fetch and no query-time requests.
The WikiMed source build took 634.7 seconds; the 227-page PDF build took 6.37 seconds
on this workstation. These are ingestion measurements, not page or search latency.

EC2 completed the same pinned pipeline on ARM64, downloading and building its
own three sources. Counts, compressed size, dictionary, and index SHA-256 match
the local release exactly. An unchanged cloud build reused its completion receipt
in 0.59 seconds including SSH, without running ingestion. No local corpus was
transferred.

The full combined catalog remains resident in the browser. Returning only the
best 100 matches reduces result ranking/storage work; it does not shrink the
initial index download or parsing cost. Public deployment verification is recorded
in [the deployment notes](../deploy/README.md): application release `d6d28d6`
serves this catalog locally and on apex/www, with the same index checksum and
successful representative page/image checks for all three sources.

Use `CATALOG_DIR=<printed release path> go run .` to serve a completed snapshot.
The obsolete iFixit-only audit script has been retired; its historical summary
remains below. It depended on a removed scratch database and an old directory
layout. Catalog-wide English-content filtering remains pending. Never rebuild a
source runtime while a server serves it; serve an immutable release instead.

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

The browser worker intersects posting lists and keeps the best 100 matches using
bounded ranking. The page renders the first guide inline, prefetches ranks two
through five, and offers ranks two through six in a side list. The centered
article keeps the hero editable; the prior answer remains until its replacement
is ready. Empty clarification answers still search the first subject.
There is no Show more or Clear search control, and empty results stay blank.
Search supports word prefixes while typing and bounded
typo matching through a compact word trie: one Levenshtein edit for 5–7 letters,
two for words of at least 8 letters. Typo recovery applies to ASCII letter words
when literal/prefix/stem lookup or the combined result is empty. Exact/model matches rank ahead of expanded
matches; numeric-bearing query chunks remain literal. All dictionaries and
postings are prepared before publication. Queries use only the browser's loaded
data, with no server or external search requests.

Tests use tiny generated fixtures and the pinned native dependencies:

```sh
python3 scripts/run_bounded.py -- uv run --python 3.12.12 --with libzim==4.1.0 --with lxml==6.1.3 --with pymupdf==1.28.2 --with wordfreq==3.1.1 --with langid==1.1.6 --with numpy==2.2.6 python -m unittest discover -s scripts -p 'test_*.py'
node --test scripts/test_search_worker.cjs
```

For isolated source work, all five `ingest_*.py` scripts accept `--data-dir` and
`--download-only` / `--build-only`; the canonical builder handles all required
download, extraction, alias, merge, and posting stages automatically.
`bench_search.cjs` compares actual worker computation against a saved
baseline without opening a browser; its numbers are not browser end-to-end latency.

On the completed 207,691-entry index, the best-100 worker and compact-trie
startup optimization were compared with the prior uncapped worker (Linux x64,
Intel i7-13650HX, Node v26.12.0). The multisource suite measures 270 searches after
three warmup rounds:

| Probe | Prior worker | Best 100 + compact trie |
| --- | ---: | ---: |
| Worker preparation | 659 ms | 493 ms |
| Local index read/parse through worker ready | 912 ms | 757 ms |
| All 27 queries, warm p95 | 2.34 ms | 2.43 ms |
| Common 10 repair queries, warm p95 | 2.21 ms | 2.70 ms |
| Medical/First Aid queries, warm p95 | 0.80 ms | 0.93 ms |
| Broad `a`, warm p50 / p95 | 17.04 / 36.72 ms | 9.20 / 21.81 ms |
| Broad `r`, warm p50 / p95 | 17.23 / 18.55 ms | 13.62 / 16.34 ms |
| Prepared JS heap growth | 108.57 MiB | 108.58 MiB |
| Prepared external memory growth (includes ArrayBuffers) | 15.79 MiB | 15.79 MiB |

The benefit is lower broad-query work and faster trie preparation; ordinary
queries remain a few milliseconds, with no general speedup demonstrated by this
sample. The initial 9,965,255-byte compressed index and resident index memory are
unchanged. Broad-query tails can still exceed one frame. Fresh-worker cold probes
take 12.06 ms for `laptpp batery`, 18.14 ms for `refrxxerator`, and 11.44 ms for
`noresultzzzz`. Startup excludes network transfer and painting; local read/parse
is included only in the second row. Memory settles GC identically before and
after startup; external memory already includes ArrayBuffers.

Thirteen worker tests passed, and 37 queries against the real combined index
returned exactly the first 100 results from the previous full ranking, including
ties, aliases, strict models, and empty queries. Each worker fetched the index
once and made no requests while searching. The complete catalog remains searchable;
each query exposes at most its 100 best matches.

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
Those historical builds kept every match; single-letter warm p95 was 11.13 ms
for `a` and 14.85 ms for `r`. The current best-100 policy supersedes that behavior.

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
# Include medical and First Aid queries alongside the repair probes:
node --expose-gc scripts/bench_search.cjs --suite multisource BEFORE.json BEFORE-worker.js AFTER.json assets/search-worker.js
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
