# please, mend my

A responsive typing page with rotating fonts and subtle monochrome lighting.

Both typing areas are underlined, matching the little idle typing animations.
After 2.5 seconds without typing, bare subjects such as `arm` reveal the hero
question `What's wrong with your arm?` and focus a blank answer field beneath it.
Enter, Tab, or a noun-ending `?`, `.`, or `!` opens it immediately; punctuation
stays normal in full phrases and the answer. Backspace on an empty answer returns
to the end of the first line. The prompt and question stay protected, and only the
focused native textbox has a caret. Full phrases such as `broken arm` use the
first line alone. Search combines the two editable answers locally, excluding
the fixed prompt and question.
Each font keeps its size when the full question fits; otherwise the hero scales
down to keep the question on one line. Long editable answers still wrap.
Broad queries favor general entries over niche model guides; exact model searches
keep their priority.

Check the hero and search interactions without opening a browser:

```sh
node --test scripts/test_hero.cjs scripts/test_hero_search.cjs scripts/test_search_worker.cjs
```

These controller tests cover timers, keyboard/selection behavior, focus, query
wiring, and ranking; they do not render CSS or measure actual font layout.

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
