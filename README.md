# please, mend my

A responsive typing page with independently rotating fonts and monochrome backgrounds.

Run locally with Go 1.25+:

```sh
go run .
# http://localhost:8080
```

Set `ADDR` to change the listening address, for example `ADDR=127.0.0.1:9000 go run .`.
The page can also be opened directly with `firefox index.html`.

Build a self-contained Linux ARM64 binary for a Graviton EC2 instance:

```sh
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath -ldflags="-s -w" -o /tmp/please-mend-my .
go test ./...
```

The binary embeds only the HTML, background modules, WOFF2 fonts, and their required OFL notices. Rebuild it after site edits; no working directory or external assets are needed at runtime. There are no third-party Go dependencies.

Assets are prepared once at startup and served from memory. Text responses use precomputed gzip when accepted, with representation-specific ETags, conditional 304 responses, HEAD, and byte ranges. WOFF2 fonts retain their existing compression and cache for one day; HTML/CSS/JS revalidate so updates appear promptly. Request handling does no filesystem reads, hashing, compression, or per-request logging.

`GET /healthz` returns `ok`. Header/read/write/idle timeouts bound stalled connections. SIGINT or SIGTERM allows up to ten seconds for active requests to finish.

Optional native HTTPS runs alongside HTTP. Set both `TLS_CERT` and `TLS_KEY` to PEM file paths; `TLS_ADDR` defaults to `:443`. Certificates load once at startup, so restart after replacing them. HTTPS supports HTTP/2 and requires TLS 1.2 or newer. Both listeners share the prepared assets and drain together on shutdown; invalid credentials or a bind failure stop startup without leaving a partial service running.
