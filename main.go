package main

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"crypto/tls"
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
//go:embed index.html credits.html assets/*.css assets/*.js assets/fonts/*.woff2 assets/fonts/*-OFL.txt
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
	assets  map[string]*staticAsset
	catalog *catalogHandler
}

func newHandler(catalogDirectory string) (*siteHandler, error) {
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
		asset, err = prepareStaticAsset(name, asset.contentType, asset.cacheControl, body)
		if err != nil {
			return err
		}
		handler.assets["/"+name] = asset
		if name == "index.html" {
			handler.assets["/"] = asset
		} else if name == "credits.html" {
			handler.assets["/credits"] = asset
		}
		return nil
	})
	if err == nil && catalogDirectory != "" {
		handler.catalog, err = openCatalog(catalogDirectory)
	}
	if err == nil {
		err = handler.prepareCredits()
	}
	if err != nil {
		_ = handler.Close()
	}
	return handler, err
}

func prepareStaticAsset(name, contentType, cacheControl string, body []byte) (*staticAsset, error) {
	asset := &staticAsset{name: name, contentType: contentType, cacheControl: cacheControl, plain: newRepresentation(body)}
	if path.Ext(name) == ".woff2" {
		return asset, nil
	}
	var compressed bytes.Buffer
	writer, err := gzip.NewWriterLevel(&compressed, gzip.BestCompression)
	if err != nil {
		return nil, err
	}
	if _, err = writer.Write(body); err != nil {
		return nil, err
	}
	if err = writer.Close(); err != nil {
		return nil, err
	}
	if compressed.Len() < len(body) {
		asset.compressed = newRepresentation(compressed.Bytes())
	}
	return asset, nil
}

func (handler *siteHandler) Close() error {
	if handler.catalog != nil {
		return handler.catalog.root.Close()
	}
	return nil
}

func newRepresentation(body []byte) representation {
	return representation{body: body, etag: fmt.Sprintf(`"%x"`, sha256.Sum256(body))}
}

func (handler *siteHandler) ServeHTTP(w http.ResponseWriter, request *http.Request) {
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Content-Security-Policy", "connect-src 'self'")
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
	if request.URL.Path == "/api/catalog" || request.URL.Path == "/api/archetypes" || strings.HasPrefix(request.URL.Path, "/api/art/") || strings.HasPrefix(request.URL.Path, "/api/guides/") || strings.HasPrefix(request.URL.Path, "/media/") {
		if handler.catalog == nil {
			w.Header().Set("Cache-Control", "no-store")
			http.Error(w, "catalog unavailable", http.StatusServiceUnavailable)
			return
		}
		handler.catalog.ServeHTTP(w, request)
		return
	}
	asset, found := handler.assets[request.URL.Path]
	if !found {
		http.NotFound(w, request)
		return
	}
	serveStaticAsset(w, request, asset)
}

func serveStaticAsset(w http.ResponseWriter, request *http.Request, asset *staticAsset) {
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
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	return serve(ctx)
}

// Local launches reuse one explicitly selected release. Resolve it only at
// startup; never scan releases or inspect the filesystem on search requests.
func configuredCatalogDirectory() (string, error) {
	if directory := os.Getenv("CATALOG_DIR"); directory != "" {
		return directory, nil
	}
	const selection = "catalog-data/local-catalog.path"
	body, err := os.ReadFile(selection)
	if err != nil {
		return "", fmt.Errorf("set CATALOG_DIR to a completed catalog release or save its path in %s: %w", selection, err)
	}
	directory := strings.TrimSpace(string(body))
	if directory == "" || strings.ContainsAny(directory, "\r\n\x00") {
		return "", fmt.Errorf("%s must contain one completed catalog release path", selection)
	}
	return directory, nil
}

func serve(ctx context.Context) error {
	certificateFile, keyFile := os.Getenv("TLS_CERT"), os.Getenv("TLS_KEY")
	if (certificateFile == "") != (keyFile == "") {
		return errors.New("TLS_CERT and TLS_KEY must be set together")
	}
	var tlsConfig *tls.Config
	if certificateFile != "" {
		certificate, err := tls.LoadX509KeyPair(certificateFile, keyFile)
		if err != nil {
			return fmt.Errorf("load TLS certificate and key: %w", err)
		}
		tlsConfig = &tls.Config{
			MinVersion: tls.VersionTLS12,
			// Keep origin handshake flights small on this network path.
			CurvePreferences: []tls.CurveID{tls.X25519, tls.CurveP256},
			Certificates:     []tls.Certificate{certificate},
		}
	}
	catalogDirectory, err := configuredCatalogDirectory()
	if err != nil {
		return err
	}
	handler, err := newHandler(catalogDirectory)
	if err != nil {
		return fmt.Errorf("prepare static assets: %w", err)
	}
	defer handler.Close()
	address := os.Getenv("ADDR")
	if address == "" {
		address = ":8080"
	}
	type endpoint struct {
		address   string
		tlsConfig *tls.Config
	}
	endpoints := []endpoint{{address: address}}
	if tlsConfig != nil {
		tlsAddress := os.Getenv("TLS_ADDR")
		if tlsAddress == "" {
			tlsAddress = ":443"
		}
		endpoints = append(endpoints, endpoint{tlsAddress, tlsConfig})
	}
	type runningServer struct {
		server   *http.Server
		listener net.Listener
	}
	var servers []runningServer
	defer func() {
		for _, running := range servers {
			_ = running.server.Close()
			_ = running.listener.Close()
		}
	}()
	// Prepare all listeners before serving either protocol, so startup is atomic.
	for _, endpoint := range endpoints {
		listener, err := net.Listen("tcp", endpoint.address)
		if err != nil {
			return fmt.Errorf("listen on %s: %w", endpoint.address, err)
		}
		server := &http.Server{
			Handler:           handler,
			ReadHeaderTimeout: 5 * time.Second,
			ReadTimeout:       10 * time.Second,
			WriteTimeout:      15 * time.Second,
			IdleTimeout:       60 * time.Second,
			MaxHeaderBytes:    16 << 10,
			TLSConfig:         endpoint.tlsConfig,
		}
		servers = append(servers, runningServer{server, listener})
	}
	serveErrors := make(chan error, len(servers))
	for _, running := range servers {
		go func() {
			if running.server.TLSConfig != nil {
				serveErrors <- running.server.ServeTLS(running.listener, "", "")
			} else {
				serveErrors <- running.server.Serve(running.listener)
			}
		}()
		protocol := "HTTP"
		if running.server.TLSConfig != nil {
			protocol = "HTTPS"
		}
		log.Printf("serving embedded site over %s on %s", protocol, running.listener.Addr())
	}
	select {
	case err = <-serveErrors:
	case <-ctx.Done():
	}
	if errors.Is(err, http.ErrServerClosed) {
		err = nil
	}
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	shutdownErrors := make(chan error, len(servers))
	for _, running := range servers {
		go func() { shutdownErrors <- running.server.Shutdown(shutdownCtx) }()
	}
	for range servers {
		if shutdownErr := <-shutdownErrors; shutdownErr != nil {
			err = errors.Join(err, fmt.Errorf("shutdown: %w", shutdownErr))
		}
	}
	return err
}

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}
