"""Merge lifecycle tests use tiny synthetic sources, never the real corpus."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import merge_catalogs as merger
import prepare_search


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "merged"

    def source(self, name, items, category="technology", media=None):
        runtime = self.root / name / "runtime"
        (runtime / "guides").mkdir(parents=True)
        (runtime / "media").mkdir()
        license_name = "CC BY-SA 4.0" if name == "wikimed" else "CC BY-NC-SA 3.0"
        descriptor = {"id": name, "name": name.title(), "archive": name + ".zim",
                      "url": "https://example.org/" + name,
                      "license": {"name": license_name, "url": "https://example.org/license/" + name},
                      "attribution": name + " contributors"}
        groups, references, rows = [""], [], []
        for identifier, title, alias in items:
            if alias not in groups:
                groups.append(alias)
            references.append(groups.index(alias))
            rows.append([identifier, title, category, "article", ""])
            record = {"id": identifier, "title": title, "sourceId": name, "sourceName": name,
                      "sourceUrl": descriptor["url"] + "/" + identifier, "license": descriptor["license"],
                      "attribution": descriptor["attribution"], "html": "<p>All original instructions and warnings.</p>"}
            body = merger.json_bytes(record)
            (runtime / "guides" / (identifier + ".json")).write_bytes(body)
            (runtime / "guides" / (identifier + ".json.gz")).write_bytes(gzip.compress(body, mtime=0))
        media = [b"shared image"] if media is None else media
        for body in media:
            filename = hashlib.sha256(body).hexdigest() + ".png"
            (runtime / "media" / filename).write_bytes(body)
        index = {"version": 1, "revision": name + "_2026-10", "source": descriptor,
                 "categories": [{"id": category, "name": category.title()}], "fields": merger.FIELDS,
                 "items": rows, "search": {"version": 1, "normalization": "NFKD-no-marks-lower",
                                           "groups": groups, "itemGroups": references,
                                           "sources": [{"id": "Q1", "revision": 12, "license": "CC0"}],
                                           "note": name + " alias attribution",
                                           "inverted": {"oldSourcePositions": True}}}
        body = merger.json_bytes(index)
        packed = gzip.compress(body, mtime=0)
        pin = hashlib.sha256(name.encode()).hexdigest()
        manifest = {"version": 1, "revision": index["revision"], "source": descriptor,
                    "archiveSha256": pin, "expectedSha256": pin, "archiveBytes": 1000,
                    "archiveChecksumVerified": True, "zimChecksumVerified": True,
                    "archiveMetadata": {"Language": "eng", "Name": name},
                    "indexedItems": len(items), "uniqueMediaFiles": len(media),
                    "mediaBytes": sum(map(len, media)), "indexBytes": len(body), "indexGzipBytes": len(packed),
                    "searchEnrichment": {"version": 1, "enrichedItems": sum(bool(item[2]) for item in items)},
                    "searchIndex": {"version": 1, "indexSha256": hashlib.sha256(body).hexdigest()},
                    "runtimeComplete": True}
        (runtime / "index.json").write_bytes(body)
        (runtime / "index.json.gz").write_bytes(packed)
        (runtime / "manifest.json").write_bytes(merger.json_bytes(manifest))
        return runtime

    def manifest_change(self, runtime, **changes):
        path = runtime / "manifest.json"
        manifest = json.loads(path.read_bytes())
        manifest.update(changes)
        path.write_bytes(merger.json_bytes(manifest))

    def test_aliases_sorting_provenance_and_hardlinks(self):
        first = self.source("ifixit", [("a" * 20, "Zulu laptop", "notebook"),
                                       ("c" * 20, "Battery", "shared alias")])
        second = self.source("wikimed", [("b" * 20, "Asthma", "asthmatic"),
                                         ("d" * 20, "Breathing", "shared alias")], category="health")
        original = {path: (path.read_bytes(), path.stat().st_ino) for source in (first, second)
                    for path in source.rglob("*") if path.is_file()}
        result = merger.merge_catalogs([second, first], self.output)
        self.assertEqual(result, self.output)
        index = json.loads((result / "index.json").read_bytes())
        manifest = json.loads((result / "manifest.json").read_bytes())
        self.assertEqual([row[1] for row in index["items"]], ["Asthma", "Battery", "Breathing", "Zulu laptop"])
        aliases = {row[0]: index["search"]["groups"][reference]
                   for row, reference in zip(index["items"], index["search"]["itemGroups"])}
        self.assertEqual(aliases, {"a" * 20: "notebook", "b" * 20: "asthmatic",
                                   "c" * 20: "shared alias", "d" * 20: "shared alias"})
        self.assertEqual(len(index["search"]["groups"]), 4)
        self.assertNotIn("inverted", index["search"])
        self.assertEqual(len(index["search"]["sources"]), 1)
        self.assertNotIn("license", index["source"])
        self.assertEqual([source["license"]["name"] for source in index["sources"]],
                         ["CC BY-NC-SA 3.0", "CC BY-SA 4.0"])
        self.assertEqual([source["archiveMetadata"]["Language"] for source in manifest["sources"]], ["eng", "eng"])
        self.assertEqual(manifest["categoryCounts"], {"health": 2, "technology": 2})
        self.assertEqual(manifest["uniqueMediaFiles"], 1)
        self.assertEqual(manifest["searchEnrichment"]["enrichedItems"], 4)
        self.assertEqual(manifest["archiveDigestKind"], "sha256-source-pins-v1")
        self.assertEqual(manifest["archiveSha256"], manifest["expectedSha256"])
        self.assertTrue(manifest["runtimeComplete"])
        self.assertEqual(gzip.decompress((result / "index.json.gz").read_bytes()), (result / "index.json").read_bytes())
        for path, (body, inode) in original.items():
            self.assertEqual(path.read_bytes(), body)
            self.assertEqual(path.stat().st_ino, inode)
            if path.parent.name in {"guides", "media"}:
                self.assertEqual((result / path.parent.name / path.name).stat().st_ino, inode if path.parent.name == "guides"
                                 or path.parts[-3] == "ifixit" else (first / "media" / path.name).stat().st_ino)

    def test_deterministic_input_order_and_prepare_search_compatibility(self):
        first = self.source("ifixit", [("a" * 20, "Laptop battery", "notebook")])
        second = self.source("wikimed", [("b" * 20, "Asthma", "asthmatic")], category="health")
        merger.merge_catalogs([first, second], self.output)
        other = merger.merge_catalogs([second, first], self.root / "other")
        for name in ("index.json", "index.json.gz", "manifest.json"):
            self.assertEqual((self.output / name).read_bytes(), (other / name).read_bytes())
        (self.root / "prepare").mkdir()
        prepared = prepare_search.prepare(self.root / "prepare", self.output)
        self.assertEqual(prepared["documents"], 2)
        self.assertIn("asthmatic", json.loads((self.output / "index.json").read_bytes())["search"]["inverted"]["terms"])
        self.assertNotIn("inverted", json.loads((other / "index.json").read_bytes())["search"])

    def test_published_directories_are_traversable_without_changing_source_modes(self):
        source = self.source("wikimed", [("a" * 20, "Asthma", "asthmatic")], category="health")
        guide = source / "guides" / ("a" * 20 + ".json")
        guide.chmod(0o640)
        image = next((source / "media").iterdir())
        original_modes = {path: stat.S_IMODE(path.stat().st_mode) for path in (guide, image)}
        stage_modes = []
        hardlink = merger._hardlink
        def observe_private_stage(source_path, destination):
            stage_modes.append(stat.S_IMODE(destination.parent.parent.stat().st_mode))
            return hardlink(source_path, destination)
        old_umask = os.umask(0o077)
        try:
            with patch.object(merger, "_hardlink", side_effect=observe_private_stage):
                merger.merge_catalogs([source], self.output)
        finally:
            os.umask(old_umask)
        self.assertTrue(stage_modes)
        self.assertEqual(set(stage_modes), {0o700}, "Unfinished merge stays private")
        for path in (self.output, self.output / "guides", self.output / "media"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755, str(path))
        for name in ("index.json", "index.json.gz", "manifest.json"):
            self.assertEqual(stat.S_IMODE((self.output / name).stat().st_mode), 0o644, name)
        for path, mode in original_modes.items():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
            linked = self.output / path.parent.name / path.name
            self.assertEqual(linked.stat().st_ino, path.stat().st_ino)
            self.assertEqual(stat.S_IMODE(linked.stat().st_mode), mode)

    def test_duplicate_ids_and_categories_rejected_before_output(self):
        first = self.source("ifixit", [("a" * 20, "Laptop", "")])
        second = self.source("wikimed", [("a" * 20, "Asthma", "")], category="health")
        with self.assertRaisesRegex(ValueError, "ID collision"):
            merger.merge_catalogs([first, second], self.output)
        self.assertFalse(self.output.exists())
        third = self.source("other", [("c" * 20, "Other", "")])
        index_path = third / "index.json"
        index = json.loads(index_path.read_bytes())
        index["categories"][0]["name"] = "Conflicting name"
        body = merger.json_bytes(index)
        packed = gzip.compress(body, mtime=0)
        index_path.write_bytes(body)
        (third / "index.json.gz").write_bytes(packed)
        self.manifest_change(third, indexBytes=len(body), indexGzipBytes=len(packed), searchIndex={})
        with self.assertRaisesRegex(ValueError, "Conflicting category"):
            merger.merge_catalogs([first, third], self.output)

    def test_partial_or_unverified_source_rejected(self):
        source = self.source("wikimed", [("a" * 20, "Asthma", "")])
        for changes in ({"runtimeComplete": False}, {"archiveChecksumVerified": False},
                        {"zimChecksumVerified": False}, {"expectedSha256": "0" * 64},
                        {"searchEnrichment": {}}, {"indexedItems": 100}):
            original = (source / "manifest.json").read_bytes()
            self.manifest_change(source, **changes)
            with self.assertRaisesRegex(ValueError, "complete, verified"):
                merger.merge_catalogs([source], self.output)
            (source / "manifest.json").write_bytes(original)
            self.assertFalse(self.output.exists())

    def test_missing_guides_cleanup_staging_directory(self):
        source = self.source("wikimed", [("a" * 20, "Asthma", "")])
        (source / "guides" / ("a" * 20 + ".json.gz")).unlink()
        with self.assertRaisesRegex(ValueError, "missing JSON or gzip"):
            merger.merge_catalogs([source], self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob("merged.merge-*")), [])
        self.assertTrue((source / "guides" / ("a" * 20 + ".json")).is_file())

    def test_media_hash_collision_compares_bytes_even_when_sizes_match(self):
        first = self.source("ifixit", [("a" * 20, "Laptop", "")])
        second = self.source("wikimed", [("b" * 20, "Asthma", "")], category="health")
        image = next((second / "media").iterdir())
        image.write_bytes(b"wrong image!")
        self.assertEqual(image.stat().st_size, next((first / "media").iterdir()).stat().st_size)
        with self.assertRaisesRegex(ValueError, "media collision"):
            merger.merge_catalogs([first, second], self.output)
        self.assertFalse(self.output.exists())

    def test_symlink_inputs_and_path_traversal_are_rejected(self):
        source = self.source("wikimed", [("a" * 20, "Asthma", "")])
        alias = self.root / "source-link"
        alias.symlink_to(source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "not symlinks"):
            merger.merge_catalogs([alias], self.output)
        payload = source / "guides" / ("a" * 20 + ".json")
        saved = self.root / "saved.json"
        payload.rename(saved)
        payload.symlink_to(saved)
        with self.assertRaisesRegex(ValueError, "not symlinks"):
            merger.merge_catalogs([source], self.output)
        payload.unlink()
        saved.rename(payload)
        index_path = source / "index.json"
        index = json.loads(index_path.read_bytes())
        index["items"][0][0] = "../escaping-file"
        body = merger.json_bytes(index)
        packed = gzip.compress(body, mtime=0)
        index_path.write_bytes(body)
        (source / "index.json.gz").write_bytes(packed)
        self.manifest_change(source, indexBytes=len(body), indexGzipBytes=len(packed), searchIndex={})
        with self.assertRaisesRegex(ValueError, "invalid or duplicate catalog rows"):
            merger.merge_catalogs([source], self.output)
        self.assertFalse((self.root / "escaping-file").exists())

    def test_existing_outputs_and_overlap_are_never_overwritten(self):
        source = self.source("wikimed", [("a" * 20, "Asthma", "")])
        merger.merge_catalogs([source], self.output)
        before = (self.output / "index.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "new or empty"):
            merger.merge_catalogs([source], self.output)
        self.assertEqual((self.output / "index.json").read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "must not overlap"):
            merger.merge_catalogs([source], source / "nested")
        empty = self.root / "empty"
        empty.mkdir()
        merger.merge_catalogs([source], empty)
        self.assertTrue((empty / "manifest.json").is_file())

    def test_pdf_sources_keep_document_proofs_and_rough_payloads(self):
        zim = self.source("wikimed", [("a" * 20, "Asthma", "")], category="health")
        pdf = self.source("army-first-aid", [("b" * 20, "Chapter 1: First aid", "aid")],
                          category="health", media=[])
        self.manifest_change(pdf, sourceFormat="pdf", documentVerified=True, zimChecksumVerified=None,
                             documentPages=123, chapters=[{"title": "First aid", "page": 1}])
        payload = pdf / "guides" / ("b" * 20 + ".json")
        original = payload.read_bytes()
        merger.merge_catalogs([zim, pdf], self.output)
        manifest = json.loads((self.output / "manifest.json").read_bytes())
        self.assertTrue(manifest["sourceChecksumVerified"])
        self.assertIsNone(manifest["zimChecksumVerified"])
        self.assertEqual(manifest["sourceFormat"], "collection")
        preserved = next(source for source in manifest["sources"] if source["sourceFormat"] == "pdf")
        self.assertEqual(preserved["chapters"], [{"title": "First aid", "page": 1}])
        self.assertEqual((self.output / "guides" / payload.name).read_bytes(), original)
        self.manifest_change(pdf, documentVerified=False)
        with self.assertRaisesRegex(ValueError, "complete, verified"):
            merger.merge_catalogs([pdf], self.root / "bad-pdf")


if __name__ == "__main__":
    unittest.main()
