package main

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
)

func TestDiagramArtifactsHavePinnedLocalURLsAndPreparedRepresentations(t *testing.T) {
	handler := testHandler(t)
	response := requestSite(handler, http.MethodGet, "/assets/art/manifest.json", "", "")
	if response.Code != http.StatusOK || response.Header().Get("Content-Type") != "application/json; charset=utf-8" {
		t.Fatal("diagram manifest must be explicitly embedded JSON")
	}
	if response.Header().Get("Cache-Control") != "no-cache" {
		t.Fatal("the mutable manifest must revalidate")
	}
	var manifest struct {
		Version    int `json:"version"`
		Archetypes []struct {
			ID     string `json:"id"`
			ArtURL string `json:"artUrl"`
		} `json:"archetypes"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &manifest); err != nil || manifest.Version != 1 {
		t.Fatalf("invalid diagram manifest: %v", err)
	}
	for _, entry := range manifest.Archetypes {
		if entry.ArtURL == "" {
			continue
		}
		plain := requestSite(handler, http.MethodGet, entry.ArtURL, "", "")
		if plain.Code != http.StatusOK || !json.Valid(plain.Body.Bytes()) {
			t.Fatalf("published diagram %s must serve valid JSON", entry.ID)
		}
		expected := fmt.Sprintf("/assets/art/%s-%x.json", entry.ID, sha256.Sum256(plain.Body.Bytes()))
		if entry.ArtURL != expected {
			t.Fatalf("diagram URL must pin its exact content: %s != %s", entry.ArtURL, expected)
		}
		if plain.Header().Get("Cache-Control") != "public, max-age=31536000, immutable" {
			t.Fatal("content-addressed diagram assets must remain cacheable")
		}
		packed := requestSite(handler, http.MethodGet, entry.ArtURL, "gzip", "")
		if packed.Code != http.StatusOK || packed.Header().Get("Content-Encoding") != "gzip" {
			t.Fatal("diagram ASTs must have prepared gzip")
		}
		reader, err := gzip.NewReader(packed.Body)
		if err != nil {
			t.Fatal(err)
		}
		body, err := io.ReadAll(reader)
		_ = reader.Close()
		if err != nil || !bytes.Equal(body, plain.Body.Bytes()) {
			t.Fatal("diagram gzip changed its contents")
		}
		head := requestSite(handler, http.MethodHead, entry.ArtURL, "gzip", "")
		if head.Code != http.StatusOK || head.Body.Len() != 0 || head.Header().Get("Content-Length") != packed.Header().Get("Content-Length") {
			t.Fatal("diagram HEAD must preserve the compressed representation headers")
		}
		unchanged := requestSite(handler, http.MethodGet, entry.ArtURL, "gzip", packed.Header().Get("ETag"))
		if unchanged.Code != http.StatusNotModified || unchanged.Body.Len() != 0 {
			t.Fatal("cached diagrams must revalidate without retransmitting their contents")
		}
	}
	home := requestSite(handler, http.MethodGet, "/", "", "").Body.String()
	if pilot, registry := strings.Index(home, "/assets/specifier-pilot.js"), strings.Index(home, "/assets/specifiers.js"); pilot < 0 || registry < pilot {
		t.Fatal("the tiny resident registry must load before the specifier controller")
	}
	for _, name := range []string{"/docs/references/specifiers/sources.json", "/catalog-data/build/diagram-pilot/manifest.json", "/assets/art/missing.json"} {
		if requestSite(handler, http.MethodGet, name, "", "").Code != http.StatusNotFound {
			t.Fatalf("only published diagram artifacts may be served: %s", name)
		}
	}
}
