package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestShellManifestCoversEveryEmbeddedAssetAndPinsWorker(t *testing.T) {
	handler := testHandler(t)
	response := requestSite(handler, http.MethodGet, "/shell-manifest.json", "", "")
	if response.Code != http.StatusOK {
		t.Fatal("shell manifest must be available")
	}
	var manifest struct {
		Version string      `json:"version"`
		Assets  []shellFile `json:"assets"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &manifest); err != nil || len(manifest.Version) != 64 {
		t.Fatal("invalid deterministic shell version", err)
	}
	seen := map[string]bool{}
	for _, entry := range manifest.Assets {
		asset := handler.assets[entry.URL]
		if asset == nil || seen[entry.URL] {
			t.Fatal("missing or duplicated shell asset", entry.URL)
		}
		seen[entry.URL] = true
		sum := sha256.Sum256(asset.plain.body)
		if hex.EncodeToString(sum[:]) != entry.SHA256 {
			t.Fatal("shell hash must describe decoded source bytes")
		}
	}
	for target := range handler.assets {
		if target == "/" || target == "/credits" || strings.HasPrefix(target, "/assets/") {
			if !seen[target] {
				t.Fatal("shell inventory omitted embedded asset", target)
			}
		}
	}
	worker := requestSite(handler, http.MethodGet, "/sw.js", "", "")
	if worker.Code != http.StatusOK || worker.Header().Get("Service-Worker-Allowed") != "/" ||
		!strings.Contains(worker.Body.String(), manifest.Version) || strings.Contains(worker.Body.String(), "__PMM_SHELL_VERSION__") {
		t.Fatal("worker must carry matching inventory identity at root scope")
	}
	if changed := requestSite(handler, http.MethodGet, "/sw.js", "", worker.Header().Get("ETag")); changed.Code != http.StatusNotModified {
		t.Fatal("worker supports normal conditional HTTP freshness")
	}
	before := manifest.Version
	handler.assets["/assets/synthetic.js"], _ = prepareStaticAsset("synthetic.js", "text/javascript", "no-cache", []byte("changed"))
	if err := handler.prepareOfflineShell(); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(handler.assets["/sw.js"].plain.body), before) {
		t.Fatal("any embedded shell change must alter the worker version")
	}
}

func TestCatalogGenerationHeaderMatchesCatalogEncoding(t *testing.T) {
	plain := newRepresentation([]byte("catalog"))
	compressed := newRepresentation([]byte("compressed catalog"))
	catalog := &catalogHandler{index: &staticAsset{plain: plain, compressed: compressed}}
	for _, encoding := range []string{"", "gzip", "gzip;q=0", "gzip;q=0.5, identity;q=0"} {
		request := httptest.NewRequest(http.MethodGet, "/api/guides/example", nil)
		request.Header.Set("Accept-Encoding", encoding)
		expected := plain.etag
		gzipQuality, identityQuality := encodingQualities([]string{encoding})
		if gzipQuality > 0 && gzipQuality >= identityQuality {
			expected = compressed.etag
		}
		if got := catalogETag(catalog, request); got != expected {
			t.Fatal("guide generation must match the browser's catalog representation")
		}
	}
}
