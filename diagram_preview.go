package main

import (
	"fmt"
	"net/http"
	"os"
	"regexp"
	"strings"
)

// Opt-in local experiment evidence. Nothing from a batch directory is exposed
// directly: the generator exports escaped HTML and content-addressed PNGs only.
type diagramPreview struct{ root *os.Root }

var previewName = regexp.MustCompile(`^(?:index\.html|batch-[0-9]+-[0-9]+\.html|[a-f0-9]{64}\.png|manifest\.json)$`)

func openDiagramPreview(directory string) (*diagramPreview, error) {
	root, err := os.OpenRoot(directory)
	if err != nil {
		return nil, err
	}
	info, err := root.Stat("index.html")
	if err != nil || !info.Mode().IsRegular() || info.Size() > 2<<20 {
		_ = root.Close()
		return nil, fmt.Errorf("diagram preview requires a generated index.html")
	}
	return &diagramPreview{root: root}, nil
}

func (preview *diagramPreview) ServeHTTP(w http.ResponseWriter, request *http.Request) {
	if request.URL.Path == "/diagram-preview" {
		http.Redirect(w, request, "/diagram-preview/", http.StatusTemporaryRedirect)
		return
	}
	name := strings.TrimPrefix(request.URL.Path, "/diagram-preview/")
	if name == "" {
		name = "index.html"
	}
	if !previewName.MatchString(name) {
		http.NotFound(w, request)
		return
	}
	file, err := preview.root.Open(name)
	if err != nil {
		http.NotFound(w, request)
		return
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > 2<<20 {
		http.NotFound(w, request)
		return
	}
	w.Header().Set("Content-Security-Policy", "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
	w.Header().Set("Cache-Control", "no-cache")
	switch {
	case strings.HasSuffix(name, ".png"):
		w.Header().Set("Content-Type", "image/png")
		w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
	case strings.HasSuffix(name, ".html"):
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
	case name == "manifest.json":
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
	}
	http.ServeContent(w, request, name, info.ModTime(), file)
}
