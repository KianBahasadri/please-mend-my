import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import diagram_discovery as discovery


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">' + b'<rect width="10" height="10"/>' * 16 + b'</svg>'
SUBJECT = {"id": "oewn-test-n", "label": "water pump", "aliases": ["water pump"], "svgQueries": ["water pump diagram"]}


def commons_page(body=SVG):
    return {"title": "File:Water pump diagram.svg", "imageinfo": [{
        "mime": "image/svg+xml", "size": len(body), "sha1": hashlib.sha1(body).hexdigest(),
        "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Water_pump.svg",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Water_pump.svg",
        "extmetadata": {"Artist": {"value": "Example Artist"},
            "LicenseShortName": {"value": "CC BY-SA 4.0"},
            "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0/"},
            "ImageDescription": {"value": "<p>Water pump with impeller labels.</p>"}}}]}


class DiagramDiscoveryTests(unittest.TestCase):
    def test_commons_pin_retains_original_metadata_and_sha(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(discovery, "request_bytes", return_value=SVG):
            row = discovery.commons_candidate(commons_page(), SUBJECT, directory)
            self.assertEqual(Path(row["file"]).read_bytes(), SVG)
            self.assertTrue(Path(row["file"]).is_absolute())
            self.assertEqual(row["sha256"], hashlib.sha256(SVG).hexdigest())
            self.assertEqual(row["author"], "Example Artist")
            self.assertEqual(row["license"], "CC BY-SA 4.0")
            self.assertEqual(row["description"], "Water pump with impeller labels.")
            self.assertIn("ImageDescription", row["extmetadata"])
            self.assertEqual(row["shapeCount"], 16)
            self.assertEqual(row["sourceUrl"], row["source"])
            self.assertEqual(row["downloadUrl"], row["download"])

    def test_changed_upstream_bytes_never_pin_a_candidate(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(discovery, "request_bytes", return_value=SVG + b" "):
            with self.assertRaisesRegex(ValueError, "size changed"):
                discovery.commons_candidate(commons_page(), SUBJECT, directory)
            self.assertFalse((Path(directory) / "sources").exists())

    def test_active_raster_and_over_complex_sources_are_rejected(self):
        record = {"source": "https://commons.wikimedia.org/wiki/File:Test.svg"}
        with tempfile.TemporaryDirectory() as directory:
            for extra in (b'<script/>', b'<image href="data:image/png;base64,abc"/>', b'<path onload="bad()"/>'):
                with self.subTest(extra=extra), self.assertRaises(ValueError):
                    discovery.pin(record, SVG.replace(b'</svg>', extra + b'</svg>'), SUBJECT, directory)
            many = b'<svg>' + b'<rect/>' * 501 + b'</svg>'
            with self.assertRaisesRegex(ValueError, "500-shape"):
                discovery.pin(record, many, SUBJECT, directory)

    def test_real_subject_guard_is_generic_not_noun_allowlist(self):
        self.assertTrue(discovery.relevant(SUBJECT, "Water pump assembly.svg"))
        self.assertTrue(discovery.relevant(SUBJECT, "Machine.svg", "Diagram of a water pump."))
        for title in ("Water pump exports chart.svg", "Water pump logo.svg", "Electric guitar.svg"):
            with self.subTest(title=title):
                self.assertFalse(discovery.relevant(SUBJECT, title))

    def test_successful_search_including_empty_is_cached_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(discovery, "request_bytes", return_value=b'{"query":{"pages":[]}}') as fetch:
                self.assertEqual(discovery.commons_search("water pump", directory)["query"]["pages"], [])
                discovery.commons_search("water pump", directory, offline=True)
                self.assertEqual(fetch.call_count, 1)
                self.assertIn("extmetadata", fetch.call_args.args[0])
                self.assertIn("maxlag=5", fetch.call_args.args[0])

    def test_api_errors_never_become_successful_empty_cache(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(discovery, "request_bytes", return_value=b'{"error":{"code":"maxlag"}}'), \
             patch.object(discovery.time, "sleep"):
            with self.assertRaisesRegex(discovery.DiscoveryError, "maxlag"):
                discovery.commons_search("water pump", directory)
            self.assertEqual(list(Path(directory).rglob("*.json")), [])

    def test_complete_discovery_replays_offline_and_checks_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(discovery, "local_candidates", return_value=[]):
            with patch.object(discovery, "commons_search", return_value={"query": {"pages": [commons_page()]}}), \
                 patch.object(discovery, "request_bytes", return_value=SVG):
                result = discovery.discover_candidates(SUBJECT, directory, limit=1)
            self.assertEqual(len(result["candidates"]), 1)
            self.assertEqual(result["errors"], [])
            with patch.object(discovery, "request_bytes", side_effect=AssertionError("network forbidden")):
                self.assertEqual(discovery.discover_candidates(SUBJECT, directory, limit=1, offline=True), result)
            Path(result["candidates"][0]["file"]).write_bytes(b"changed")
            with patch.object(discovery, "commons_search", return_value={"query": {"pages": [commons_page()]}}), \
                 patch.object(discovery, "openclipart_search", return_value=[]):
                replay = discovery.discover_candidates(SUBJECT, directory, limit=1, offline=True)
            self.assertEqual(len(replay["candidates"]), 0)
            self.assertTrue(any("changed" in row["reason"] for row in replay["rejected"]))

    def test_transient_provider_failures_are_reported_and_retried(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(discovery, "local_candidates", return_value=[]):
            with patch.object(discovery, "commons_search", side_effect=discovery.DiscoveryError("Upstream HTTP 503")), \
                 patch.object(discovery, "openclipart_search", return_value=[]):
                failed = discovery.discover_candidates(SUBJECT, directory, limit=1)
            self.assertEqual(failed["candidates"], [])
            self.assertEqual(failed["errors"][0]["reason"], "Upstream HTTP 503")
            with patch.object(discovery, "commons_search", return_value={"query": {"pages": [commons_page()]}}) as retry, \
                 patch.object(discovery, "request_bytes", return_value=SVG):
                fixed = discovery.discover_candidates(SUBJECT, directory, limit=1)
            self.assertEqual(retry.call_count, 1)
            self.assertEqual(fixed["errors"], [])
            self.assertEqual(len(fixed["candidates"]), 1)

    def test_openclipart_requires_explicit_cc0(self):
        page = '<h2>Water pump</h2><a href="/artist/Example">Example</a><script type="application/ld+json">{"@type":"ImageObject","license":"https://creativecommons.org/publicdomain/zero/1.0/","description":"Water pump diagram"}</script>'
        with tempfile.TemporaryDirectory() as directory, patch.object(discovery, "cached_html", return_value=page), \
             patch.object(discovery, "request_bytes", return_value=SVG):
            row = discovery.openclipart_candidate("https://openclipart.org/detail/123/water-pump", SUBJECT, directory)
            self.assertEqual(row["author"], "Example")
            self.assertEqual(row["license"], "CC0 1.0")
        with tempfile.TemporaryDirectory() as directory, patch.object(discovery, "cached_html", return_value=page.replace("zero/1.0", "invalid")):
            with self.assertRaisesRegex(ValueError, "explicit CC0"):
                discovery.openclipart_candidate("https://openclipart.org/detail/123/water-pump", SUBJECT, directory)

    def test_unexpected_download_hosts_are_rejected_before_request(self):
        for url in ("http://upload.wikimedia.org/test.svg", "https://example.com/test.svg", "https://secret@upload.wikimedia.org/test.svg"):
            with self.subTest(url=url), patch.object(discovery, "urlopen", side_effect=AssertionError("must reject before request")):
                with self.assertRaises(ValueError):
                    discovery.request_bytes(url)


if __name__ == "__main__":
    unittest.main()
