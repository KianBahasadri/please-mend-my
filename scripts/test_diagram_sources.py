"""Source integrity and XML security shared by all diagram discovery."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import diagram_discovery as sources
import diagram_svg as svg

SVG = b'<svg xmlns="http://www.w3.org/2000/svg">' + b'<rect width="10" height="10"/>' * 8 + b'</svg>'


class DiagramSourceIntegrityTests(unittest.TestCase):
    def test_vector_gate_rejects_raster_active_external_and_tiny_sources(self):
        self.assertEqual(svg.inspect_svg(SVG)['shapes'], 8)
        self.assertEqual(svg.inspect_svg(SVG.replace(b'<rect ', b'<rect fill="url(\'#paint\')" '))['shapes'], 8)
        for extra in [b'<image href="data:image/png;base64,a"/>', b'<script/>', b'<foreignObject/>',
                      b'<use href="https://example.com/file.svg#shape"/>', b'<path onclick="bad()"/>',
                      b'<path fill="url(https://example.com/paint.svg#x)"/>',
                      b'<style>@import url(https://example.com/style.css);</style>',
                      b'<style>path{fill:url(https://example.com/paint.svg#x)}</style>',
                      b'<feImage href="https://example.com/raster.png"/>']:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                svg.inspect_svg(SVG.replace(b'</svg>', extra + b'</svg>'))
        with self.assertRaises(ValueError): svg.inspect_svg(b'<svg><path d="M0 0"/></svg>')

    def test_unused_namespace_entity_is_stripped_without_xml_resolution(self):
        dtd = b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "https://example.com/never-fetch.dtd" [<!ENTITY ns_flows "http://ns.adobe.com/Flows/1.0/">]>'
        self.assertEqual(svg.safe_svg_xml(dtd + SVG), SVG)
        self.assertEqual(svg.inspect_svg(dtd + SVG)['shapes'], 8)
        for declaration, suffix in [
            (b'<!ENTITY x "&y;">', b''),
            (b'<!ENTITY x SYSTEM "file:///etc/passwd">', b''),
            (b'<!ENTITY % x "namespace">', b''),
            (b'<!ENTITY x "expanded">', b'&x;'),
        ]:
            payload = b'<!DOCTYPE svg [' + declaration + b']>' + SVG + suffix
            with self.subTest(declaration=declaration), self.assertRaises(ValueError): svg.safe_svg_xml(payload)

    def test_license_filter_requires_explicit_supported_source_metadata(self):
        metadata = {'LicenseShortName': {'value': 'CC BY-SA 4.0'},
                    'LicenseUrl': {'value': 'http://creativecommons.org/licenses/by-sa/4.0/'}}
        self.assertEqual(sources.license_from_metadata(metadata), ('CC BY-SA 4.0', 'https://creativecommons.org/licenses/by-sa/4.0/'))
        for name in ['GFDL', 'MIT', 'All rights reserved', '']:
            metadata['LicenseShortName']['value'] = name
            with self.subTest(name=name), self.assertRaises(ValueError): sources.license_from_metadata(metadata)
        self.assertEqual(sources.license_from_metadata({'LicenseShortName': {'value': 'CC0'}})[0], 'CC0 1.0')
        with self.assertRaises(ValueError):
            sources.license_from_metadata({'LicenseShortName': {'value': 'CC BY 4.0'}, 'LicenseUrl': {'value': 'https://creativecommons.org/licenses/by-sa/4.0/'}})

    def test_manifest_verify_is_offline_and_checks_exact_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = {'id': 'arm-test', 'file': 'arm.original.svg', 'sha256': hashlib.sha256(SVG).hexdigest(),
                      'bytes': len(SVG), 'license': 'CC0 1.0'}
            manifest = {'version': 1, 'archetypes': [{'id': 'arm', 'candidates': [record]}]}
            path = root / 'sources.json'; path.write_text(json.dumps(manifest)); (root / record['file']).write_bytes(SVG)
            with patch.object(sources, 'request_bytes', side_effect=AssertionError('offline verify must not fetch')):
                self.assertEqual(sources.verify_manifest(path), {'archetypes': 1, 'candidates': 1})
            (root / record['file']).write_bytes(SVG + b' ')
            with self.assertRaises(ValueError): sources.verify_manifest(path)
            record['file'] = '../private.svg'; path.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError): sources.verify_manifest(path)

    def test_pinned_fetch_rejects_changed_upstream_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = {'id': 'arm-test', 'file': 'arm.original.svg', 'sha256': hashlib.sha256(SVG).hexdigest(),
                      'bytes': len(SVG), 'license': 'CC0 1.0', 'download': 'https://example.com/source.svg'}
            path = root / 'sources.json'; path.write_text(json.dumps({'version': 1, 'archetypes': [{'id': 'arm', 'candidates': [record]}]}))
            with patch.object(sources, 'request_bytes', return_value=SVG + b' '):
                with self.assertRaises(ValueError): sources.verify_manifest(path, fetch_missing=True)
            self.assertFalse((root / record['file']).exists(), 'changed bytes must never be published')
            with patch.object(sources, 'request_bytes', return_value=SVG):
                self.assertEqual(sources.verify_manifest(path, fetch_missing=True)['candidates'], 1)

    def test_commons_discovery_is_cached_and_pins_required_metadata_request(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sources, 'request_bytes', return_value=b'{"query":{"pages":[]}}') as fetch:
                sources.commons_search('arm diagram', Path(directory), limit=3)
                sources.commons_search('arm diagram', Path(directory), limit=3)
                self.assertEqual(fetch.call_count, 1)
                url = fetch.call_args.args[0]
                self.assertIn('maxlag=5', url)
                self.assertIn('extmetadata', url)
                self.assertIn('timestamp', url)
                self.assertIn('sha1', url)

    def test_commons_json_maxlag_is_retried_serially_and_never_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            replies = [b'{"error":{"code":"maxlag"}}', b'{"query":{"pages":[]}}']
            with patch.object(sources, 'request_bytes', side_effect=replies) as fetch, \
                 patch.object(sources.time, 'sleep') as sleep:
                sources.commons_search('arm diagram', Path(directory), limit=3)
                sources.commons_search('arm diagram', Path(directory), limit=3)
                self.assertEqual(fetch.call_count, 2, 'the recovered response is reused without new requests')
                sleep.assert_called_once()
            cached = list((Path(directory) / 'discovery-cache/commons').glob('*.json'))
            self.assertEqual(len(cached), 1)
            self.assertNotIn('error', json.loads(cached[0].read_text()))
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(sources, 'request_bytes', return_value=b'{"error":{"code":"maxlag"}}') as fetch, \
             patch.object(sources.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, 'maxlag'):
                sources.commons_search('arm diagram', Path(directory), limit=3)
            self.assertEqual(fetch.call_count, 3)
            self.assertEqual(sleep.call_count, 2)
            self.assertFalse((Path(directory) / 'discovery-cache/commons').exists())

if __name__ == "__main__":
    unittest.main()
