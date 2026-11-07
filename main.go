package main

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"embed"
	"errors"
	"fmt"
	"io/fs"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// Embed only public site files; repository files cannot be requested.
//
//go:embed index.html assets/backgrounds.css assets/backgrounds.js assets/fonts/*.woff2 assets/fonts/*-OFL.txt
var siteFiles embed.FS

type representation struct {
	body []byte
	etag string
}

type staticAsset struct {
	name         string
	contentType  string
	cacheControl string
	plain        representation
	compressed   representation
}

type siteHandler struct {
	assets map[string]*staticAsset
}

func newHandler() (*siteHandler, error) {
	handler := &siteHandler{assets: make(map[string]*staticAsset)}
	err := fs.WalkDir(siteFiles, ".", func(name string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() {
			return nil
		}
		body, err := siteFiles.ReadFile(name)
		if err != nil {
			return err
		}
		asset := &staticAsset{name: name, cacheControl: "no-cache", plain: newRepresentation(body)}
		switch path.Ext(name) {
		case ".html":
			asset.contentType = "text/html; charset=utf-8"
		case ".css":
			asset.contentType = "text/css; charset=utf-8"
		case ".js":
			asset.contentType = "text/javascript; charset=utf-8"
		case ".woff2":
			asset.contentType = "font/woff2"
			asset.cacheControl = "public, max-age=86400"
		case ".txt":
			asset.contentType = "text/plain; charset=utf-8"
		default:
			return fmt.Errorf("unexpected embedded asset: %s", name)
		}
		if path.Ext(name) != ".woff2" {
			var compressed bytes.Buffer
			writer, err := gzip.NewWriterLevel(&compressed, gzip.BestCompression)
			if err != nil {
				return err
			}
			if _, err = writer.Write(body); err != nil {
				return err
			}
			if err = writer.Close(); err != nil {
				return err
			}
			if compressed.Len() < len(body) {
				asset.compressed = newRepresentation(compressed.Bytes())
			}
		}
		handler.assets["/"+name] = asset
		if name == "index.html" {
			handler.assets["/"] = asset
		}
		return nil
	})
	return handler, err
}

func newRepresentation(body []byte) representation {
	return representation{body: body, etag: fmt.Sprintf(`"%x"`, sha256.Sum256(body))}
}

func (handler *siteHandler) ServeHTTP(w http.ResponseWriter, request *http.Request) {
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if request.Method != http.MethodGet && request.Method != http.MethodHead {
		w.Header().Set("Allow", "GET, HEAD")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if request.URL.Path == "/healthz" {
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Content-Length", "3")
		if request.Method != http.MethodHead {
			_, _ = w.Write([]byte("ok\n"))
		}
		return
	}
	asset, found := handler.assets[request.URL.Path]
	if !found {
		http.NotFound(w, request)
		return
	}
	selected := &asset.plain
	gzipQuality, identityQuality := encodingQualities(request.Header.Values("Accept-Encoding"))
	if asset.compressed.body != nil {
		w.Header().Set("Vary", "Accept-Encoding")
		if gzipQuality > 0 && gzipQuality >= identityQuality {
			selected = &asset.compressed
			w.Header().Set("Content-Encoding", "gzip")
		}
	}
	if selected == &asset.plain && identityQuality == 0 {
		http.Error(w, "no acceptable content encoding", http.StatusNotAcceptable)
		return
	}
	w.Header().Set("Content-Type", asset.contentType)
	w.Header().Set("Cache-Control", asset.cacheControl)
	w.Header().Set("ETag", selected.etag)
	w.Header().Set("Content-Length", strconv.Itoa(len(selected.body)))
	http.ServeContent(w, request, asset.name, time.Time{}, bytes.NewReader(selected.body))
}

// Negotiate cached representations without allocating a per-request lookup map.
func encodingQualities(headers []string) (gzipQuality, identityQuality float64) {
	gzipQuality, identityQuality, wildcard := -1.0, -1.0, -1.0
	for _, header := range headers {
		for item := range strings.SplitSeq(header, ",") {
			coding, parameters, _ := strings.Cut(item, ";")
			quality := 1.0
			for parameter := range strings.SplitSeq(parameters, ";") {
				name, value, found := strings.Cut(parameter, "=")
				if found && strings.EqualFold(strings.TrimSpace(name), "q") {
					parsed, err := strconv.ParseFloat(strings.TrimSpace(value), 64)
					if err != nil || !(parsed >= 0 && parsed <= 1) {
						quality = 0
					} else {
						quality = parsed
					}
				}
			}
			switch {
			case strings.EqualFold(strings.TrimSpace(coding), "gzip"):
				gzipQuality = quality
			case strings.EqualFold(strings.TrimSpace(coding), "identity"):
				identityQuality = quality
			case strings.TrimSpace(coding) == "*":
				wildcard = quality
			}
		}
	}
	if gzipQuality < 0 {
		gzipQuality = max(wildcard, 0)
	}
	if identityQuality < 0 {
		identityQuality = 1
		if wildcard == 0 {
			identityQuality = 0
		}
	}
	return gzipQuality, identityQuality
}

func run() error {
	handler, err := newHandler()
	if err != nil {
		return fmt.Errorf("prepare static assets: %w", err)
	}
	address := os.Getenv("ADDR")
	if address == "" {
		address = ":8080"
	}
	listener, err := net.Listen("tcp", address)
	if err != nil {
		return err
	}
	server := &http.Server{
		Handler:           handler,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      15 * time.Second,
		IdleTimeout:       60 * time.Second,
		MaxHeaderBytes:    16 << 10,
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	serveError := make(chan error, 1)
	go func() { serveError <- server.Serve(listener) }()
	log.Printf("serving embedded site on %s", listener.Addr())
	select {
	case err = <-serveError:
	case <-ctx.Done():
		stop()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err = server.Shutdown(shutdownCtx); err != nil {
			_ = server.Close()
			return fmt.Errorf("shutdown: %w", err)
		}
		err = <-serveError
	}
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}
