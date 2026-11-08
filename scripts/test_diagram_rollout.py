"""All-physical selection, cache replay, disk safety and resumable SVG windows."""
from argparse import Namespace
from copy import deepcopy
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import diagram_batch as batch
import diagram_rollout as rollout


class DiagramRolloutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def source(self):
        body = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60"><rect x="5" y="5" width="90" height="50"/></svg>'
        path = self.root / 'source.svg'
        path.write_bytes(body)
        return {'id': 'source', 'title': 'Fixture object', 'file': str(path),
            'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body),
            'author': 'Fixture author', 'source': 'https://commons.wikimedia.org/wiki/File:Fixture.svg',
            'download': 'https://upload.wikimedia.org/fixture.svg',
            'license': 'CC0 1.0', 'licenseUrl': 'https://creativecommons.org/publicdomain/zero/1.0/'}

    def subject(self, identifier='one'):
        return {'id': identifier, 'label': 'fixture', 'definition': 'Physical fixture',
            'parts': [{'id': word, 'label': word, 'query': word, 'aliases': [],
                'kind': 'physical', 'viewId': 'outside'} for word in ('leg', 'lid', 'handle')],
            'views': [{'id': 'outside', 'label': 'Outside', 'svgQueries': ['fixture diagram']}],
            'status': 'taxonomy-ready'}

    def test_all_physical_selection_does_not_require_frequency_coverage_or_seed_parts(self):
        database = self.root / 'nouns.sqlite'
        with sqlite3.connect(database) as db:
            db.executescript('''CREATE TABLE nouns(id,label,domain,definition,frequency,guide_count);
                CREATE TABLE models(noun_id,guide_count);
                CREATE TABLE noun_edges(child_id,relation);
                CREATE TABLE guide_nouns(guide_id,noun_id);
                CREATE TABLE items(id,title,device,kind);''')
            db.executemany('INSERT INTO nouns VALUES(?,?,?,?,?,?)', [
                ('one', 'rare fixture', 'artifact', 'An object.', .1, 1),
                ('two', 'rare tendon', 'body', 'A body structure.', .1, 1),
                ('three', 'not an object', 'group', 'A group.', 8, 99)])
        runtime = self.root / 'runtime'
        batch.ai.save(runtime / 'archetypes.json', {'revision': 'test', 'archetypes': [
            {'id': identifier, 'aliases': [identifier], 'parts': []} for identifier in ('one', 'two', 'three')]})
        selected = batch.prepare_selection(runtime, database, self.root / 'work', None, all_physical=True)
        self.assertEqual({row['id'] for row in selected['archetypes']}, {'one', 'two'})
        self.assertTrue(all(row['baselineParts'] == [] for row in selected['archetypes']))
        self.assertFalse((runtime / 'guides').exists())

    def test_cache_import_keeps_originals_and_exact_paid_response_keys(self):
        old, new = self.root / 'old', self.root / 'new'
        original = old / 'cache/taxonomy/pin.json'
        batch.ai.save(original, {'cacheKey': 'pin', 'content': {'fixture': True}})
        self.assertEqual(rollout.import_cache(new, [old]), 1)
        self.assertEqual((new / 'cache/taxonomy/pin.json').read_bytes(), original.read_bytes())
        self.assertEqual(rollout.import_cache(new, [old]), 0)
        self.assertTrue(original.is_file())

    def test_context_only_safe_source_survives_missing_or_invalid_part_map(self):
        subject, source = self.subject(), self.source()
        taxonomy = {'archetypes': [subject]}
        discovery = {'views': {'one/outside': {'candidates': [source]}}}
        for mapping in ({}, {'selectedSource': 'source', 'groupMap': [
                    {'node': 'not-a-real-path', 'part': 'leg', 'confidence': 'high'}]}):
            receipt = {'entries': [{'id': 'one/outside', 'content': mapping}]}
            result = rollout.compile_available(taxonomy, discovery, {}, receipt, self.root / 'work')
            view = result['archetypes'][0]['views'][0]
            self.assertTrue(view['contextOnly'])
            self.assertFalse(view['qualityApprovalRequired'])
            art = batch.read(view['artPath'])
            self.assertEqual(art['parts'], {})
            self.assertEqual(len(art['context']), 1)
            self.assertEqual(art['attribution']['author'], 'Fixture author')

    def test_disk_reserve_stops_before_new_work(self):
        with patch.object(rollout.shutil, 'disk_usage', return_value=Namespace(free=900_000_000)):
            with self.assertRaisesRegex(RuntimeError, 'Disk reserve reached'):
                rollout.disk_gate(self.root, 1)

    def test_sparse_context_views_do_not_inherit_old_quality_part_minimum(self):
        subject = self.subject()
        subject['parts'] = subject['parts'][:1]
        self.assertEqual(batch.view_targets({'archetypes': [subject]}), [])
        targets = batch.view_targets({'allowSparseViews': True, 'archetypes': [subject]})
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]['minimumDistinctPhysicalParts'], 1)

    def test_preview_cleanup_cannot_remove_pins_paid_objects_or_compiled_art(self):
        work = self.root / 'window'
        for name in ('sources/original.svg', 'cache/taxonomy/paid.json',
                     'compiled/one/outside/art.json', 'digests/one/marks/contact.png'):
            path = work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture')
        rollout.discard_previews(work)
        self.assertFalse((work / 'digests').exists())
        for name in ('sources/original.svg', 'cache/taxonomy/paid.json', 'compiled/one/outside/art.json'):
            self.assertTrue((work / name).is_file())

    def test_offline_windows_resume_without_repeating_discovery_and_preserve_source_pins(self):
        source = self.source()
        selection = {'count': 2, 'archetypes': [self.subject('one'), self.subject('two')]}
        taxonomy = {'archetypes': selection['archetypes']}
        args = Namespace(work_dir=self.root / 'work', runtime_dir=self.root / 'runtime',
            database=self.root / 'nouns.sqlite', reuse_batch=[], env_file=self.root / '.env',
            offline=True, min_free_gib=1, chunk_size=1, concurrency=2, model='fixture',
            geometry_workers=2, discovery_workers=2, max_windows=1)
        calls = []
        def discover(current, window, *, phase, **_):
            calls.append((current['archetypes'][0]['id'], phase))
            views = {batch.target_key(target): {'candidates': [source]}
                     for target in batch.view_targets(current, phase)}
            report = {'views': views}
            batch.ai.save(window / ('discovery-' + phase + '.json'), report)
            return report
        def digests(current, discovery, window, *, phase, **_):
            batch.ai.atomic(window / 'digests/scratch.png', b'regenerable')
            return {'views': {}}
        def mapping(current, digests, window, *, phase, **_):
            report = {'entries': []}
            batch.ai.save(window / ('mapping-' + phase + '-receipt.json'), report)
            return report
        with patch.object(batch, 'prepare_selection', return_value=selection), \
             patch.object(batch, 'run_taxonomy', return_value=taxonomy), \
             patch.object(batch, 'run_discovery', side_effect=discover), \
             patch.object(batch, 'run_digests', side_effect=digests), \
             patch.object(batch, 'run_mapping', side_effect=mapping), \
             patch.object(rollout.shutil, 'disk_usage', return_value=Namespace(free=10 * 1024**3)):
            first = rollout.run(args)
            self.assertEqual(first['completedSubjects'], 1)
            self.assertFalse(first['complete'])
            args.max_windows = 0
            second = rollout.run(args)
        self.assertEqual(second['completedSubjects'], 2)
        self.assertTrue(second['complete'])
        self.assertEqual(second['subjectsWithArt'], 2)
        self.assertEqual(calls.count(('one', 'primary')), 1)
        self.assertEqual(calls.count(('two', 'primary')), 1)
        self.assertEqual(second['newAcceptedResponses'], 0)
        self.assertTrue(Path(source['file']).is_file())
        self.assertEqual(len(batch.read(args.work_dir / 'candidate-sources.json')['subjects']), 2)


if __name__ == '__main__':
    unittest.main()
