"""CSV ingestion uses tiny quoted/multiline fixtures, never the public corpus."""
import csv
import gzip
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import ingest_common as common
import ingest_wikihow as ingest
import merge_catalogs


class WikiHowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'fixture.csv'
        self.data = self.root / 'data'
        with self.source.open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['headline', 'title', 'text'])
            writer.writerow(['Fix the pipe, safely.', 'How to Fix a Leaking Pipe1', 'Turn off the water.\n\nDo not work on live pipes. <script>bad()</script>'])
            writer.writerow(['Use settings.', 'How to Repair Windows 101', 'Preserve exact numbers.'])
            writer.writerow(['', 'Empty', ''])
        self.config = dict(ingest.configuration(), bytes=self.source.stat().st_size,
                           sha256=common.digest_file(self.source))

    def test_complete_stream_preserves_raw_content_and_provenance(self):
        original = self.source.read_bytes()
        result = ingest.build(self.data, self.source, self.config)
        self.assertEqual(result['sourceRows'], 3)
        self.assertEqual(result['indexedItems'], 2)
        self.assertEqual(result['excludedEmptyRows'], 1)
        self.assertEqual(result['uniqueMediaFiles'], 0)
        self.assertTrue(result['datasetVerified'])
        body = (self.data / 'runtime/index.json').read_bytes()
        self.assertEqual(gzip.decompress((self.data / 'runtime/index.json.gz').read_bytes()), body)
        index = json.loads(body)
        first = next(row for row in index['items'] if 'Pipe' in row[1])
        self.assertEqual(first[2], 'handyman')
        guide = json.loads((self.data / 'runtime/guides' / (first[0] + '.json')).read_bytes())
        self.assertIn('&lt;script&gt;', guide['html'])
        self.assertIn('Do not work', guide['html'])
        self.assertEqual(guide['sourceHeadline'], 'Fix the pipe, safely.')
        self.assertEqual(guide['sourceRow'], 1)
        self.assertEqual(guide['sourceUrl'], self.config['url'])
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual((self.data / 'runtime').stat().st_mode & 0o777, 0o755)
        with sqlite3.connect(self.data / 'catalog.sqlite') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM items').fetchone()[0], 2)
        self.assertIsNotNone(common.completed(self.data, common.ingestion_key(ingest.__file__, self.config), self.config))

    def test_numeric_title_not_corrupted_and_deterministic_ids(self):
        row = {'title': 'How to Fix Windows 101', 'headline': 'Restart', 'text': 'Original instructions.'}
        first = ingest.record_for(1, row, self.config)
        self.assertEqual(first['title'], row['title'])
        self.assertEqual(first['id'], ingest.record_for(1, row, self.config)['id'])
        self.assertNotEqual(first['id'], ingest.record_for(2, row, self.config)['id'])

    def test_invalid_rows_or_checksum_never_replace_previous(self):
        ingest.build(self.data, self.source, self.config)
        before = (self.data / 'runtime/index.json').read_bytes()
        self.source.write_text('unexpected,header\na,b\n')
        config = dict(self.config, bytes=self.source.stat().st_size, sha256=common.digest_file(self.source))
        with self.assertRaisesRegex(ValueError, 'header'):
            ingest.build(self.data, self.source, config)
        self.assertEqual((self.data / 'runtime/index.json').read_bytes(), before)
        with self.assertRaisesRegex(RuntimeError, 'absent/incomplete|SHA-256'):
            ingest.build(self.data, self.source, self.config)

    def test_malformed_rows_are_quarantined_without_losing_good_rows(self):
        with self.source.open('a') as stream:
            stream.write('broken,row\n')
        config = dict(self.config, bytes=self.source.stat().st_size, sha256=common.digest_file(self.source))
        result = ingest.build(self.data, self.source, config)
        self.assertEqual(result['sourceRows'], 4)
        self.assertEqual(result['indexedItems'], 2)
        self.assertEqual(result['excludedMalformedRows'], 1)
        quarantine = json.loads((self.data / 'quarantine.jsonl').read_text())
        self.assertEqual(quarantine['sourceRow'], 4)
        self.assertEqual(quarantine['fields']['headline'], 'broken')
        self.assertIsNone(quarantine['fields']['text'])
        self.assertFalse((self.data / 'runtime/quarantine.jsonl').exists())

    def test_abandoned_private_stage_is_replaced_without_touching_runtime(self):
        ingest.build(self.data, self.source, self.config)
        previous = (self.data / 'runtime/index.json').read_bytes()
        abandoned = self.data / '.runtime-interrupted'
        abandoned.mkdir()
        (abandoned / 'partial-guide').write_text('incomplete')
        with common.CatalogWriter(self.data, self.config, 'new-test-key'):
            self.assertFalse(abandoned.exists())
            self.assertEqual((self.data / 'runtime/index.json').read_bytes(), previous)

    def test_schema_and_missing_pins_fail_closed(self):
        self.source.write_text('title,text\nOther,words\n')
        with self.assertRaisesRegex(ValueError, 'header'):
            list(ingest.rows(self.source))
        with self.assertRaises(FileNotFoundError):
            common.download(self.data, self.config, build_only=True)

    def test_empty_media_csv_merges_with_provenance(self):
        ingest.build(self.data, self.source, self.config)
        output = merge_catalogs.merge_catalogs([self.data / 'runtime'], self.root / 'merged')
        manifest = json.loads((output / 'manifest.json').read_bytes())
        self.assertEqual(manifest['uniqueMediaFiles'], 0)
        self.assertTrue(manifest['sources'][0]['datasetVerified'])
        self.assertEqual(manifest['sources'][0]['sourceFormat'], 'csv')
        self.assertEqual(manifest['sources'][0]['sourcePin']['licenseNotice'], self.config['licenseNotice'])

    def test_completed_reuse_does_not_touch_corpus_or_network(self):
        ingest.build(self.data, self.source, self.config)
        with (patch.object(ingest, 'configuration', return_value=self.config),
              patch.object(ingest, 'download', side_effect=AssertionError('unexpected download')),
              patch.object(ingest, 'build', side_effect=AssertionError('unexpected corpus read')),
              patch('sys.argv', ['ingest_wikihow', '--data-dir', str(self.data)])):
            ingest.main()

    def test_changed_official_metalink_cannot_start_download(self):
        raw = self.data / 'raw'
        raw.mkdir(parents=True)
        pin = dict(self.config, metalink='https://example.org/fixture.meta4')
        metadata = raw / (pin['filename'] + '.meta4')
        metadata.write_text('<metalink xmlns="urn:ietf:params:xml:ns:metalink"><file name="wikihowAll.csv"><size>123</size><hash type="sha-256">wrong</hash></file></metalink>')
        with patch.object(common.subprocess, 'run', side_effect=AssertionError('download must not start')):
            with self.assertRaisesRegex(ValueError, 'Metalink differs'):
                common.download(self.data, pin)


if __name__ == '__main__':
    unittest.main()
