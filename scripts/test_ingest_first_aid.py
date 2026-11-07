"""First Aid ingestion tests use tiny generated PDFs, never real catalog scans."""
from contextlib import ExitStack
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

import ingest_first_aid as ingest


class FirstAidTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "source-build"
        self.source = self.data / "source"
        self.source.mkdir(parents=True)
        self.pdf = self.source / ingest.ARCHIVE_NAME
        with pymupdf.open() as document:
            for number in range(1, 6):
                page = document.new_page(width=220, height=240)
                page.insert_text((12, 24), "FIXTURE PAGE " + str(number), fontsize=10)
                page.insert_text((12, 48), "Full text retained <tag> & text", fontsize=9)
                page.draw_rect(pymupdf.Rect(20, 90, 130, 140), color=(0, 0, 0))
            document.set_toc([[1, "First Aid", -1], [2, "COVER", 1],
                              [2, "CHAPTER 1 BASIC MEASURES FOR FIRST AID", 2],
                              [3, "Section I. STOP THE BLEEDING", 3],
                              [2, "CHAPTER 2 FIRST AID FOR FRACTURES", 4]])
            document.save(self.pdf)
        self.pdf_bytes = self.pdf.read_bytes()
        self.pins = {"EXPECTED_BYTES": len(self.pdf_bytes), "EXPECTED_PAGES": 5,
                     "EXPECTED_SHA256": hashlib.sha256(self.pdf_bytes).hexdigest(),
                     "EXPECTED_SHA1": hashlib.sha1(self.pdf_bytes).hexdigest(),
                     "EXPECTED_MD5": hashlib.md5(self.pdf_bytes).hexdigest()}
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        for name, value in self.pins.items():
            self.patches.enter_context(patch.object(ingest, name, value))
        self.metadata = {"files": [{"name": ingest.ARCHIVE_NAME, "size": str(ingest.EXPECTED_BYTES),
                                    "sha1": ingest.EXPECTED_SHA1, "md5": ingest.EXPECTED_MD5}]}
        (self.source / "archive-metadata.json").write_text(json.dumps(self.metadata))

    def build(self):
        with patch.object(ingest, "fetch", side_effect=AssertionError("Build-only must not use the network")):
            return ingest.ingest(self.data, build_only=True)

    def test_chapter_entries_keep_every_page_text_images_and_provenance(self):
        runtime = self.build()
        index = json.loads((runtime / "index.json").read_bytes())
        manifest = json.loads((runtime / "manifest.json").read_bytes())
        self.assertEqual(index["fields"], ["id", "title", "category", "kind", "device"])
        self.assertEqual(index["source"]["id"], "first-aid")
        self.assertEqual(index["categories"], [{"id": "health", "name": "Health & medicine"}])
        self.assertEqual(len(index["items"]), 3)
        self.assertTrue(manifest["runtimeComplete"])
        self.assertTrue(manifest["documentVerified"])
        self.assertTrue(manifest["archiveChecksumVerified"])
        self.assertEqual(manifest["sourceFormat"], "pdf")
        self.assertNotIn("zimChecksumVerified", manifest)
        self.assertEqual(manifest["archiveSha256"], ingest.EXPECTED_SHA256)
        self.assertEqual(manifest["publicationDate"], "2002-12-23")
        self.assertEqual(manifest["changeDate"], "2004-07-15")
        self.assertEqual(gzip.decompress((runtime / "index.json.gz").read_bytes()),
                         (runtime / "index.json").read_bytes())
        raw_pages = [json.loads(line) for line in (self.source / "pages.jsonl").read_bytes().splitlines()]
        self.assertEqual([page["page"] for page in raw_pages], [1, 2, 3, 4, 5])
        with pymupdf.open(self.pdf) as document:
            for offset, raw_page in enumerate(raw_pages):
                self.assertEqual(raw_page["text"], document[offset].get_text("text", sort=True))
                self.assertTrue((runtime / raw_page["image"].lstrip("/")).is_file())
            self.assertEqual(json.loads((self.source / "outline.json").read_bytes()), document.get_toc())
        self.assertEqual(self.pdf.read_bytes(), self.pdf_bytes)
        pages_covered = []
        for row in index["items"]:
            record_bytes = (runtime / "guides" / (row[0] + ".json")).read_bytes()
            record = json.loads(record_bytes)
            self.assertEqual(gzip.decompress((runtime / "guides" / (row[0] + ".json.gz")).read_bytes()), record_bytes)
            self.assertEqual(record["sourceName"], "U.S. military")
            self.assertIn("2002", record["title"])
            self.assertIn("2004", record["summary"])
            self.assertEqual(record["license"]["name"], "Public domain (U.S. federal government)")
            self.assertNotIn("reformatted", record["attribution"])
            self.assertNotIn("sanitized", record["attribution"])
            self.assertNotIn("offline catalog", record["attribution"])
            self.assertIn("&lt;tag&gt; &amp; text", record["html"])
            start, end = record["provenance"]["startPage"], record["provenance"]["endPage"]
            self.assertTrue(record["sourceUrl"].endswith("#page=" + str(start)))
            for number in range(start, end + 1):
                self.assertIn("FIXTURE PAGE " + str(number), record["html"])
                pages_covered.append(number)
        self.assertEqual(sorted(pages_covered), [1, 2, 3, 4, 5])
        groups = index["search"]["groups"]
        self.assertEqual(groups[0], "")
        self.assertEqual(len(index["search"]["itemGroups"]), 3)
        self.assertTrue(any("broken bones" in group for group in groups))

    def test_repeat_ingestion_reuses_receipt_without_source_read_or_network(self):
        runtime = self.build()
        original = (runtime / "manifest.json").read_bytes()
        with patch.object(ingest, "verify_pdf", side_effect=AssertionError("No corpus rehash on reuse")), \
                patch.object(ingest.pymupdf, "open", side_effect=AssertionError("No PDF parse on reuse")), \
                patch.object(ingest, "fetch", side_effect=AssertionError("No network on reuse")):
            self.assertEqual(ingest.ingest(self.data), runtime)
        self.assertEqual((runtime / "manifest.json").read_bytes(), original)

    def test_script_change_invalidates_completion_receipt(self):
        self.build()
        with patch.object(ingest, "script_fingerprint", return_value="changed"):
            self.assertIsNone(ingest.cached_runtime(self.data))

    def test_changed_bytes_fail_pinned_verification_before_publication(self):
        self.pdf.write_bytes(self.pdf_bytes[:-1] + b"x")
        with self.assertRaisesRegex(ValueError, "pinned size/checksums"):
            ingest.build_runtime(self.data, self.pdf)
        self.assertFalse((self.data / "runtime").exists())

    def test_official_metadata_pin_mismatch_is_rejected(self):
        changed = json.loads(json.dumps(self.metadata))
        changed["files"][0]["sha1"] = "f" * 40
        with self.assertRaisesRegex(ValueError, "metadata differs"):
            ingest.validate_archive_metadata(changed)

    def test_download_only_checks_source_without_building(self):
        with patch.object(ingest.pymupdf, "open", side_effect=AssertionError("Download-only must not parse")), \
                patch.object(ingest, "fetch", side_effect=AssertionError("Source is already cached")):
            self.assertEqual(ingest.ingest(self.data, download_only=True), self.pdf)
        self.assertFalse((self.data / "runtime").exists())

    def test_download_falls_back_to_provided_mirror_and_verifies_bytes(self):
        self.pdf.unlink()
        urls = []
        def fallback(url):
            urls.append(url)
            if url == ingest.ARCHIVE_URL:
                raise OSError("Canonical mirror unavailable")
            self.assertEqual(url, ingest.MIRROR_URL)
            return io.BytesIO(self.pdf_bytes)
        with patch.object(ingest, "fetch", side_effect=fallback):
            self.assertEqual(ingest.ingest(self.data, download_only=True), self.pdf)
        self.assertEqual(urls, [ingest.ARCHIVE_URL, ingest.MIRROR_URL])
        self.assertEqual(self.pdf.read_bytes(), self.pdf_bytes)
        self.assertFalse(self.pdf.with_suffix(".pdf.part").exists())

    def test_mid_extract_failure_preserves_previous_runtime_and_raw_text(self):
        runtime = self.build()
        old_manifest = (runtime / "manifest.json").read_bytes()
        old_pages = (self.source / "pages.jsonl").read_bytes()
        real_open = pymupdf.open
        class InterruptedDocument:
            def __init__(self, path):
                self.doc = real_open(path)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.doc.close()
            def __len__(self):
                return len(self.doc)
            def __getattr__(self, name):
                return getattr(self.doc, name)
            def __getitem__(self, index):
                if index == 1:
                    raise RuntimeError("Interrupted page extraction")
                return self.doc[index]
        with patch.object(ingest.pymupdf, "open", side_effect=InterruptedDocument):
            with self.assertRaisesRegex(RuntimeError, "Interrupted page extraction"):
                ingest.build_runtime(self.data, self.pdf)
        self.assertEqual((runtime / "manifest.json").read_bytes(), old_manifest)
        self.assertEqual((self.source / "pages.jsonl").read_bytes(), old_pages)
        self.assertEqual(list(self.data.glob(".runtime-*")), [])
        self.assertEqual(list(self.source.glob(".extract-*")), [])

    def test_same_pdf_produces_deterministic_catalogs(self):
        first = self.build()
        other = self.root / "other"
        (other / "source").mkdir(parents=True)
        pdf = other / "source" / ingest.ARCHIVE_NAME
        shutil.copyfile(self.pdf, pdf)
        second = ingest.build_runtime(other, pdf)
        self.assertEqual((first / "index.json.gz").read_bytes(), (second / "index.json.gz").read_bytes())
        self.assertEqual((first / "manifest.json").read_bytes(), (second / "manifest.json").read_bytes())

    def test_unbookmarked_pages_and_absent_outline_are_retained(self):
        self.assertEqual(ingest.chapter_ranges([], 3),
                         [{"heading": "First Aid manual", "startPage": 1, "endPage": 3}])
        self.assertEqual(ingest.chapter_ranges([[1, "Chapter", 2]], 4),
                         [{"heading": "Front matter", "startPage": 1, "endPage": 1},
                          {"heading": "Chapter", "startPage": 2, "endPage": 4}])
        self.assertEqual(ingest.normalize("Frácturès"), "fractures")


if __name__ == "__main__":
    unittest.main()
