package main

import (
	"bytes"
	"compress/gzip"
	"io"
	"net/http"
	"strconv"
	"strings"
	"testing"
)

func TestCreditsPageIsUnlinkedAndServedFromPreparedAssets(t *testing.T) {
	directory, _, _ := archetypeFixture(t)
	t.Setenv("CATALOG_DIR", directory)
	handler := testHandler(t)
	home := requestSite(handler, http.MethodGet, "/", "", "")
	if strings.Contains(home.Body.String(), "/credits") {
		t.Fatal("credits must remain unlinked from the landing page")
	}
	var canonical []byte
	for _, target := range []string{"/credits", "/credits.html"} {
		plain := requestSite(handler, http.MethodGet, target, "", "")
		if plain.Code != http.StatusOK || plain.Header().Get("Content-Type") != "text/html; charset=utf-8" {
			t.Fatalf("credits page %s: status=%d type=%q", target, plain.Code, plain.Header().Get("Content-Type"))
		}
		if canonical == nil {
			canonical = bytes.Clone(plain.Body.Bytes())
		} else if !bytes.Equal(canonical, plain.Body.Bytes()) {
			t.Fatal("credits route aliases must serve identical content")
		}
		for _, credit := range []string{
			`name="robots" content="noindex"`,
			"https://github.com/KianBahasadri/please-mend-my",
			"Artist &lt;script&gt;alert(1)&lt;/script&gt;",
			"https://commons.wikimedia.org/wiki/File:Bicycle_diagram.svg",
			"https://creativecommons.org/licenses/by/3.0/",
			"Second artist", "https://openclipart.org/detail/3891/bicycle",
			"https://creativecommons.org/publicdomain/zero/1.0/",
		} {
			if !bytes.Contains(plain.Body.Bytes(), []byte(credit)) {
				t.Fatalf("credits page is missing %q", credit)
			}
		}
		if bytes.Contains(plain.Body.Bytes(), []byte("<script")) {
			t.Fatal("credits must not initialize the catalog or hero scripts")
		}
		packed := requestSite(handler, http.MethodGet, target, "gzip", "")
		if packed.Code != http.StatusOK || packed.Header().Get("Content-Encoding") != "gzip" {
			t.Fatalf("credits route %s must use prepared gzip", target)
		}
		reader, err := gzip.NewReader(packed.Body)
		if err != nil {
			t.Fatal(err)
		}
		decoded, err := io.ReadAll(reader)
		_ = reader.Close()
		if err != nil || !bytes.Equal(decoded, canonical) {
			t.Fatalf("credits gzip must preserve the page: %v", err)
		}
		for _, encoding := range []string{"", "gzip"} {
			get := requestSite(handler, http.MethodGet, target, encoding, "")
			head := requestSite(handler, http.MethodHead, target, encoding, "")
			if head.Code != http.StatusOK || head.Body.Len() != 0 || head.Header().Get("Content-Length") != strconv.Itoa(get.Body.Len()) {
				t.Fatalf("credits HEAD %s %q must match GET headers", target, encoding)
			}
			if get.Header().Get("ETag") == "" {
				t.Fatal("credits page must have a prepared ETag")
			}
			unchanged := requestSite(handler, http.MethodGet, target, encoding, get.Header().Get("ETag"))
			if unchanged.Code != http.StatusNotModified || unchanged.Body.Len() != 0 {
				t.Fatalf("credits conditional request %s %q must return an empty 304", target, encoding)
			}
		}
	}
	style := requestSite(handler, http.MethodGet, "/assets/credits.css", "", "")
	if style.Code != http.StatusOK || !strings.HasPrefix(style.Header().Get("Content-Type"), "text/css") {
		t.Fatal("credits stylesheet must be embedded and publicly served")
	}
}
