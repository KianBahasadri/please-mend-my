package main

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
)

func catalogFixture(t *testing.T) (string, []byte) {
	t.Helper()
	directory := t.TempDir()
	for _, name := range []string{"guides", "media"} {
		if err := os.Mkdir(filepath.Join(directory, name), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	files := map[string][]byte{
		"index.json":            []byte(`{"version":1,"revision":"test-v1","source":{"name":"iFixit"},"categories":[{"id":"technology","name":"Technology"}],"fields":["id","title","category","kind","device"],"items":[["guide-123","Replace a screen","technology","guide","Phone"]]}`),
		"guides/guide-123.json": []byte(`{"id":"guide-123","title":"Replace a screen","category":"technology","kind":"guide","device":"Phone","summary":"A complete repair guide.","html":"<h2>Step 1</h2><p>Open the device.</p>","images":["/media/abc123.webp"]}`),
		"media/abc123.webp":     []byte("RIFF fixture WEBP"),
		"media/active.html":     []byte("<script>bad()</script>"),
	}
	files["manifest.json"] = []byte(`{"version":1,"revision":"test-v1","runtimeComplete":true,"indexedItems":1,"indexBytes":` + strconv.Itoa(len(files["index.json"])) + `}`)
	for name, body := range files {
		if err := os.WriteFile(filepath.Join(directory, name), body, 0o644); err != nil {
			t.Fatal(err)
		}
		if strings.HasSuffix(name, ".json") {
			var buffer bytes.Buffer
			writer := gzip.NewWriter(&buffer)
			_, _ = writer.Write(body)
			if err := writer.Close(); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(directory, name+".gz"), buffer.Bytes(), 0o644); err != nil {
				t.Fatal(err)
			}
		}
	}
	return directory, files["index.json"]
}

func writeLocalCatalogSelection(t *testing.T, value string) {
	t.Helper()
	if err := os.MkdirAll("catalog-data", 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile("catalog-data/local-catalog.path", []byte(value), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestRememberedCatalogServesLibraryWithoutEnvironment(t *testing.T) {
	directory, index := catalogFixture(t)
	t.Chdir(t.TempDir())
	t.Setenv("CATALOG_DIR", "")
	writeLocalCatalogSelection(t, directory+"\n")
	selected, err := configuredCatalogDirectory()
	if err != nil || selected != directory {
		t.Fatalf("local startup must reuse the selected catalog: directory=%q error=%v", selected, err)
	}
	handler, err := newHandler(selected)
	if err != nil {
		t.Fatal(err)
	}
	defer handler.Close()
	for _, target := range []string{"/healthz", "/api/catalog", "/api/guides/guide-123"} {
		response := requestSite(handler, http.MethodGet, target, "", "")
		if response.Code != http.StatusOK {
			t.Fatalf("remembered catalog must serve %s: status=%d", target, response.Code)
		}
		if target == "/api/catalog" && !bytes.Equal(response.Body.Bytes(), index) {
			t.Fatal("remembered selection must serve the complete selected index")
		}
	}
}

func TestExplicitCatalogOverridesLocalSelection(t *testing.T) {
	directory, _ := catalogFixture(t)
	t.Chdir(t.TempDir())
	t.Setenv("CATALOG_DIR", directory)
	writeLocalCatalogSelection(t, "invalid\nselection\n")
	selected, err := configuredCatalogDirectory()
	if err != nil || selected != directory {
		t.Fatalf("explicit deployment catalog must win: directory=%q error=%v", selected, err)
	}
}

func TestCatalogSelectionFailsBeforeListening(t *testing.T) {
	for _, selection := range []string{"missing", "empty", "multiline", "invalid", "unfinished"} {
		t.Run(selection, func(t *testing.T) {
			t.Chdir(t.TempDir())
			t.Setenv("CATALOG_DIR", "")
			t.Setenv("TLS_CERT", "")
			t.Setenv("TLS_KEY", "")
			t.Setenv("ADDR", "invalid listening address")
			switch selection {
			case "empty":
				writeLocalCatalogSelection(t, " \n")
			case "multiline":
				writeLocalCatalogSelection(t, "one\ntwo\n")
			case "invalid":
				writeLocalCatalogSelection(t, "/missing/catalog/release\n")
			case "unfinished":
				directory, _ := catalogFixture(t)
				if err := os.Remove(filepath.Join(directory, "manifest.json")); err != nil {
					t.Fatal(err)
				}
				writeLocalCatalogSelection(t, directory+"\n")
			}
			err := serve(context.Background())
			if err == nil || strings.Contains(err.Error(), "listen on") {
				t.Fatalf("bad catalog selection must fail before binding a healthy-looking server: %v", err)
			}
		})
	}
}

func TestCatalogResponsesCachingAndRanges(t *testing.T) {
	directory, index := catalogFixture(t)
	t.Setenv("CATALOG_DIR", directory)
	handler := testHandler(t)
	for _, target := range []string{"/api/catalog", "/api/guides/guide-123"} {
		plain := requestSite(handler, http.MethodGet, target, "", "")
		if plain.Code != http.StatusOK || !json.Valid(plain.Body.Bytes()) || plain.Header().Get("Cache-Control") != "no-cache" {
			t.Fatalf("invalid catalog response for %s: status=%d body=%s", target, plain.Code, plain.Body.String())
		}
		if target == "/api/catalog" && !bytes.Equal(index, plain.Body.Bytes()) {
			t.Fatal("browser index must contain the complete ingested index")
		}
		encoded := requestSite(handler, http.MethodGet, target, "gzip", "")
		if encoded.Code != http.StatusOK || encoded.Header().Get("Content-Encoding") != "gzip" {
			t.Fatalf("precompressed %s response unavailable", target)
		}
		head := requestSite(handler, http.MethodHead, target, "gzip", "")
		if head.Code != http.StatusOK || head.Body.Len() != 0 || head.Header().Get("Content-Length") != encoded.Header().Get("Content-Length") {
			t.Fatal("catalog HEAD must preserve compressed representation headers")
		}
		conditional := requestSite(handler, http.MethodGet, target, "gzip", encoded.Header().Get("ETag"))
		if conditional.Code != http.StatusNotModified || conditional.Body.Len() != 0 {
			t.Fatalf("prefetched %s should revalidate with an empty 304", target)
		}
	}
	request := httptest.NewRequest(http.MethodGet, "/media/abc123.webp", nil)
	request.Header.Set("Range", "bytes=0-3")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != http.StatusPartialContent || response.Body.String() != "RIFF" || response.Header().Get("Cache-Control") != "public, max-age=31536000, immutable" {
		t.Fatal("content-addressed media should support immutable caching and byte ranges")
	}
}

func TestCatalogNeverExposesOutsideFilesOrActiveMedia(t *testing.T) {
	directory, _ := catalogFixture(t)
	outside := filepath.Join(t.TempDir(), "secret.json")
	if err := os.WriteFile(outside, []byte(`{"secret":"private"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(directory, "guides", "private.json")); err != nil {
		t.Fatal(err)
	}
	t.Setenv("CATALOG_DIR", directory)
	handler := testHandler(t)
	for _, target := range []string{"/api/guides/private", "/api/guides/../index", "/api/guides/%2e%2e", "/api/guides/guide-123/extra", "/api/guides/missing", "/media/../index.json", "/media/active.html", "/media/abc123.webp/extra", "/catalog-data/index.json"} {
		response := requestSite(handler, http.MethodGet, target, "", "")
		if response.Code != http.StatusNotFound {
			t.Errorf("%s should be inaccessible; received %d", target, response.Code)
		}
	}
}

func TestCatalogConcurrentVisibleCardPrefetch(t *testing.T) {
	directory, _ := catalogFixture(t)
	t.Setenv("CATALOG_DIR", directory)
	handler := testHandler(t)
	var workers sync.WaitGroup
	for range 24 {
		workers.Add(1)
		go func() {
			defer workers.Done()
			response := requestSite(handler, http.MethodGet, "/api/guides/guide-123", "gzip", "")
			if response.Code != http.StatusOK || response.Header().Get("ETag") == "" {
				t.Errorf("concurrent guide prefetch failed: %d", response.Code)
			}
		}()
	}
	workers.Wait()
}

func TestCatalogRejectsMismatchedCompressedIndex(t *testing.T) {
	directory, _ := catalogFixture(t)
	incorrect, err := os.ReadFile(filepath.Join(directory, "guides", "guide-123.json.gz"))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(directory, "index.json.gz"), incorrect, 0o644); err != nil {
		t.Fatal(err)
	}
	if handler, err := openCatalog(directory); err == nil {
		_ = handler.root.Close()
		t.Fatal("startup must reject stale compressed data rather than serve a different browser index")
	}
}

func TestCatalogRejectsUnfinishedSnapshot(t *testing.T) {
	directory, _ := catalogFixture(t)
	if err := os.Remove(filepath.Join(directory, "manifest.json")); err != nil {
		t.Fatal(err)
	}
	if handler, err := openCatalog(directory); err == nil {
		_ = handler.root.Close()
		t.Fatal("an index written before the ingestion completion manifest must not be served")
	}
}
