package main

import (
	"bytes"
	"compress/gzip"
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
)

func testHandler(t *testing.T) *siteHandler {
	t.Helper()
	handler, err := newHandler()
	if err != nil {
		t.Fatal(err)
	}
	return handler
}

func requestSite(handler http.Handler, method, target, encoding, etag string) *httptest.ResponseRecorder {
	request := httptest.NewRequest(method, target, nil)
	request.Header.Set("Accept-Encoding", encoding)
	if etag != "" {
		request.Header.Set("If-None-Match", etag)
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	return response
}

func TestPublicAssetsAndPrivatePaths(t *testing.T) {
	handler := testHandler(t)
	for _, asset := range []struct{ target, contentType, contains string }{
		{"/", "text/html", "please, mend my"},
		{"/index.html", "text/html", "please, mend my"},
		{"/assets/backgrounds.css", "text/css", ".atmosphere"},
		{"/assets/backgrounds.js", "text/javascript", "pickMendVariant"},
		{"/assets/fonts/pixelify-sans.woff2", "font/woff2", "wOF2"},
		{"/assets/fonts/pixelify-sans-OFL.txt", "text/plain", "SIL OPEN FONT LICENSE"},
	} {
		t.Run(asset.target, func(t *testing.T) {
			response := requestSite(handler, http.MethodGet, asset.target, "", "")
			if response.Code != http.StatusOK || !strings.HasPrefix(response.Header().Get("Content-Type"), asset.contentType) || !strings.Contains(response.Body.String(), asset.contains) {
				t.Fatalf("unexpected public response: status=%d type=%q", response.Code, response.Header().Get("Content-Type"))
			}
		})
	}
	for _, target := range []string{"/AGENTS.md", "/main.go", "/go.mod", "/.git/config", "/assets/", "/assets/fonts/", "/assets/../AGENTS.md", "/missing"} {
		if response := requestSite(handler, http.MethodGet, target, "", ""); response.Code != http.StatusNotFound {
			t.Errorf("private/unknown path %s returned %d", target, response.Code)
		}
	}
	response := requestSite(handler, http.MethodPost, "/", "", "")
	if response.Code != http.StatusMethodNotAllowed || response.Header().Get("Allow") != "GET, HEAD" {
		t.Fatal("POST must be rejected with supported methods")
	}
}

func TestCompressionHEADAndConditionalCache(t *testing.T) {
	handler := testHandler(t)
	plain := requestSite(handler, http.MethodGet, "/", "", "")
	compressed := requestSite(handler, http.MethodGet, "/", "gzip", "")
	if compressed.Code != http.StatusOK || compressed.Header().Get("Content-Encoding") != "gzip" || compressed.Header().Get("Vary") != "Accept-Encoding" {
		t.Fatal("gzip response must negotiate and vary by encoding")
	}
	compressedLength := compressed.Body.Len()
	reader, err := gzip.NewReader(compressed.Body)
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := io.ReadAll(reader)
	if err != nil {
		t.Fatal(err)
	}
	_ = reader.Close()
	if !bytes.Equal(decoded, plain.Body.Bytes()) || compressedLength >= plain.Body.Len() {
		t.Fatal("gzip must preserve bytes and reduce response size")
	}
	if plain.Header().Get("ETag") == "" || compressed.Header().Get("ETag") == plain.Header().Get("ETag") {
		t.Fatal("different representations must have their own ETags")
	}
	for _, encoding := range []string{"", "gzip"} {
		get := requestSite(handler, http.MethodGet, "/", encoding, "")
		head := requestSite(handler, http.MethodHead, "/", encoding, "")
		if head.Code != http.StatusOK || head.Body.Len() != 0 || head.Header().Get("Content-Length") != strconv.Itoa(get.Body.Len()) {
			t.Fatalf("HEAD for %q must expose GET length without a body", encoding)
		}
		conditional := requestSite(handler, http.MethodGet, "/", encoding, get.Header().Get("ETag"))
		if conditional.Code != http.StatusNotModified || conditional.Body.Len() != 0 || conditional.Header().Get("Cache-Control") != "no-cache" {
			t.Fatalf("unchanged %q representation must return an empty 304", encoding)
		}
	}
	for _, negotiation := range []struct {
		header, encoding string
		status           int
	}{
		{"gzip;q=0", "", http.StatusOK},
		{"gzip;q=0.5, identity;q=0", "gzip", http.StatusOK},
		{"*;q=0, gzip;q=1", "gzip", http.StatusOK},
		{"gzip;q=0, identity;q=0", "", http.StatusNotAcceptable},
	} {
		response := requestSite(handler, http.MethodGet, "/", negotiation.header, "")
		if response.Code != negotiation.status || response.Header().Get("Content-Encoding") != negotiation.encoding {
			t.Errorf("encoding %q: status=%d encoding=%q", negotiation.header, response.Code, response.Header().Get("Content-Encoding"))
		}
	}
}

func TestFontRangesAndHealth(t *testing.T) {
	handler := testHandler(t)
	request := httptest.NewRequest(http.MethodGet, "/assets/fonts/pixelify-sans.woff2", nil)
	request.Header.Set("Range", "bytes=0-3")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusPartialContent || response.Body.String() != "wOF2" || !strings.HasPrefix(response.Header().Get("Content-Range"), "bytes 0-3/") {
		t.Fatal("font byte ranges must preserve binary content")
	}
	for _, method := range []string{http.MethodGet, http.MethodHead} {
		response := requestSite(handler, method, "/healthz", "", "")
		if response.Code != http.StatusOK || response.Header().Get("Cache-Control") != "no-store" {
			t.Fatal("health check must succeed without caching")
		}
		if method == http.MethodHead && response.Body.Len() != 0 || method == http.MethodGet && response.Body.String() != "ok\n" {
			t.Fatal("health response must obey GET/HEAD semantics")
		}
	}
}

func TestPartialTLSConfigurationFailsBeforeListening(t *testing.T) {
	for _, provided := range []string{"TLS_CERT", "TLS_KEY"} {
		t.Run(provided, func(t *testing.T) {
			t.Setenv("ADDR", "invalid listening address")
			t.Setenv("TLS_CERT", "")
			t.Setenv("TLS_KEY", "")
			t.Setenv(provided, "/missing/credential.pem")
			err := serve(context.Background())
			if err == nil || err.Error() != "TLS_CERT and TLS_KEY must be set together" {
				t.Fatalf("partial TLS configuration must fail before binding: %v", err)
			}
		})
	}
}
