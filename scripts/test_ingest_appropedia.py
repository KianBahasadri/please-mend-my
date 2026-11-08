"""Appropedia uses a generated tiny ZIM; all media stays local and bounded."""
import gzip
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from libzim.writer import Creator, Hint
from lxml import html

import ingest_appropedia as ingest
from ingest_common import completed, digest_file, short_id
from test_ingest_wikimed import FixtureItem


class AppropediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / 'fixture.zim'
        creator = Creator(self.archive).config_indexing(False, 'eng').config_nbworkers(1)
        with creator:
            creator.set_mainpath('mainPage')
            creator.add_metadata('Language', 'eng')
            creator.add_item(FixtureItem('mainPage', 'Home', '<p>Navigation</p>'))
            creator.add_item(FixtureItem('Solar_water', 'Solar water heater', '''<html lang="en"><body><h1>Solar water heater</h1><div id="mw-content-text"><div class="mw-parser-output"><p>Turn off power before repairs.</p><p id="steps">Keep all warnings.</p><a href="Pipe%3F">Pipe</a><a href="File:photo.png"><img src="photo.png" onerror="bad()"></a><img src="https://tracker.invalid/x"><a href="https://creativecommons.org/licenses/by-sa/3.0/">License</a><script>bad()</script><a href="Category:Solar">Solar category</a></div></div></body></html>'''))
            creator.add_item(FixtureItem('Pipe?', 'Pipe repair', '<html lang="en"><body><p>Preserve punctuation identity.</p></body></html>'))
            creator.add_item(FixtureItem('Pipe', 'Other pipe', '<html lang="en"><body><p>Different entry.</p></body></html>'))
            creator.add_item(FixtureItem('Category:Solar', 'Solar', '<p>Category page</p>'))
            creator.add_item(FixtureItem('German', 'German', '<html lang="de"><body><p>Deutsch</p></body></html>'))
            creator.add_item(FixtureItem('photo.png', 'Photo', b'small image fixture', 'image/png'))
            creator.add_item(FixtureItem('unsafe.svg', 'Unsafe', b'<svg><script>bad()</script></svg>', 'image/svg+xml'))
            creator.add_redirection('Solar_heater', 'Solar heater', 'Solar_water', {Hint.FRONT_ARTICLE: True})
        self.config = dict(ingest.configuration(), bytes=self.archive.stat().st_size,
                           sha256=digest_file(self.archive))
        self.data = self.root / 'data'

    def test_zim_preserves_licensed_text_media_aliases_and_source_categories(self):
        result = ingest.build(self.data, self.archive, self.config)
        self.assertEqual(result['indexedItems'], 3)
        self.assertEqual(result['uniqueMediaFiles'], 1)
        self.assertTrue(result['zimChecksumVerified'])
        self.assertEqual(result['excludedPages']['explicit-non-English'], 1)
        index_body = (self.data / 'runtime/index.json').read_bytes()
        self.assertEqual(gzip.decompress((self.data / 'runtime/index.json.gz').read_bytes()), index_body)
        index = json.loads(index_body)
        own = next(row for row in index['items'] if row[1] == 'Solar water heater')
        self.assertEqual(own[2], 'handyman')
        detail = json.loads((self.data / 'runtime/guides' / (own[0] + '.json')).read_bytes())
        tree = html.fromstring(detail['html'])
        self.assertIn('Turn off power', tree.text_content())
        self.assertFalse(tree.xpath('.//script|.//*[@onerror]'))
        self.assertTrue(all(img.get('src').startswith('/media/') for img in tree.xpath('.//img')))
        self.assertEqual(detail['license']['name'], 'CC BY-SA 3.0')
        self.assertIn('Category:Solar', detail['sourceCategories'])
        self.assertEqual(tree.xpath('.//a')[0].get('href'), '/?guide=' + short_id('appropedia', 'Pipe?'))
        self.assertNotEqual(short_id('appropedia', 'Pipe'), short_id('appropedia', 'Pipe?'))
        self.assertIn('solar heater', '\n'.join(index['search']['groups']))
        with sqlite3.connect(self.data / 'catalog.sqlite') as db:
            self.assertEqual(db.execute('SELECT guide FROM guide_categories').fetchone()[0], 'Solar_water')
        self.assertIsNotNone(completed(self.data, ingest.key_for(self.config), self.config))

    def test_absolute_relative_and_encoded_links_are_safe(self):
        self.assertIn('Pipe?', ingest.references('Solar_water', 'https://www.appropedia.org/Pipe%3F'))
        self.assertIn('Percent%3F', ingest.references('X', 'Percent%253F'))
        self.assertEqual(ingest.external_url('X', 'javascript:bad()'), '')
        self.assertEqual(ingest.external_url('X', 'data:text/html,bad'), '')
        self.assertEqual(ingest.references('X', 'https://tracker.invalid/p'), [])
        self.assertEqual(ingest.source_url('Pipe?'), 'https://www.appropedia.org/Pipe%3F')

    def test_bad_checksum_and_normalization_failure_leave_completed_source(self):
        ingest.build(self.data, self.archive, self.config)
        original = (self.data / 'runtime/index.json').read_bytes()
        with patch.object(ingest, 'sanitize', side_effect=ValueError('parse failed')):
            with self.assertRaisesRegex(ValueError, 'parse failed'):
                ingest.build(self.data, self.archive, self.config)
        self.assertEqual((self.data / 'runtime/index.json').read_bytes(), original)
        with self.assertRaisesRegex(RuntimeError, 'SHA-256'):
            ingest.build(self.data, self.archive, dict(self.config, sha256='0' * 64))

    def test_completed_reuse_reads_no_zim_or_network(self):
        ingest.build(self.data, self.archive, self.config)
        with (patch.object(ingest, 'configuration', return_value=self.config),
              patch.object(ingest, 'download', side_effect=AssertionError('network')),
              patch.object(ingest, 'build', side_effect=AssertionError('corpus')),
              patch('sys.argv', ['ingest_appropedia', '--data-dir', str(self.data)])):
            ingest.main()

    def test_body_filter_ignores_brand_titles_and_quarantines_foreign_prose(self):
        english = html.fromstring('<div><h1>Düsseldorf Acer Lenovo Samsung</h1><p>This guide explains how to repair your computer. Disconnect the battery before you remove the cover and keep all of the screws in a safe place.</p></div>')
        self.assertIsNone(ingest.foreign_body(english))
        self.assertIsNone(ingest.foreign_body(html.fromstring('<div><h1>Düsseldorf</h1><p>Lenovo Acer Samsung</p></div>')))
        spanish = html.fromstring('<html lang="en"><body><p>Esta página explica cómo hacer un acabado casero como una laca transparente. Primero hay que preparar los materiales y después seguir los pasos con mucho cuidado.</p></body></html>')
        self.assertEqual(ingest.foreign_body(spanish)[0], 'es')

    def test_false_english_header_does_not_publish_foreign_body(self):
        creator = Creator(self.root / 'mixed.zim').config_indexing(False, 'eng').config_nbworkers(1)
        with creator:
            creator.set_mainpath('English')
            creator.add_item(FixtureItem('English', 'English', '<html lang="en"><body><p>This is a useful repair article. Disconnect the battery before replacing the damaged component and preserve the source safety warnings.</p></body></html>'))
            creator.add_item(FixtureItem('Spanish', 'Spanish', '<html lang="en"><body><p>Esta página explica cómo hacer un acabado casero como una laca transparente. Primero hay que preparar los materiales y después seguir los pasos con mucho cuidado.</p></body></html>'))
        archive = self.root / 'mixed.zim'
        pin = dict(self.config, bytes=archive.stat().st_size, sha256=digest_file(archive))
        result = ingest.build(self.data, archive, pin)
        self.assertEqual(result['indexedItems'], 1)
        self.assertEqual(result['excludedPages']['detected-es'], 1)
        with sqlite3.connect(self.data / 'catalog.sqlite') as db:
            self.assertEqual(db.execute('SELECT path,language FROM language_quarantine').fetchall(), [('Spanish', 'es')])


if __name__ == '__main__':
    unittest.main()
