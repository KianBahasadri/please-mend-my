"""Tiny publication fixtures exercise v2/v1 routing without reading the corpus."""
from copy import deepcopy
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import overlay_guides as overlay
import prepare_search as search
from structured_guides import canonical_bytes, make_v2
from test_prepare_search import unpack


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.cache = self.root / "source", self.root / "cache"
        self.source.mkdir()
        self.cache.mkdir()
        (self.source / "guides").mkdir()
        (self.source / "media").mkdir()
        self.identifier, self.legacy_id = "a" * 20, "b" * 20
        self.raw = {"id": self.identifier, "title": "Replace a laptop battery", "category": "technology",
                    "kind": "guide", "device": "Laptop", "sourceUrl": "https://example.com/guide",
                    "license": {"name": "CC BY-SA", "url": "https://example.com/license"},
                    "attribution": "Example contributor", "html": "<p>Disconnect power.</p>",
                    "summary": "Raw summary", "images": [], "related": []}
        for identifier in (self.identifier, self.legacy_id):
            raw = dict(self.raw, id=identifier)
            body = canonical_bytes(raw)
            (self.source / "guides" / (identifier + ".json")).write_bytes(body)
            (self.source / "guides" / (identifier + ".json.gz")).write_bytes(gzip.compress(body, mtime=0))
        self.content = {"summary": "Replace the battery after disconnecting power.", "tools": ["Screwdriver"],
                        "materials": ["Replacement battery"], "safety": [{"level": "warning", "text": "Disconnect power."}],
                        "symptoms": ["Battery does not hold charge"], "references": [],
                        "steps": [{"title": "Disconnect power", "body": "Unplug the laptop.",
                                   "warnings": [], "images": [], "code": ""}]}
        self.detail = make_v2(self.raw, self.content, "qwen/qwen3.6-35b-a3b")
        source_index = {"version": 1, "revision": "fixture", "categories": [
            {"id": "technology", "name": "Technology"}],
            "fields": ["id", "title", "category", "kind", "device"], "items": [
            [identifier, self.raw["title"], "technology", "guide", "Laptop"]
            for identifier in (self.identifier, self.legacy_id)],
            "search": {"version": 1, "groups": ["", "notebook"], "itemGroups": [1, 1]}}
        source_index["search"]["inverted"] = search.prepare_index(source_index)
        index = canonical_bytes(source_index)
        (self.source / "index.json").write_bytes(index)
        packed = gzip.compress(index, mtime=0)
        (self.source / "index.json.gz").write_bytes(packed)
        (self.source / "media" / ("c" * 64 + ".png")).write_bytes(b"fixture-image")
        manifest = {"version": 1, "revision": "fixture", "runtimeComplete": True,
                    "indexedItems": 2, "uniqueMediaFiles": 1,
                    "indexBytes": len(index), "indexGzipBytes": len(packed),
                    "guideJsonBytes": sum(path.stat().st_size for path in (self.source / "guides").glob("*.json")),
                    "searchIndex": {"version": 1}, "searchEnrichment": {"version": 1},
                    "archiveChecksumVerified": True,
                    "zimChecksumVerified": True, "archiveSha256": "f" * 64, "expectedSha256": "f" * 64}
        (self.source / "manifest.json").write_bytes(canonical_bytes(manifest))

    def activate(self, detail=None, identifier=None):
        path = self.cache / ((identifier or self.identifier) + ".json")
        path.write_bytes(canonical_bytes(self.detail if detail is None else detail))
        return path

    def clone(self, name="stage"):
        stage = self.root / name
        stage.mkdir()
        for name in ("index.json", "index.json.gz", "manifest.json"):
            os.link(self.source / name, stage / name)
        overlay._link_directory(self.source / "guides", stage / "guides")
        overlay._link_directory(self.source / "media", stage / "media")
        return stage

    def test_valid_v2_replaces_only_stage_hardlinks_with_matching_deterministic_gzip(self):
        self.activate()
        stage = self.clone()
        original = (self.source / "guides" / (self.identifier + ".json")).read_bytes()
        legacy_inode = (stage / "guides" / (self.legacy_id + ".json")).stat().st_ino
        metadata = overlay.overlay_guides(stage, self.cache)
        path = stage / "guides" / (self.identifier + ".json")
        self.assertEqual(json.loads(path.read_bytes()), self.detail)
        self.assertEqual(gzip.decompress(path.with_suffix(".json.gz").read_bytes()), path.read_bytes())
        self.assertEqual((self.source / "guides" / path.name).read_bytes(), original)
        self.assertNotEqual(path.stat().st_ino, (self.source / "guides" / path.name).stat().st_ino)
        self.assertEqual((stage / "guides" / (self.legacy_id + ".json")).stat().st_ino, legacy_inode)
        self.assertEqual(metadata["processedItems"], 1)
        self.assertEqual(metadata["itemIds"], [self.identifier])
        self.assertEqual(metadata["legacyItems"], 1)
        self.assertEqual(metadata["rejectedItems"], 0)
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        self.assertNotIn("structuredGuides", json.loads((self.source / "manifest.json").read_bytes()))
        other = self.clone("other")
        overlay.overlay_guides(other, self.cache)
        self.assertEqual(path.with_suffix(".json.gz").read_bytes(),
                         (other / "guides" / path.with_suffix(".json.gz").name).read_bytes())

    def test_missing_cache_leaves_every_legacy_guide_byte_identical(self):
        stage = self.clone()
        with patch.object(overlay.os, "scandir", side_effect=AssertionError("missing cache needs no scan")):
            metadata = overlay.overlay_guides(stage, self.root / "missing-cache")
        self.assertEqual(metadata["processedItems"], 0)
        self.assertEqual(metadata["itemIds"], [])
        self.assertEqual(metadata["legacyItems"], 2)
        for path in (stage / "guides").iterdir():
            self.assertEqual(path.read_bytes(), (self.source / "guides" / path.name).read_bytes())

    def test_malformed_wrong_id_stale_raw_and_modified_attribution_fall_back_to_v1(self):
        cases = [b"{broken", canonical_bytes(dict(self.detail, id=self.legacy_id))]
        stale = deepcopy(self.detail)
        stale["processing"]["rawSha256"] = "e" * 64
        cases.append(canonical_bytes(stale))
        cases.append(canonical_bytes(dict(self.detail, attribution="Invented author")))
        for number, body in enumerate(cases):
            with self.subTest(number=number):
                self.activate().write_bytes(body)
                stage = self.clone("stage" + str(number))
                metadata = overlay.overlay_guides(stage, self.cache)
                self.assertEqual(metadata["processedItems"], 0)
                self.assertEqual(metadata["itemIds"], [])
                self.assertEqual(metadata["rejectedItems"], 1)
                for path in (stage / "guides").iterdir():
                    self.assertEqual(path.read_bytes(), (self.source / "guides" / path.name).read_bytes())

    def test_symlink_cache_entry_is_rejected_without_following_it(self):
        external = self.root / "external.json"
        external.write_bytes(canonical_bytes(self.detail))
        (self.cache / (self.identifier + ".json")).symlink_to(external)
        metadata = overlay.overlay_guides(self.clone(), self.cache)
        self.assertEqual(metadata["processedItems"], 0)
        self.assertEqual(metadata["rejectedItems"], 1)

    def test_cache_fingerprint_tracks_active_content_only_and_ignores_sha_objects(self):
        before = overlay.cache_fingerprint(self.cache)
        (self.cache / "objects").mkdir()
        (self.cache / "objects" / ("d" * 64 + ".json")).write_text("new object")
        (self.cache / "report.json").write_text("report")
        (self.cache / (self.identifier + ".json.tmp")).write_text("unfinished")
        self.assertEqual(overlay.cache_fingerprint(self.cache), before)
        path = self.activate()
        first = overlay.cache_fingerprint(self.cache)
        self.assertNotEqual(first, before)
        changed = deepcopy(self.detail)
        changed["summary"] += " Updated."
        path.write_bytes(canonical_bytes(changed))
        self.assertNotEqual(overlay.cache_fingerprint(self.cache), first)

    def test_expected_cache_fingerprint_mismatch_prevents_any_stage_write(self):
        fingerprint = overlay.cache_fingerprint(self.cache)
        self.activate()
        stage = self.clone()
        with self.assertRaisesRegex(RuntimeError, "cache changed"):
            overlay.overlay_guides(stage, self.cache, fingerprint)
        self.assertEqual((stage / "manifest.json").read_bytes(), (self.source / "manifest.json").read_bytes())

    def test_cache_changed_mid_overlay_aborts_publication(self):
        self.activate()
        with patch.object(overlay, "cache_fingerprint", return_value="changed"):
            with self.assertRaisesRegex(RuntimeError, "cache changed"):
                overlay.overlay_guides(self.clone(), self.cache)

    def test_standalone_build_publishes_reuses_and_rebuilds_without_mutating_source(self):
        path = self.activate()
        releases = self.root / "releases"
        original_index = (self.source / "index.json").read_bytes()
        original_manifest = (self.source / "manifest.json").read_bytes()
        first = overlay.overlay_release(self.source, self.cache, releases)
        self.assertEqual(json.loads((first / "guides" / path.name).read_bytes())["version"], 2)
        self.assertEqual(first.stat().st_mode & 0o777, 0o755)
        self.assertNotEqual((first / "index.json").stat().st_ino, (self.source / "index.json").stat().st_ino)
        self.assertEqual((self.source / "index.json").read_bytes(), original_index)
        self.assertEqual((self.source / "manifest.json").read_bytes(), original_manifest)
        body = (first / "index.json").read_bytes()
        self.assertEqual(gzip.decompress((first / "index.json.gz").read_bytes()), body)
        index = json.loads(body)
        postings = unpack(index["search"]["inverted"])
        self.assertEqual(postings["charge"], [0], "Only the processed guide inherits symptoms")
        self.assertEqual(postings["replacement"], [0], "Materials become document terms")
        receipt = json.loads((first / "ingest.json").read_bytes())
        manifest = json.loads((first / "manifest.json").read_bytes())
        self.assertEqual(receipt["indexSha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(receipt["searchIndex"], manifest["searchIndex"])
        self.assertEqual(receipt["sourceIndexSha256"], hashlib.sha256(original_index).hexdigest())
        self.assertNotEqual(receipt["indexSha256"], receipt["sourceIndexSha256"])
        for name in ("index.json", "index.json.gz", "manifest.json"):
            self.assertEqual((first / name).stat().st_mode & 0o777, 0o644)
        media_name = "c" * 64 + ".png"
        self.assertEqual((first / "media" / media_name).stat().st_ino, (self.source / "media" / media_name).stat().st_ino)
        with patch.object(overlay, "_link_directory", side_effect=AssertionError("reuse must not enumerate corpus")):
            self.assertEqual(overlay.overlay_release(self.source, self.cache, releases), first)
        updated = deepcopy(self.content)
        updated["summary"] += " Updated."
        self.activate(make_v2(self.raw, updated, "qwen/qwen3.6-35b-a3b"))
        second = overlay.overlay_release(self.source, self.cache, releases)
        self.assertNotEqual(first, second)
        self.assertEqual(json.loads((first / "guides" / path.name).read_bytes())["summary"], self.content["summary"])
        self.assertIn("html", json.loads((self.source / "guides" / path.name).read_bytes()))
        self.assertEqual(list(releases.glob(".overlay-*")), [])
        self.assertEqual(list(releases.glob(".search-*")), [])

    def test_search_preparation_failure_leaves_source_and_previous_release_untouched(self):
        self.activate()
        releases = self.root / "releases"
        first = overlay.overlay_release(self.source, self.cache, releases)
        original_index = (self.source / "index.json").read_bytes()
        published_index = (first / "index.json").read_bytes()
        changed = deepcopy(self.content)
        changed["symptoms"].append("A newly extracted diagnostic symptom")
        self.activate(make_v2(self.raw, changed, "qwen/qwen3.6-35b-a3b"))
        with patch.object(search, "prepare", side_effect=OSError("index write failed")):
            with self.assertRaisesRegex(OSError, "index write failed"):
                overlay.overlay_release(self.source, self.cache, releases)
        self.assertEqual((self.source / "index.json").read_bytes(), original_index)
        self.assertEqual((first / "index.json").read_bytes(), published_index)
        self.assertEqual(sorted(path.name for path in releases.iterdir()), [".overlay.lock", first.name])

    def test_standalone_key_includes_search_preparation_code(self):
        self.activate()
        import build_catalog
        captured = []
        original_key = build_catalog.input_key
        def record_inputs(paths):
            captured.append(tuple(Path(path).name for path in paths))
            return original_key(paths)
        with patch.object(build_catalog, "input_key", side_effect=record_inputs):
            overlay.overlay_release(self.source, self.cache, self.root / "releases")
        self.assertGreaterEqual(len(captured), 2)
        self.assertTrue(all("prepare_search.py" in paths for paths in captured))

    def test_standalone_empty_cache_returns_source_without_corpus_read_or_new_release(self):
        with patch.object(overlay, "_link_directory", side_effect=AssertionError("no active cache needs no corpus walk")):
            self.assertEqual(overlay.overlay_release(self.source, self.cache, self.root / "releases"), self.source)
        self.assertFalse((self.root / "releases").exists())

    def test_failed_standalone_build_cleans_stage_without_touching_source(self):
        self.activate()
        releases = self.root / "releases"
        with patch.object(overlay, "overlay_guides", side_effect=RuntimeError("cache changed")):
            with self.assertRaisesRegex(RuntimeError, "cache changed"):
                overlay.overlay_release(self.source, self.cache, releases)
        self.assertTrue((self.source / "guides" / (self.identifier + ".json")).is_file())
        self.assertEqual([path.name for path in releases.iterdir()], [".overlay.lock"])

    def test_processed_snapshot_is_never_accepted_as_raw_base(self):
        self.activate()
        release = overlay.overlay_release(self.source, self.cache, self.root / "releases")
        with self.assertRaisesRegex(ValueError, "original legacy"):
            overlay.overlay_release(release, self.cache, self.root / "releases")


if __name__ == "__main__":
    unittest.main()
