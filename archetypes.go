package main

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net/url"
	"strings"
)

type artAttribution struct {
	Title         string           `json:"title"`
	Author        string           `json:"author"`
	Source        string           `json:"source"`
	License       string           `json:"license"`
	LicenseURL    string           `json:"licenseUrl"`
	Changes       string           `json:"changes"`
	Supplementary []artAttribution `json:"supplementary"`
}

type archetypeRegistry struct {
	Version    int    `json:"version"`
	Revision   string `json:"revision"`
	Archetypes []struct {
		ID          string          `json:"id"`
		Label       string          `json:"label"`
		ArtURL      string          `json:"artUrl"`
		Attribution *artAttribution `json:"attribution"`
	} `json:"archetypes"`
	Models []struct{} `json:"models"`
}

type archetypeManifest struct {
	Version   int    `json:"version"`
	Count     int    `json:"count"`
	Models    int    `json:"models"`
	Bytes     int    `json:"bytes"`
	GzipBytes int    `json:"gzipBytes"`
	SHA256    string `json:"sha256"`
	ArtFiles  int    `json:"artFiles"`
}

type publishedFile struct {
	Bytes  int    `json:"bytes"`
	SHA256 string `json:"sha256"`
}

func (catalog *catalogHandler) loadDiagramCredits(manifest *publishedFile) error {
	if manifest == nil {
		return errors.New("catalog manifest must include diagram credits")
	}
	body, err := catalog.root.ReadFile("diagram-credits.html")
	if err != nil {
		return fmt.Errorf("read catalog diagram credits: %w", err)
	}
	if len(body) != manifest.Bytes || fmt.Sprintf("%x", sha256.Sum256(body)) != manifest.SHA256 {
		return errors.New("catalog diagram credits do not match the release manifest")
	}
	catalog.credits = body
	return nil
}

func validArtHash(hash string) bool {
	return len(hash) == 64 && strings.Trim(hash, "0123456789abcdef") == ""
}

func verifyGzip(plain, compressed []byte) error {
	reader, err := gzip.NewReader(bytes.NewReader(compressed))
	if err != nil {
		return fmt.Errorf("invalid compressed catalog asset: %w", err)
	}
	defer reader.Close()
	hash := sha256.New()
	_, err = io.Copy(hash, io.LimitReader(reader, int64(len(plain))+1))
	expected := sha256.Sum256(plain)
	if err != nil || !bytes.Equal(hash.Sum(nil), expected[:]) {
		return errors.New("compressed catalog asset does not match its JSON")
	}
	return nil
}

func (catalog *catalogHandler) loadArchetypes(embedded json.RawMessage, revision string, manifest *archetypeManifest) error {
	body, err := catalog.root.ReadFile("archetypes.json")
	if err != nil {
		return fmt.Errorf("read catalog archetypes: %w", err)
	}
	var compact bytes.Buffer
	if err := json.Compact(&compact, body); err != nil {
		return fmt.Errorf("invalid catalog archetypes: %w", err)
	}
	var resident bytes.Buffer
	if err := json.Compact(&resident, embedded); err != nil || !bytes.Equal(compact.Bytes(), resident.Bytes()) {
		return errors.New("catalog archetypes do not match the browser index")
	}
	var registry archetypeRegistry
	if err := json.Unmarshal(body, &registry); err != nil || registry.Version != 1 || registry.Revision != revision {
		return errors.New("catalog archetypes must match the release revision")
	}
	if manifest == nil || manifest.Version != 1 || manifest.Bytes != len(body) || manifest.Count != len(registry.Archetypes) || manifest.Models != len(registry.Models) || manifest.SHA256 != fmt.Sprintf("%x", sha256.Sum256(body)) {
		return errors.New("catalog manifest must match the published archetypes")
	}
	catalog.artHashes = make(map[string]bool)
	for _, entry := range registry.Archetypes {
		if entry.ArtURL == "" {
			continue
		}
		hash, found := strings.CutPrefix(entry.ArtURL, "/api/art/")
		hash = strings.TrimSuffix(hash, ".json")
		if !found || !strings.HasSuffix(entry.ArtURL, ".json") || !validArtHash(hash) || entry.Attribution == nil {
			return fmt.Errorf("invalid pinned diagram for archetype %s", entry.ID)
		}
		if err := validateAttribution(*entry.Attribution); err != nil {
			return fmt.Errorf("invalid diagram attribution for %s: %w", entry.ID, err)
		}
		catalog.artHashes[hash] = true
	}
	if manifest.ArtFiles != len(catalog.artHashes) {
		return errors.New("catalog manifest must match the pinned diagram count")
	}
	asset := &staticAsset{name: "archetypes.json", contentType: "application/json; charset=utf-8", cacheControl: "no-cache", plain: newRepresentation(body)}
	compressed, err := catalog.root.ReadFile("archetypes.json.gz")
	if err == nil {
		if manifest.GzipBytes != len(compressed) {
			return errors.New("catalog manifest must match compressed archetype bytes")
		}
		if err := verifyGzip(body, compressed); err != nil {
			return err
		}
		asset.compressed = newRepresentation(compressed)
	} else if !errors.Is(err, fs.ErrNotExist) || manifest.GzipBytes != 0 {
		return err
	}
	catalog.archetypes = asset
	return nil
}

func validateAttribution(record artAttribution) error {
	if record.Title == "" || record.Author == "" || record.License == "" || record.Changes == "" {
		return errors.New("missing source, license, author, or adaptation description")
	}
	for _, address := range []string{record.Source, record.LicenseURL} {
		parsed, err := url.Parse(address)
		if err != nil || parsed.Hostname() == "" || parsed.User != nil || parsed.Scheme != "https" && parsed.Scheme != "http" {
			return errors.New("source and license links must be public HTTP(S) URLs")
		}
	}
	for _, additional := range record.Supplementary {
		if err := validateAttribution(additional); err != nil {
			return err
		}
	}
	return nil
}

// Credits are compiled once from the selected release, alongside embedded site
// assets. Serving the page performs no catalog traversal or template rendering.
func (handler *siteHandler) prepareCredits() error {
	asset := handler.assets["/credits"]
	const start = "<!-- diagram-pilot-credits:start -->"
	const end = "<!-- diagram-pilot-credits:end -->"
	before, rest, found := strings.Cut(string(asset.plain.body), start)
	_, after, complete := strings.Cut(rest, end)
	if !found || !complete {
		return errors.New("credits page is missing its diagram section markers")
	}
	var section []byte
	if handler.catalog != nil {
		section = handler.catalog.credits
	}
	body := []byte(before + start + "\n" + string(section) + "\n" + end + after)
	prepared, err := prepareStaticAsset(asset.name, asset.contentType, asset.cacheControl, body)
	if err != nil {
		return err
	}
	handler.assets["/credits"] = prepared
	handler.assets["/credits.html"] = prepared
	return nil
}
