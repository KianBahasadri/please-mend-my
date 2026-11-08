package main

import (
	"bytes"
	"compress/gzip"
	"container/list"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"mime"
	"net/http"
	"os"
	"path"
	"strconv"
	"strings"
	"sync"
)

const guideCacheBytes = 64 << 20

type cachedGuide struct {
	key   string
	asset *staticAsset
	size  int
}

// Catalog snapshots are immutable while a process serves them. Only the compact
// search index and a bounded working set of guide responses occupy Go memory.
type catalogHandler struct {
	root       *os.Root
	index      *staticAsset
	archetypes *staticAsset
	credits    []byte
	artHashes  map[string]bool
	cacheMu    sync.Mutex
	cache      map[string]*list.Element
	cacheOrder list.List
	cacheBytes int
}

func openCatalog(directory string) (_ *catalogHandler, err error) {
	root, err := os.OpenRoot(directory)
	if err != nil {
		return nil, err
	}
	defer func() {
		if err != nil {
			_ = root.Close()
		}
	}()
	body, err := root.ReadFile("index.json")
	if err != nil {
		return nil, fmt.Errorf("read catalog index: %w", err)
	}
	var metadata struct {
		Version    int             `json:"version"`
		Revision   string          `json:"revision"`
		Archetypes json.RawMessage `json:"archetypes"`
	}
	if err = json.Unmarshal(body, &metadata); err != nil || metadata.Version != 1 || metadata.Revision == "" {
		return nil, errors.New("catalog index must be valid version 1 JSON with a revision")
	}
	manifestBody, err := root.ReadFile("manifest.json")
	if err != nil {
		return nil, fmt.Errorf("read catalog completion manifest: %w", err)
	}
	var manifest struct {
		Version         int                `json:"version"`
		Revision        string             `json:"revision"`
		RuntimeComplete bool               `json:"runtimeComplete"`
		IndexedItems    int                `json:"indexedItems"`
		IndexBytes      int                `json:"indexBytes"`
		Archetypes      *archetypeManifest `json:"archetypes"`
		DiagramCredits  *publishedFile     `json:"diagramCredits"`
	}
	if err = json.Unmarshal(manifestBody, &manifest); err != nil || manifest.Version != 1 || !manifest.RuntimeComplete || manifest.IndexedItems <= 0 || manifest.Revision != metadata.Revision || manifest.IndexBytes != len(body) {
		return nil, errors.New("catalog manifest must mark a complete snapshot matching index.json")
	}
	asset := &staticAsset{
		name: "index.json", contentType: "application/json; charset=utf-8", cacheControl: "no-cache",
		plain: newRepresentation(body),
	}
	compressed, readErr := root.ReadFile("index.json.gz")
	if readErr == nil {
		reader, gzipErr := gzip.NewReader(bytes.NewReader(compressed))
		if gzipErr != nil {
			return nil, fmt.Errorf("read compressed catalog index: %w", gzipErr)
		}
		hash := sha256.New()
		_, gzipErr = io.Copy(hash, io.LimitReader(reader, int64(len(body))+1))
		_ = reader.Close()
		plainHash := sha256.Sum256(body)
		if gzipErr != nil || !bytes.Equal(hash.Sum(nil), plainHash[:]) {
			return nil, errors.New("compressed catalog index does not match index.json")
		}
		asset.compressed = newRepresentation(compressed)
	} else if !errors.Is(readErr, fs.ErrNotExist) {
		return nil, fmt.Errorf("read compressed catalog index: %w", readErr)
	}
	catalog := &catalogHandler{root: root, index: asset, cache: make(map[string]*list.Element)}
	if len(metadata.Archetypes) != 0 && string(metadata.Archetypes) != "null" {
		if err := catalog.loadArchetypes(metadata.Archetypes, metadata.Revision, manifest.Archetypes); err != nil {
			return nil, err
		}
		if err := catalog.loadDiagramCredits(manifest.DiagramCredits); err != nil {
			return nil, err
		}
	} else if manifest.Archetypes != nil {
		return nil, errors.New("catalog manifest declares archetypes absent from the browser index")
	}
	return catalog, nil
}

func validCatalogID(id string) bool {
	if len(id) == 0 || len(id) > 64 {
		return false
	}
	for _, character := range id {
		if character >= 'a' && character <= 'z' || character >= 'A' && character <= 'Z' || character >= '0' && character <= '9' || character == '-' || character == '_' {
			continue
		}
		return false
	}
	return true
}

func (catalog *catalogHandler) guide(id string) (*staticAsset, error) {
	return catalog.cachedJSON("guides/"+id+".json", "no-cache", false)
}

// Guides and lazy diagram geometry share one bounded cache. The release registry
// stays resident, so recognizing nouns never needs a server request.
func (catalog *catalogHandler) cachedJSON(filename, cacheControl string, verify bool) (*staticAsset, error) {
	catalog.cacheMu.Lock()
	if element, exists := catalog.cache[filename]; exists {
		catalog.cacheOrder.MoveToFront(element)
		asset := element.Value.(*cachedGuide).asset
		catalog.cacheMu.Unlock()
		return asset, nil
	}
	catalog.cacheMu.Unlock()
	body, err := catalog.root.ReadFile(filename)
	if err != nil {
		return nil, err
	}
	asset := &staticAsset{
		name: filename, contentType: "application/json; charset=utf-8", cacheControl: cacheControl,
		plain: newRepresentation(body),
	}
	if verify && (!json.Valid(body) || asset.plain.etag != strconv.Quote(strings.TrimSuffix(path.Base(filename), ".json"))) {
		return nil, errors.New("diagram content does not match its pinned hash")
	}
	compressed, err := catalog.root.ReadFile(filename + ".gz")
	if err == nil {
		if verify {
			if err := verifyGzip(body, compressed); err != nil {
				return nil, err
			}
		}
		asset.compressed = newRepresentation(compressed)
	} else if !errors.Is(err, fs.ErrNotExist) {
		return nil, err
	}
	size := len(body) + len(compressed)
	if size > guideCacheBytes {
		return asset, nil
	}
	catalog.cacheMu.Lock()
	defer catalog.cacheMu.Unlock()
	// Another prefetch may have completed while this request read the files.
	if element, exists := catalog.cache[filename]; exists {
		catalog.cacheOrder.MoveToFront(element)
		return element.Value.(*cachedGuide).asset, nil
	}
	for catalog.cacheBytes+size > guideCacheBytes {
		oldest := catalog.cacheOrder.Back()
		entry := oldest.Value.(*cachedGuide)
		delete(catalog.cache, entry.key)
		catalog.cacheOrder.Remove(oldest)
		catalog.cacheBytes -= entry.size
	}
	entry := &cachedGuide{key: filename, asset: asset, size: size}
	catalog.cache[filename] = catalog.cacheOrder.PushFront(entry)
	catalog.cacheBytes += size
	return asset, nil
}

func (catalog *catalogHandler) ServeHTTP(w http.ResponseWriter, request *http.Request) {
	if request.URL.Path == "/api/catalog" {
		serveStaticAsset(w, request, catalog.index)
		return
	}
	if request.URL.Path == "/api/archetypes" {
		if catalog.archetypes == nil {
			http.NotFound(w, request)
			return
		}
		serveStaticAsset(w, request, catalog.archetypes)
		return
	}
	if filename, found := strings.CutPrefix(request.URL.Path, "/api/art/"); found {
		hash := strings.TrimSuffix(filename, ".json")
		if !strings.HasSuffix(filename, ".json") || !validArtHash(hash) || !catalog.artHashes[hash] {
			http.NotFound(w, request)
			return
		}
		asset, err := catalog.cachedJSON("art/"+filename, "public, max-age=31536000, immutable", true)
		if err != nil {
			http.NotFound(w, request)
			return
		}
		serveStaticAsset(w, request, asset)
		return
	}
	if id, found := strings.CutPrefix(request.URL.Path, "/api/guides/"); found {
		if !validCatalogID(id) {
			http.NotFound(w, request)
			return
		}
		asset, err := catalog.guide(id)
		if err != nil {
			http.NotFound(w, request)
			return
		}
		serveStaticAsset(w, request, asset)
		return
	}
	if filename, found := strings.CutPrefix(request.URL.Path, "/media/"); found {
		extension := strings.ToLower(path.Ext(filename))
		if !validCatalogID(strings.TrimSuffix(filename, path.Ext(filename))) || !strings.Contains("|.avif|.webp|.png|.jpg|.jpeg|.gif|.svg|.bmp|.ico|", "|"+extension+"|") {
			http.NotFound(w, request)
			return
		}
		file, err := catalog.root.Open("media/" + filename)
		if err != nil {
			http.NotFound(w, request)
			return
		}
		defer file.Close()
		stat, err := file.Stat()
		if err != nil || !stat.Mode().IsRegular() {
			http.NotFound(w, request)
			return
		}
		w.Header().Set("Content-Type", mime.TypeByExtension(extension))
		w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
		w.Header().Set("ETag", strconv.Quote(filename))
		if extension == ".svg" {
			w.Header().Set("Content-Security-Policy", "sandbox")
		}
		http.ServeContent(w, request, filename, stat.ModTime(), file)
		return
	}
	http.NotFound(w, request)
}
