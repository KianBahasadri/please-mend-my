package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"sort"
	"strings"
)

type shellFile struct {
	URL    string `json:"url"`
	SHA256 string `json:"sha256"`
}

// The inventory is derived from the embed tree, never a maintained subset of
// fonts or scripts. Installation verifies decoded bytes against these hashes.
func (handler *siteHandler) prepareOfflineShell() error {
	files := make([]shellFile, 0, len(handler.assets))
	for target, asset := range handler.assets {
		if target == "/" || target == "/credits" || strings.HasPrefix(target, "/assets/") {
			sum := sha256.Sum256(asset.plain.body)
			files = append(files, shellFile{target, hex.EncodeToString(sum[:])})
		}
	}
	sort.Slice(files, func(i, j int) bool { return files[i].URL < files[j].URL })
	encoded, err := json.Marshal(files)
	if err != nil {
		return err
	}
	template, err := siteFiles.ReadFile("sw.js")
	if err != nil {
		return err
	}
	identity := sha256.New()
	_, _ = identity.Write(template)
	_, _ = identity.Write(encoded)
	version := fmt.Sprintf("%x", identity.Sum(nil))
	manifest, err := json.Marshal(struct {
		Version string      `json:"version"`
		Assets  []shellFile `json:"assets"`
	}{version, files})
	if err != nil {
		return err
	}
	worker, err := prepareStaticAsset("sw.js", "text/javascript; charset=utf-8", "no-cache",
		[]byte(strings.ReplaceAll(string(template), "__PMM_SHELL_VERSION__", version)))
	if err != nil {
		return err
	}
	handler.assets["/sw.js"] = worker
	asset, err := prepareStaticAsset("shell-manifest.json", "application/json; charset=utf-8", "no-cache", manifest)
	if err != nil {
		return err
	}
	handler.assets["/shell-manifest.json"] = asset
	return nil
}

func catalogETag(catalog *catalogHandler, request *http.Request) string {
	asset := catalog.index
	gzipQuality, identityQuality := encodingQualities(request.Header.Values("Accept-Encoding"))
	if asset.compressed.body != nil && gzipQuality > 0 && gzipQuality >= identityQuality {
		return asset.compressed.etag
	}
	return asset.plain.etag
}
