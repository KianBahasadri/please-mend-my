# please, mend my

A responsive typing page with rotating typography and subtle monochrome lighting.

Thirteen hero styles rotate without consecutive repeats. Alongside pixel, serif,
comic, poster, typewriter, handwritten, and emoji styles, six repair treatments
add stitched lettering, a torn tape patch, tool-shaped letters, riveted spare
parts, blueprint marks, and cut-paper lettering. The new treatments decorate only
the protected prompt; the editable text and caret stay native. The idle typing
floaters share the same styles. Artwork uses local fonts, CSS, and inline vectors
in the system light/dark palette; backgrounds keep their three-load rotation.
Prefix measurements run during fitting and font changes, never during ordinary
typing.

Both typing areas are underlined, matching the little idle typing animations.
Hero clarification and results share one settle timer: 380 ms after a recognized
trailing word of at least two letters, or 1000 ms for an unfamiliar token, typo,
or model number. The worker supplies its resident dictionary once at startup;
word checks then run synchronously in the page. Enter, Tab, or a noun-ending
`?`, `.`, or `!` settles immediately; typed punctuation remains intact. Clearing
an input cancels pending work and hides the corresponding UI immediately.

Settled bare subjects such as `bike` reveal `What's wrong with your bike?` and
focus the blank native answer beneath it. Cards stay hidden until that answer
has content. The first line and question retain the full container width; a
workspace below pairs the answer with a bicycle diagram and a scrollable parts
list. Diagram and list highlights follow hover, focus, selection, and matching
typed answers. Selecting a part fills the answer and searches immediately;
selecting that unchanged part again clears it. Arm, laptop, phone, car, stove,
toilet, refrigerator, and jeans have list-only specifiers. On smaller screens,
the specifier moves beneath the answer, with its diagram and list still paired.
The bicycle reuses [Al2's Wikimedia drawing](https://commons.wikimedia.org/wiki/File:Bicycle_diagram.svg)
under CC BY 3.0 and a headlight from [WRomey's Openclipart bicycle](https://openclipart.org/detail/3891/bicycle)
under CC0. The adapted artwork and credits are served locally; pinned originals
and license records live in [the source directory](docs/references/bicycle/).

Backspace on an already-empty answer returns to the end of the first line and
suppresses reopening. The protected prompt/question and single native caret are
preserved. Full phrases such as `broken arm` use the first line alone. Search
combines only the editable answers locally. Returning from a guide settles
immediately. Only guides and articles appear as cards; topic files and direct
`?guide=<id>` links remain available. Bicycle component matches favor actual
bicycle procedures over shared alias collisions and unrelated dirt-bike guides.
Each font keeps its size when the full question fits; otherwise the hero scales
down to keep the question on one line. Long editable answers still wrap.
Broad queries favor general entries over niche model guides; exact model searches
keep their priority. Complete title matches also win when the query reorders the
words, so `arm broken` favors `Broken arm` over a detailed eyeglasses repair.
Conversational filler and intensifiers are removed before local lookup, so
`arm it's broken` searches like `arm broken` and `fucking dick` like `dick`.
Negation, symptoms, numeric model identifiers, and exact title priority remain.
The hero uses those same filler rules when deciding whether to ask a follow-up:
`fucking dick` supports Enter just like `dick`, while the original input remains
editable and the question uses the cleaned subject.
The tab title follows the first hero line immediately, including its typed text.

Check the hero and search interactions without opening a browser:

```sh
node --test scripts/test_hero.cjs scripts/test_hero_styles.cjs scripts/test_hero_search.cjs scripts/test_specifiers.cjs scripts/test_search_worker.cjs scripts/test_structured_guides.cjs
```

These controller tests cover timers, keyboard/selection behavior, focus, query
wiring, and ranking; they do not render CSS or measure actual font layout.
See [specifier integration and measurements](docs/specifiers.md) for the shared
controller contract and local performance results.

Run locally with Go 1.25+:

```sh
go run .
# http://localhost:8080
```

Set `ADDR` to change the listening address, for example `ADDR=127.0.0.1:9000 go run .`.

Serve an ingested repair catalog locally with
`CATALOG_DIR=./catalog-data/runtime ADDR=127.0.0.1:8080 go run .`.
Build or audit that snapshot with the commands in [scripts/README.md](scripts/README.md).
`GET /api/catalog` supplies the complete compact browser-search index;
`GET /api/guides/{id}` supplies full guide/topic content, and `/media/{filename}`
serves content-addressed local images. Catalog data stays outside the binary and Git.
`python3 scripts/build_catalog.py` runs the pinned ingestion pipeline locally or
on EC2. Production downloads its own archive and builds its own catalog;
deployment never uploads the local corpus. See [deployment](deploy/README.md).
External lookups run during ingestion only. User searches use the prepared
browser index and same-origin catalog endpoints; page and worker connection
policies block requests to outside services.

Guide responses support both original HTML and offline-generated v2 steps,
checklists, safety notes, and code-copy controls. The build overlays validated
structured guides before publication; the Go handler keeps serving static,
precompressed files. See [the restructuring pipeline](docs/structured-guides.md)
for the repeatable source-backed pilot, cache, and local rollout commands.

Build a self-contained Linux ARM64 binary for a Graviton EC2 instance:

```sh
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath -ldflags="-s -w" -o /tmp/please-mend-my .
go test ./...
```

The binary embeds the HTML, background/search modules, WOFF2 fonts, and their required OFL notices. Rebuild it after site edits. The landing page needs no working directory; repair content is loaded from `CATALOG_DIR`. There are no third-party Go dependencies.

Embedded assets are prepared once at startup and served from memory. Text responses use precomputed gzip when accepted, with representation-specific ETags, conditional 304 responses, HEAD, and byte ranges. WOFF2 fonts retain their existing compression and cache for one day; HTML/CSS/JS revalidate so updates appear promptly. Embedded-asset requests perform no filesystem reads, hashing, compression, or per-request logging.

`GET /healthz` returns `ok`. Header/read/write/idle timeouts bound stalled connections. SIGINT or SIGTERM allows up to ten seconds for active requests to finish.

Optional native HTTPS runs alongside HTTP. Set both `TLS_CERT` and `TLS_KEY` to PEM file paths; `TLS_ADDR` defaults to `:443`. Certificates load once at startup, so restart after replacing them. HTTPS supports HTTP/2 and requires TLS 1.2 or newer. Both listeners share the prepared assets and drain together on shutdown; invalid credentials or a bind failure stop startup without leaving a partial service running.

TLS uses X25519/P-256 key exchange to keep handshake flights small on the origin network path.

The catalog completion manifest and precompressed index are verified at startup;
an interrupted or unfinished ingestion is rejected. Guide
responses use ingestion-time gzip, content ETags and a bounded 64 MiB memory cache.
The first guide prefetch reads and hashes its files; warm guide hits use the cache.
No request performs compression or database queries. Media supports byte ranges
and immutable caching. Runtime files are confined with `os.OpenRoot`; private
source archives, inventory databases, and arbitrary files are never public routes.
