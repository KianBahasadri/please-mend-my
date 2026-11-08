package main

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func writeFixtureJSON(t *testing.T, directory, name string, body []byte) {
	t.Helper()
	filename := filepath.Join(directory, name)
	if err := os.MkdirAll(filepath.Dir(filename), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filename, body, 0o644); err != nil {
		t.Fatal(err)
	}
	var buffer bytes.Buffer
	writer := gzip.NewWriter(&buffer)
	if _, err := writer.Write(body); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filename+".gz", buffer.Bytes(), 0o644); err != nil {
		t.Fatal(err)
	}
}

func archetypeFixture(t *testing.T) (directory, artURL string, art []byte) {
	t.Helper()
	directory, index := catalogFixture(t)
	art = []byte(`{"version":1,"id":"bicycle","viewBox":"0 0 360 220","context":[],"parts":{"chain":[["path",{"d":"M 100 150 H 200","stroke":"currentColor"}]]}}`)
	hash := fmt.Sprintf("%x", sha256.Sum256(art))
	artURL = "/api/art/" + hash + ".json"
	registry := map[string]any{
		"version": 1, "revision": "test-v1",
		"archetypes": []any{map[string]any{
			"id": "bicycle", "label": "Bicycle", "aliases": []string{"bicycle", "bike"}, "guideCount": 1,
			"parts":  []any{map[string]any{"id": "chain", "label": "Chain", "query": "chain", "aliases": []string{"chain"}}},
			"artUrl": artURL,
			"attribution": map[string]any{
				"title": "Bicycle diagram", "author": "Artist <script>alert(1)</script>",
				"source":  "https://commons.wikimedia.org/wiki/File:Bicycle_diagram.svg",
				"license": "CC BY 3.0", "licenseUrl": "https://creativecommons.org/licenses/by/3.0/",
				"changes": "Adapted into selectable monochrome parts.",
				"supplementary": []any{map[string]any{
					"title": "Bicycle light", "author": "Second artist", "source": "https://openclipart.org/detail/3891/bicycle",
					"license": "CC0 1.0", "licenseUrl": "https://creativecommons.org/publicdomain/zero/1.0/", "changes": "Extracted headlight geometry.",
				}},
			},
		}}, "models": []any{},
	}
	body, err := json.Marshal(registry)
	if err != nil {
		t.Fatal(err)
	}
	writeFixtureJSON(t, directory, "archetypes.json", body)
	writeFixtureJSON(t, directory, "art/"+hash+".json", art)
	var document map[string]json.RawMessage
	if err := json.Unmarshal(index, &document); err != nil {
		t.Fatal(err)
	}
	document["archetypes"] = body
	index, err = json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	writeFixtureJSON(t, directory, "index.json", index)
	credits := []byte(`<section><h2>Diagram artwork</h2><a href="https://commons.wikimedia.org/wiki/File:Bicycle_diagram.svg">Bicycle diagram</a><p>Artist &lt;script&gt;alert(1)&lt;/script&gt;</p><a href="https://creativecommons.org/licenses/by/3.0/">CC BY 3.0</a><a href="https://openclipart.org/detail/3891/bicycle">Second artist</a><a href="https://creativecommons.org/publicdomain/zero/1.0/">CC0</a><p>Adapted into selectable monochrome parts.</p></section>`)
	if err := os.WriteFile(filepath.Join(directory, "diagram-credits.html"), credits, 0o644); err != nil {
		t.Fatal(err)
	}
	packed, err := os.ReadFile(filepath.Join(directory, "archetypes.json.gz"))
	if err != nil {
		t.Fatal(err)
	}
	manifest, err := json.Marshal(map[string]any{"version": 1, "revision": "test-v1", "runtimeComplete": true, "indexedItems": 1, "indexBytes": len(index), "archetypes": map[string]any{"version": 1, "count": 1, "models": 0, "bytes": len(body), "gzipBytes": len(packed), "sha256": fmt.Sprintf("%x", sha256.Sum256(body)), "artFiles": 1}, "diagramCredits": map[string]any{"bytes": len(credits), "sha256": fmt.Sprintf("%x", sha256.Sum256(credits))}})
	if err != nil {
		t.Fatal(err)
	}
	writeFixtureJSON(t, directory, "manifest.json", manifest)
	return directory, artURL, art
}

func TestReleaseArchetypesAndArtRepresentations(t *testing.T) {
	directory, artURL, art := archetypeFixture(t)
	t.Setenv("CATALOG_DIR", directory)
	handler := testHandler(t)
	for _, target := range []string{"/api/archetypes", artURL} {
		plain := requestSite(handler, http.MethodGet, target, "", "")
		if plain.Code != http.StatusOK || plain.Header().Get("Content-Type") != "application/json; charset=utf-8" || !json.Valid(plain.Body.Bytes()) {
			t.Fatalf("invalid published JSON: %s", target)
		}
		expectedCache := "no-cache"
		if target == artURL {
			expectedCache = "public, max-age=31536000, immutable"
			if !bytes.Equal(plain.Body.Bytes(), art) {
				t.Fatal("diagram geometry changed")
			}
		}
		if plain.Header().Get("Cache-Control") != expectedCache {
			t.Fatal("incorrect representation cache policy")
		}
		packed := requestSite(handler, http.MethodGet, target, "gzip", "")
		if packed.Code != http.StatusOK || packed.Header().Get("Content-Encoding") != "gzip" {
			t.Fatal("missing release gzip")
		}
		reader, err := gzip.NewReader(packed.Body)
		if err != nil {
			t.Fatal(err)
		}
		body, err := io.ReadAll(reader)
		_ = reader.Close()
		if err != nil || !bytes.Equal(body, plain.Body.Bytes()) {
			t.Fatal("gzip differs from release JSON")
		}
		head := requestSite(handler, http.MethodHead, target, "gzip", "")
		if head.Code != http.StatusOK || head.Body.Len() != 0 || head.Header().Get("Content-Length") != packed.Header().Get("Content-Length") {
			t.Fatal("HEAD must retain the selected representation headers")
		}
		unchanged := requestSite(handler, http.MethodGet, target, "gzip", packed.Header().Get("ETag"))
		if unchanged.Code != http.StatusNotModified || unchanged.Body.Len() != 0 {
			t.Fatal("prepared ETags must avoid retransmitting art")
		}
	}
	home := requestSite(handler, http.MethodGet, "/", "", "").Body.String()
	for _, name := range []string{"bicycle-art.js", "specifier-pilot.js", "/api/archetypes", "/api/art/"} {
		if strings.Contains(home, name) {
			t.Fatalf("HTML must not embed catalog data or issue another registry fetch: %s", name)
		}
	}
	// Registry bytes stay resident after startup, rather than touching the disk on each request.
	if err := os.Remove(filepath.Join(directory, "archetypes.json")); err != nil {
		t.Fatal(err)
	}
	if requestSite(handler, http.MethodGet, "/api/archetypes", "", "").Code != http.StatusOK {
		t.Fatal("registry must stay resident")
	}
	// The first lazy read is cached; art shares the bounded guide working set.
	if err := os.Remove(filepath.Join(directory, strings.TrimPrefix(artURL, "/api/"))); err != nil {
		t.Fatal(err)
	}
	if requestSite(handler, http.MethodGet, artURL, "", "").Code != http.StatusOK {
		t.Fatal("art must remain cached after its first read")
	}
	if handler.catalog.cacheBytes > guideCacheBytes {
		t.Fatal("art must share the bounded response cache")
	}
	for _, target := range []string{"/api/art/../../index.json", "/api/art/" + strings.Repeat("f", 64) + ".json", "/api/art/short.json", "/api/art/" + strings.Repeat("A", 64) + ".json", "/api/art/" + strings.Repeat("0", 64) + ".json/extra", "/catalog-data/build/nouns/catalog.sqlite", "/gaps.json", "/assets/art/manifest.json"} {
		if requestSite(handler, http.MethodGet, target, "", "").Code != http.StatusNotFound {
			t.Fatalf("private or unpinned data must not be served: %s", target)
		}
	}
}

func TestReleaseRejectsInconsistentArchetypes(t *testing.T) {
	for _, mode := range []string{"missing", "changed", "gzip", "credits"} {
		t.Run(mode, func(t *testing.T) {
			directory, _, _ := archetypeFixture(t)
			switch mode {
			case "missing":
				if err := os.Remove(filepath.Join(directory, "archetypes.json")); err != nil {
					t.Fatal(err)
				}
			case "changed":
				writeFixtureJSON(t, directory, "archetypes.json", []byte(`{"version":1,"revision":"other","archetypes":[]}`))
			case "gzip":
				if err := os.WriteFile(filepath.Join(directory, "archetypes.json.gz"), []byte("not gzip"), 0o644); err != nil {
					t.Fatal(err)
				}
			case "credits":
				if err := os.WriteFile(filepath.Join(directory, "diagram-credits.html"), []byte("stale"), 0o644); err != nil {
					t.Fatal(err)
				}
			}
			if catalog, err := openCatalog(directory); err == nil {
				_ = catalog.root.Close()
				t.Fatal("inconsistent registry must fail before the server listens")
			}
		})
	}
}

func TestLazyArtRejectsModifiedOrCompressedMismatch(t *testing.T) {
	for _, mode := range []string{"changed", "gzip", "outside"} {
		t.Run(mode, func(t *testing.T) {
			directory, artURL, _ := archetypeFixture(t)
			filename := filepath.Join(directory, strings.TrimPrefix(artURL, "/api/"))
			switch mode {
			case "changed":
				writeFixtureJSON(t, directory, strings.TrimPrefix(artURL, "/api/"), []byte(`{"version":1,"parts":{}}`))
			case "gzip":
				if err := os.WriteFile(filename+".gz", []byte("bad"), 0o644); err != nil {
					t.Fatal(err)
				}
			case "outside":
				outside := filepath.Join(t.TempDir(), "secret.json")
				if err := os.WriteFile(outside, []byte(`{"private":true}`), 0o600); err != nil {
					t.Fatal(err)
				}
				if err := os.Remove(filename); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(outside, filename); err != nil {
					t.Fatal(err)
				}
			}
			t.Setenv("CATALOG_DIR", directory)
			handler := testHandler(t)
			if requestSite(handler, http.MethodGet, artURL, "", "").Code != http.StatusNotFound {
				t.Fatal("unverified art must not be served")
			}
		})
	}
}
