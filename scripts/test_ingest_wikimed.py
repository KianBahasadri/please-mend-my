"""New-source tests use tiny generated ZIMs, never the real catalog.

Run with the ingestion pins: uv run --python 3.12.12 --with libzim==4.1.0
--with lxml==6.1.3 python -m unittest discover -s scripts -p test_ingest_wikimed.py
"""
import base64
import gzip
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from libzim.writer import Creator, Hint, Item, StringProvider
from lxml import html

import ingest_wikimed as ingest


class FixtureItem(Item):
    def __init__(self, path, title, body, mime="text/html"):
        super().__init__()
        self.path, self.title, self.body, self.mime = path, title, body, mime

    def get_path(self):
        return self.path

    def get_title(self):
        return self.title

    def get_mimetype(self):
        return self.mime

    def get_contentprovider(self):
        return StringProvider(self.body)

    def get_hints(self):
        return {Hint.FRONT_ARTICLE: self.mime == "text/html"}


def make_zim(path):
    creator = Creator(path).config_indexing(False, "eng").config_nbworkers(1)
    pixel = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==")
    with creator:
        creator.set_mainpath("mainPage")
        for name, value in {"Name": "fixture_wikimed", "Language": "eng", "Title": "WikiMed fixture", "Date": "2026-10-10"}.items():
            creator.add_metadata(name, value)
        creator.add_item(FixtureItem("mainPage", "Main page", "<html><body>Source navigation</body></html>"))
        creator.add_item(FixtureItem("File:Diagram.png", "File description", "<p>Image attribution</p>"))
        creator.add_item(FixtureItem("A/Asthma", "Asthma", """<html lang="en"><head><script id="mwoffliner-jsConfigVars">RLCONF = {"wgCurRevisionId": 12345, "wgCategories":["Respiratory diseases"]};evil()</script></head><body onload="evil()"><h1>Asthma</h1><div id="mw-content-text"><div class="mw-parser-output"><p>Asthma affects the airways.</p><p class="noprint">Seek urgent care for severe symptoms.</p><h2 id="Treatment">Treatment</h2><p>Medication instructions preserved.</p><a href="../Heart_attack">Heart attack</a><a href="../Myocardial_infarction">Alias link</a><a href="#Treatment">Treatment section</a><a href="../File:Diagram.png"><img src="../_assets_/x/diagram.png" alt="Diagram" srcset="https://tracker.invalid/a 2x" onerror="evil()"></a><img src="https://tracker.invalid/missing.png"><script>evil()</script><iframe src="https://tracker.invalid"></iframe><a href="javascript:evil()">Bad link</a><p id="refs">References <a href="https://example.org/study">Study</a></p><math alttext="x+y"><mi>x</mi></math><div class="toc">Navigation removed</div></div></div></body></html>"""))
        creator.add_item(FixtureItem("Heart_attack", "Heart attack", "<html><body><p>A myocardial infarction article.</p><p>References retained.</p></body></html>"))
        creator.add_item(FixtureItem("_assets_/x/diagram.png", "Diagram", pixel, "image/png"))
        creator.add_item(FixtureItem("_assets_/x/duplicate.png", "Duplicate diagram", pixel, "image/png"))
        creator.add_redirection("Bronchial_asthma", "Bronchial asthma", "A/Asthma", {Hint.FRONT_ARTICLE: True})
        creator.add_redirection("Myocardial_infarction", "Myocardial infarction", "Heart_attack", {Hint.FRONT_ARTICLE: True})


class WikiMedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_namespaces_do_not_discard_real_colon_or_slash_titles(self):
        self.assertEqual(ingest.article_name("A/Heart_attack", legacy=True), "Heart_attack")
        self.assertEqual(ingest.article_name("A/Heart_attack"), "A/Heart_attack")
        self.assertEqual(ingest.article_name("I/Real_article"), "I/Real_article")
        self.assertEqual(ingest.source_url("A/B"), "https://en.wikipedia.org/wiki/A/B")
        self.assertEqual(ingest.source_url("A/B", legacy=True), "https://en.wikipedia.org/wiki/B")
        self.assertEqual(ingest.article_name("COVID-19/Long_term_effects"), "COVID-19/Long_term_effects")
        self.assertEqual(ingest.article_name("Re:_Condition"), "Re:_Condition")
        for name in ["mainPage", "Main_Page", "A/Category:Infections", "File:Foo.png", "User_talk:X", "-/styles.css", "I/photo.jpg"]:
            self.assertIsNone(ingest.article_name(name, legacy=True), name)
        self.assertNotEqual(ingest.short_id("Heart_attack"), ingest.short_id("ifixit:Heart_attack"))

    def test_reference_resolution_handles_namespaces_and_encoded_titles(self):
        self.assertIn("A/Heart_attack", ingest.resolve_reference("A/Asthma", "./Heart_attack"))
        self.assertIn("Heart_attack", ingest.resolve_reference("A/Asthma", "https://en.wikipedia.org/wiki/Heart_attack"))
        self.assertEqual(ingest.resolve_reference("Asthma", "https://tracker.invalid/Asthma"), [])
        self.assertEqual(ingest.normalized_path("A/Foo%20bar%23name?x=1#tail"), "A/Foo bar#name")
        self.assertEqual(ingest.external_url("Asthma", "File:Diagram.png"), "https://en.wikipedia.org/wiki/File:Diagram.png")
        self.assertIn("Question?", ingest.resolve_reference("Question?", "Question%3F"))
        self.assertIn("Percent%3F", ingest.resolve_reference("Question?", "Percent%253F"))
        self.assertEqual(ingest.source_url("Question?"), "https://en.wikipedia.org/wiki/Question%3F")
        self.assertEqual(ingest.source_url("Percent%3F"), "https://en.wikipedia.org/wiki/Percent%253F")
        self.assertEqual(ingest.resolve_reference("A/B", "./C"), ["A/C"])
        self.assertEqual(ingest.resolve_reference("A/B", "https://en.wikipedia.org/wiki/A/C"), ["A/C"])
        self.assertEqual(ingest.resolve_reference("A/B", "https://en.wikipedia.org/wiki/C", legacy=True), ["A/C", "C"])

    def test_sanitizer_preserves_content_without_remote_subresources(self):
        identifier = ingest.short_id("A/Asthma")
        tree = html.fromstring("""<body onload="bad()"><p>Warning: urgent care.</p><a href="#a">Ref</a><span id="a">Target</span><img src="../I/x.png" onerror="bad()" srcset="https://bad/x"><img src="https://bad/x"><script>bad()</script><a href="data:text/html,x">Bad</a><math alttext="a+b"></math></body>""")
        body, images, provenance, _ = ingest.sanitize(tree, "A/Asthma", identifier, {}, {"I/x.png": "/media/abc.png"})
        parsed = html.fromstring(body)
        self.assertIn("Warning: urgent care.", parsed.text_content())
        self.assertIn("a+b", parsed.text_content())
        self.assertEqual(images, ["/media/abc.png"])
        self.assertEqual(provenance[0]["sourcePath"], "I/x.png")
        self.assertFalse(parsed.xpath(".//script|.//iframe"))
        self.assertEqual([img.get("src") for img in parsed.xpath(".//img")], ["/media/abc.png"])
        for node in parsed.iter():
            self.assertFalse(any(key.startswith("on") or key in {"style", "srcset"} for key in node.attrib))
        self.assertEqual(parsed.xpath(".//a")[0].get("href"), "#" + parsed.xpath(".//span[@id]")[0].get("id"))

    def test_real_zim_build_retains_redirect_aliases_provenance_and_deduplicated_images(self):
        archive = self.root / "fixture.zim"
        make_zim(archive)
        checksum = ingest.digest_file(archive)
        metadata = {"size": archive.stat().st_size, "sha256": checksum}
        data = self.root / "data"
        data.mkdir()
        result = ingest.build(data, archive, metadata, checksum)
        self.assertTrue(result["runtimeComplete"])
        self.assertEqual(result["indexedItems"], 2)
        self.assertEqual(result["uniqueMediaFiles"], 1)
        index_bytes = (data / "runtime/index.json").read_bytes()
        index = json.loads(index_bytes)
        self.assertEqual(gzip.decompress((data / "runtime/index.json.gz").read_bytes()), index_bytes)
        self.assertEqual(index["source"]["id"], "wikimed")
        self.assertEqual(index["categories"], [{"id": "health", "name": "Health & medicine"}])
        by_title = {row[1]: (row, index["search"]["groups"][index["search"]["itemGroups"][pos]]) for pos, row in enumerate(index["items"])}
        self.assertEqual(by_title["Heart attack"][1], "myocardial infarction")
        self.assertEqual(by_title["Asthma"][1], "bronchial asthma")
        detail = json.loads((data / "runtime/guides" / (by_title["Asthma"][0][0] + ".json")).read_bytes())
        self.assertEqual(detail["license"], ingest.LICENSE)
        self.assertEqual(detail["sourceName"], "Wikipedia")
        self.assertEqual(detail["sourceRevisionId"], 12345)
        self.assertIn("oldid=12345", detail["sourceRevisionUrl"])
        self.assertIn("Category:Respiratory_diseases", detail["sourceCategories"])
        self.assertIn("Seek urgent care for severe symptoms.", detail["html"])
        self.assertIn("References", detail["html"])
        self.assertNotIn("sanitized", detail["attribution"])
        self.assertTrue(detail["mediaSources"][0]["filePageUrl"].endswith("/File:Diagram.png"))
        with sqlite3.connect(data / "catalog.sqlite") as database:
            self.assertEqual(database.execute("SELECT count(*) FROM items").fetchone()[0], 2)
            self.assertEqual(database.execute("SELECT count(*) FROM redirects").fetchone()[0], 2)
            self.assertEqual(database.execute("SELECT count(*) FROM media").fetchone()[0], 2)
        self.assertFalse((data / "runtime-building").exists())

    def test_checksum_and_upstream_pin_mismatch_fail_before_build(self):
        archive = self.root / "bad.zim"
        archive.write_bytes(b"bad")
        with self.assertRaisesRegex(RuntimeError, "SHA-256 mismatch"):
            ingest.verify(archive, {"size": 3, "sha256": "wrong"})
        metalink = self.root / (ingest.ARCHIVE_NAME + ".meta4")
        metalink.write_text('<metalink xmlns="urn:ietf:params:xml:ns:metalink"><file><size>3</size><hash type="sha-256">wrong</hash><url>https://example.org/test.zim</url></file></metalink>')
        with self.assertRaisesRegex(RuntimeError, "differs from pinned"):
            ingest.source_metadata(self.root)

    def test_incomplete_source_is_never_reused(self):
        runtime = self.root / "runtime"
        runtime.mkdir()
        (runtime / "manifest.json").write_text(json.dumps({"runtimeComplete": False, "ingestionKey": ingest.ingestion_key()}))
        self.assertIsNone(ingest.completed(self.root))

    def test_completed_source_reuses_without_network_archive_hash_or_corpus_scan(self):
        archive = self.root / "fixture.zim"
        make_zim(archive)
        checksum = ingest.digest_file(archive)
        data = self.root / "data"
        data.mkdir()
        with patch.object(ingest, "ARCHIVE_SHA256", checksum):
            ingest.build(data, archive, {"sha256": checksum}, checksum)
            self.assertIsNotNone(ingest.completed(data))
            with (patch.object(ingest, "source_metadata", side_effect=AssertionError("unexpected network")),
                  patch.object(ingest, "digest_file", side_effect=AssertionError("unexpected archive hash")),
                  patch.object(Path, "glob", side_effect=AssertionError("unexpected corpus scan")),
                  patch.object(ingest.sqlite3, "connect", side_effect=AssertionError("unexpected corpus read")),
                  patch("sys.argv", ["ingest_wikimed.py", "--data-dir", str(data)])):
                ingest.main()

    def test_literal_title_punctuation_and_percent_encoding_never_merge_articles(self):
        archive = self.root / "punctuation.zim"
        creator = Creator(archive).config_indexing(False, "eng").config_nbworkers(1)
        paths = ["Question", "Question?", "Percent?", "Percent%3F", "Hash#tag"]
        with creator:
            creator.set_mainpath("Question")
            for path in paths:
                creator.add_item(FixtureItem(path, path, '<html><body><p>Distinct source article.</p><a href="Question%3F">Question mark</a><a href="Percent%253F">Literal percent</a></body></html>'))
        data = self.root / "data"
        data.mkdir()
        checksum = ingest.digest_file(archive)
        result = ingest.build(data, archive, {"sha256": checksum}, checksum)
        self.assertEqual(result["indexedItems"], len(paths))
        with sqlite3.connect(data / "catalog.sqlite") as database:
            self.assertEqual({row[0] for row in database.execute("SELECT source_path FROM items")}, set(paths))
        for path in paths:
            detail = json.loads((data / "runtime/guides" / (ingest.short_id(path) + ".json")).read_bytes())
            parsed = html.fromstring(detail["html"])
            self.assertEqual(parsed.xpath(".//a")[0].get("href"), "/?guide=" + ingest.short_id("Question?"))
            self.assertEqual(parsed.xpath(".//a")[1].get("href"), "/?guide=" + ingest.short_id("Percent%3F"))

    def test_existing_completed_runtime_survives_failed_new_build(self):
        archive = self.root / "fixture.zim"
        make_zim(archive)
        data = self.root / "data"
        (data / "runtime").mkdir(parents=True)
        previous = b'{"runtimeComplete":true,"revision":"previous"}'
        (data / "runtime/manifest.json").write_bytes(previous)
        with patch.object(ingest, "sanitize", side_effect=ValueError("fixture parse failure")):
            with self.assertRaisesRegex(ValueError, "fixture parse failure"):
                ingest.build(data, archive, {"sha256": "fixture"}, "fixture")
        self.assertEqual((data / "runtime/manifest.json").read_bytes(), previous)
        self.assertFalse(json.loads((data / "runtime-building/manifest.json").read_bytes())["runtimeComplete"])

    def test_fork_normalization_matches_sequential_output(self):
        archive = self.root / "fixture.zim"
        make_zim(archive)
        checksum = ingest.digest_file(archive)
        runtimes = []
        for workers in (1, 2):
            data = self.root / ("workers-" + str(workers))
            data.mkdir()
            ingest.build(data, archive, {"sha256": checksum}, checksum, worker_count=workers)
            runtimes.append(data / "runtime")
        self.assertEqual((runtimes[0] / "index.json").read_bytes(), (runtimes[1] / "index.json").read_bytes())
        for payload in (runtimes[0] / "guides").iterdir():
            self.assertEqual(payload.read_bytes(), (runtimes[1] / "guides" / payload.name).read_bytes())

    def test_unpublished_inventory_avoids_per_batch_durable_fsync(self):
        archive = self.root / "fixture.zim"
        make_zim(archive)
        checksum = ingest.digest_file(archive)
        data = self.root / "data"
        data.mkdir()
        statements = []
        connect = sqlite3.connect
        def traced_connect(path, *args, **kwargs):
            connection = connect(path, *args, **kwargs)
            if Path(path).name == "catalog-building.sqlite":
                connection.set_trace_callback(statements.append)
            return connection
        with patch.object(ingest.sqlite3, "connect", side_effect=traced_connect):
            ingest.build(data, archive, {"sha256": checksum}, checksum)
        self.assertIn("PRAGMA journal_mode=OFF", statements)
        self.assertIn("PRAGMA synchronous=OFF", statements)
        self.assertTrue(json.loads((data / "runtime/manifest.json").read_bytes())["runtimeComplete"])


if __name__ == "__main__":
    unittest.main()
