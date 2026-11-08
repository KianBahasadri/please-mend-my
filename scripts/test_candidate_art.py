"""Pinned candidates publish without semantic quality thresholds or live AI."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import prepare_candidate_art as candidates
import build_archetypes as archetypes


class CandidateArtTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / 'sources'; self.cache.mkdir()
        self.output = self.root / 'prepared'
        self.body = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect x="0" y="0" width="50" height="50"/></svg>'
        self.sha = hashlib.sha256(self.body).hexdigest()
        self.original = self.cache / (self.sha + '.original.svg'); self.original.write_bytes(self.body)
        self.source = {'id': 'candidate-one', 'title': 'Rough source', 'author': 'Original artist',
            'source': 'https://commons.wikimedia.org/wiki/File:Example.svg',
            'download': 'https://upload.wikimedia.org/wikipedia/commons/a/ab/Example.svg',
            'license': 'CC0 1.0', 'licenseUrl': 'https://creativecommons.org/publicdomain/zero/1.0/',
            'sha256': self.sha, 'bytes': len(self.body)}
        self.part = {'id': 'surface', 'label': 'Surface', 'query': 'surface', 'aliases': ['surface']}
        self.choice = {'source': self.source, 'parts': [self.part], 'mapping': [],
                       'label': 'Object', 'view': 'exterior', 'phase': 'primary', 'batch': 'diagram-batch-10'}
        self.config = self.root / 'config.json'

    def prepare(self, choice=None):
        self.config.write_text(json.dumps({'version': 1, 'subjects': [{'id': 'object', 'candidates': [choice or self.choice]}]}))
        return candidates.prepare(self.config, self.output, source_caches=[self.cache], offline=True)

    def test_single_shape_context_candidate_is_portable_cached_and_does_not_need_approval(self):
        with patch.object(candidates, 'request_bytes', side_effect=AssertionError('No upstream calls')):
            first = self.prepare()
            second = candidates.prepare(self.config, self.output, offline=True)
        self.assertEqual(first, second)
        self.assertEqual(len(first['artifacts']), 1)
        self.assertFalse(first['qualityApprovalRequired'])
        record = first['artifacts'][0]
        art = json.loads((self.output / 'art' / (record['sha256'] + '.json')).read_bytes())
        self.assertEqual(art['parts'], {})
        self.assertEqual(len(art['context']), 1)
        self.assertIn('Adapted', art['attribution']['changes'])
        self.assertNotIn(str(self.root), self.config.read_text())

    def test_low_confidence_and_one_part_are_not_visual_publication_gates(self):
        choice = deepcopy(self.choice)
        choice['mapping'] = [{'node': '0.0', 'part': 'surface', 'confidence': 'low'}]
        result = self.prepare(choice)
        record = result['artifacts'][0]
        art = json.loads((self.output / 'art' / (record['sha256'] + '.json')).read_bytes())
        self.assertEqual(set(art['parts']), {'surface'})
        applied = {'selectedSource': self.source['id'], 'groupMap': choice['mapping']}
        self.assertEqual(record['mappingSha256'], hashlib.sha256(candidates.canonical(applied)).hexdigest())
        self.assertEqual(record['attemptedMappingSha256'], record['mappingSha256'])
        self.assertFalse(record['contextFallback'])
        invalid = deepcopy(choice); invalid['mapping'][0]['node'] = 'missing'
        result = self.prepare(invalid)
        record = result['artifacts'][0]
        art = json.loads((self.output / 'art' / (record['sha256'] + '.json')).read_bytes())
        self.assertEqual(art['parts'], {}, 'broken mapping falls back to actual context geometry')
        attempted = {'selectedSource': self.source['id'], 'groupMap': invalid['mapping']}
        applied = {'selectedSource': self.source['id'], 'groupMap': []}
        self.assertEqual(record['attemptedMappingSha256'], hashlib.sha256(candidates.canonical(attempted)).hexdigest())
        self.assertEqual(record['mappingSha256'], hashlib.sha256(candidates.canonical(applied)).hexdigest())
        self.assertNotEqual(record['attemptedMappingSha256'], record['mappingSha256'])
        self.assertTrue(record['contextFallback'])

    def test_source_tampering_and_active_xml_still_reject(self):
        self.original.write_bytes(self.body + b'changed')
        result = self.prepare()
        self.assertEqual(result['artifacts'], [])
        self.assertIn('SHA pin', result['rejected'][0]['reason'])
        active = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><script>alert(1)</script><rect width="5" height="5"/></svg>'
        choice = deepcopy(self.choice); sha = hashlib.sha256(active).hexdigest()
        choice['source']['sha256'] = sha
        (self.cache / (sha + '.original.svg')).write_bytes(active)
        result = self.prepare(choice)
        self.assertEqual(result['artifacts'], [])
        self.assertIn('active', result['rejected'][0]['reason'])

    def test_registry_attaches_by_exact_noun_and_preserves_reviewed_art_priority(self):
        result = self.prepare()
        rows = [{'id': 'object', 'label': 'Object', 'parts': [], 'artUrl': None}]
        bodies = archetypes.import_candidate_art(rows, self.output)
        self.assertEqual(len(bodies), 1)
        self.assertIn('/api/art/', rows[0]['artUrl'])
        self.assertEqual(rows[0]['parts'], [self.part])
        self.assertEqual(rows[0]['artStatus'], 'candidate')
        self.assertFalse(rows[0]['qualityApprovalRequired'])
        reviewed = [{'id': 'object', 'artUrl': '/api/art/reviewed.json', 'parts': ['kept']}]
        self.assertEqual(archetypes.import_candidate_art(reviewed, self.output), {})
        self.assertEqual(reviewed[0]['parts'], ['kept'])
        self.assertEqual(archetypes.import_candidate_art([{'id': 'other', 'artUrl': None}], self.output), {})
        record = result['artifacts'][0]
        (self.output / 'art' / (record['sha256'] + '.json')).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            archetypes.import_candidate_art([{'id': 'object', 'artUrl': None}], self.output)

    def test_batch_export_includes_unmapped_sources_and_omits_absolute_paths_and_geometry(self):
        batch = self.root / 'diagram-batch-10'; batch.mkdir()
        def save(name, value): (batch / name).write_text(json.dumps(value))
        save('taxonomy.json', {'archetypes': [{'id': 'object', 'label': 'Object', 'parts': [self.part]}]})
        save('discovery-primary.json', {'views': {'object/exterior': {'candidates': [{**self.source, 'file': '/private/source.svg'}]}}})
        metadata = candidates.export_batches([batch], self.config)
        self.assertEqual(metadata['subjects'], 1)
        self.assertEqual(metadata['candidates'], 1)
        text = self.config.read_text()
        self.assertNotIn('/private', text)
        self.assertNotIn('viewBox', text)
        exported = json.loads(text)
        self.assertEqual(exported['subjects'][0]['candidates'][0]['mapping'], [])


if __name__ == '__main__':
    unittest.main()
