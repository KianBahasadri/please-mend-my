package main

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDiagramPreviewOptInAndConfinedFiles(t *testing.T) {
	handler := testHandler(t)
	if response := requestSite(handler, http.MethodGet, "/diagram-preview/", "", ""); response.Code != http.StatusNotFound {
		t.Fatal("preview must be absent without explicit configuration")
	}
	directory := t.TempDir()
	png := strings.Repeat("a", 64) + ".png"
	for name, body := range map[string]string{"index.html": "<h1>Rejected candidates</h1>", "batch-10-1.html": "<p>All candidates</p>", png: "PNG preview", ".env": "PRIVATE", "unsafe.svg": "<svg/>"} {
		if err := os.WriteFile(filepath.Join(directory, name), []byte(body), 0600); err != nil {
			t.Fatal(err)
		}
	}
	preview, err := openDiagramPreview(directory)
	if err != nil {
		t.Fatal(err)
	}
	handler.preview = preview
	for _, target := range []string{"/diagram-preview/", "/diagram-preview/batch-10-1.html", "/diagram-preview/" + png} {
		response := requestSite(handler, http.MethodGet, target, "", "")
		if response.Code != http.StatusOK || !strings.Contains(response.Header().Get("Content-Security-Policy"), "default-src 'none'") {
			t.Fatalf("expected constrained preview at %s: %d", target, response.Code)
		}
		head := requestSite(handler, http.MethodHead, target, "", "")
		if head.Code != http.StatusOK || head.Body.Len() != 0 {
			t.Fatal("preview HEAD must not emit a body")
		}
	}
	for _, target := range []string{"/diagram-preview/.env", "/diagram-preview/unsafe.svg", "/diagram-preview/../AGENTS.md", "/diagram-preview/sub/index.html"} {
		if response := requestSite(handler, http.MethodGet, target, "", ""); response.Code != http.StatusNotFound {
			t.Fatalf("private preview path %s returned %d", target, response.Code)
		}
	}
	outside := filepath.Join(t.TempDir(), "secret")
	if err := os.WriteFile(outside, []byte("PRIVATE"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(directory, strings.Repeat("b", 64)+".png")); err != nil {
		t.Fatal(err)
	}
	if response := requestSite(handler, http.MethodGet, "/diagram-preview/"+strings.Repeat("b", 64)+".png", "", ""); response.Code != http.StatusNotFound {
		t.Fatal("preview root must reject escaping symlinks")
	}
}

func TestDiagramPreviewRequiresGeneratedIndex(t *testing.T) {
	if _, err := openDiagramPreview(t.TempDir()); err == nil {
		t.Fatal("missing gallery must fail startup before listening")
	}
}
