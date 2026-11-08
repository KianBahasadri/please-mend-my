"""Offline regression checks for generic bounded SVG batch orchestration."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import diagram_batch as batch


def content():
    return {'aliases': ['wheelbarrow'], 'reason': 'Physical object in catalog.',
        'parts': [{'id': value, 'label': value.title(), 'query': value, 'aliases': [],
                   'kind': 'physical', 'viewId': 'exterior'} for value in ('wheel', 'handle', 'tray')]
                + [{'id': 'noise', 'label': 'Noise', 'query': 'noise', 'aliases': [],
                    'kind': 'symptom', 'viewId': ''}],
        'views': [{'id': 'exterior', 'label': 'Exterior', 'svgQueries': ['wheelbarrow diagram']}]}


def subject():
    return {'id': 'noun-barrow', 'label': 'wheelbarrow', 'definition': 'A small hand vehicle.',
        'domain': 'artifact', 'aliases': ['wheelbarrow'], 'baselineParts': [],
        'catalogMatches': 5, 'examples': [{'title': 'Wheelbarrow wheel repair', 'device': ''}],
        'parts': content()['parts'], 'views': content()['views'], 'status': 'taxonomy-ready'}


class DiagramBatchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_selection_is_generic_physical_distinct_and_metadata_only(self):
        database = self.root / 'nouns.sqlite'
        with sqlite3.connect(database) as connection:
            connection.executescript('''
                CREATE TABLE nouns(id TEXT,label TEXT,domain TEXT,definition TEXT,frequency REAL,guide_count INTEGER);
                CREATE TABLE models(name TEXT,noun_id TEXT,guide_count INTEGER);
                CREATE TABLE noun_edges(parent_id TEXT,child_id TEXT,relation TEXT);
                CREATE TABLE guide_nouns(guide_id TEXT,noun_id TEXT);
                CREATE TABLE items(id TEXT,title TEXT,kind TEXT,device TEXT);
            ''')
            connection.executemany('INSERT INTO nouns VALUES(?,?,?,?,?,?)', [
                ('one', 'wheelbarrow', 'artifact', 'A small hand vehicle.', 3, 10),
                ('two', 'barrow', 'artifact', 'A hand cart.', 3, 5),
                ('three', 'community', 'group', 'People living together.', 6, 50),
                ('four', 'elbow', 'body', 'Joint between upper and lower arm.', 4, 20)])
            connection.execute("INSERT INTO models VALUES('Garden Cart X1','one',10)")
            for identifier in ('one', 'two', 'three', 'four'):
                connection.execute('INSERT INTO items VALUES(?,?,?,?)', (identifier, identifier+' repair', 'guide', ''))
                connection.execute('INSERT INTO guide_nouns VALUES(?,?)', (identifier, identifier))
        runtime = self.root / 'runtime'; runtime.mkdir()
        rows = [{'id': value, 'aliases': aliases, 'parts': [{'id': 'a'}, {'id': 'b'}]}
                for value, aliases in [('one', ['wheelbarrow', 'barrow']), ('two', ['barrow']),
                                      ('three', ['community']), ('four', ['elbow'])]]
        batch.ai.save(runtime/'archetypes.json', {'revision': 'fixture', 'archetypes': rows})
        result = batch.prepare_selection(runtime, database, self.root/'work', 2)
        self.assertEqual({row['id'] for row in result['archetypes']}, {'one', 'four'})
        self.assertEqual(next(row for row in result['archetypes'] if row['id']=='one')['examples'][0]['id'], 'one')
        self.assertFalse((runtime/'guides').exists())
        self.assertEqual(result, batch.prepare_selection(runtime, database, self.root/'work', 2))

    def test_taxonomy_rejects_duplicate_ids_bad_views_and_symptom_geometry(self):
        batch.validate_taxonomy(content())
        for mutate in (lambda value: value['parts'].append(deepcopy(value['parts'][0])),
                       lambda value: value['parts'][0].update(viewId='missing'),
                       lambda value: value['parts'][-1].update(viewId='exterior'),
                       lambda value: value['parts'][0].update(id='../unsafe')):
            value = content(); mutate(value)
            with self.assertRaises(ValueError):
                batch.validate_taxonomy(value)

    def test_offline_normalization_recovers_apostrophes_orphan_views_and_keeps_raw(self):
        value = content()
        value['parts'][-1].update(id="won't-start")
        value['parts'][0]['viewId'] = 'missing-view'
        clean, notes = batch.normalize_taxonomy(value)
        self.assertEqual(clean['parts'][-1]['id'], 'wont-start')
        self.assertEqual(clean['parts'][0]['viewId'], '')
        self.assertEqual(value['parts'][0]['viewId'], 'missing-view')
        self.assertTrue(any(note.get('heldListOnly') for note in notes))
        value['parts'][0]['id'] = 'wont-start'
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            batch.normalize_taxonomy(value)

    def test_taxonomy_complete_response_is_cached_then_replayed_without_network(self):
        selection = {'revision': 'fixture', 'archetypes': [subject()]}
        calls = []
        def transport(payload, key, schema):
            calls.append(payload)
            return content(), {'cost': .001, 'total_tokens': 20}
        online = batch.run_taxonomy(selection, self.root, api_key='fake-key', concurrency=2, transport=transport)
        self.assertEqual(online['archetypes'][0]['status'], 'taxonomy-ready')
        self.assertEqual(len(calls), 1)
        with patch.object(batch.ai, 'completion', side_effect=AssertionError('network forbidden')):
            offline = batch.run_taxonomy(selection, self.root, api_key=None, concurrency=2, transport=transport)
        self.assertEqual(online, offline)
        self.assertEqual(len(calls), 1)
        self.assertEqual(batch.read(self.root/'taxonomy-receipt.json')['cacheHits'], 1)

    def test_mismatch_empty_taxonomy_is_list_only_without_discovery(self):
        value = subject(); value.update(parts=[], views=[])
        self.assertEqual(batch.view_targets({'archetypes': [value]}), [])

    def test_secondary_requires_low_coverage_two_missing_parts_and_max_three_views(self):
        value = subject()
        value['views'] += [{'id': 'inside', 'label': 'Inside', 'svgQueries': ['inside barrow']}]
        value['parts'] += [{'id': part, 'label': part, 'query': part, 'aliases': [],
            'kind': 'physical', 'viewId': 'inside'} for part in ('axle', 'bearing')]
        taxonomy = {'archetypes': [value]}
        prior = {'archetypes': [{'id': value['id'], 'views': [
            {'status': 'needs-visual-review', 'mappedParts': ['wheel', 'handle', 'tray']}]}]}
        self.assertEqual([target['viewId'] for target in batch.view_targets(taxonomy, 'secondary', prior)], ['inside'])
        prior['archetypes'][0]['views'][0]['mappedParts'] += ['axle']
        self.assertEqual(batch.view_targets(taxonomy, 'secondary', prior), [])

    def test_sparse_exterior_does_not_suppress_first_eligible_internal_primary(self):
        value = subject()
        value['views'].insert(0, {'id': 'small-exterior', 'label': 'Small Exterior', 'svgQueries': []})
        value['parts'] += [{'id': name, 'label': name, 'query': name, 'aliases': [],
            'kind': 'physical', 'viewId': 'small-exterior'} for name in ('case', 'lid')]
        primary = batch.view_targets({'archetypes': [value]})
        self.assertEqual([target['viewId'] for target in primary], ['exterior'])
        secondary = batch.view_targets({'archetypes': [value]}, 'secondary')
        self.assertEqual([target['viewId'] for target in secondary], ['small-exterior'])

    def test_discovery_errors_are_counted_not_a_crash_or_active_release_mutation(self):
        def broken(*args, **kwargs):
            raise OSError('temporary upstream failure')
        report = batch.run_discovery({'archetypes': [subject()]}, self.root, workers=2, discover=broken)
        value = report['views']['noun-barrow/exterior']
        self.assertEqual(value['candidates'], [])
        self.assertIn('temporary upstream', value['errors'][0])
        self.assertFalse((self.root/'archetypes.json').exists())

    def test_digest_rejects_source_pin_before_geometry_or_render(self):
        source = self.root/'source.svg'; source.write_text('<svg/>')
        target = subject(); target['viewId'] = 'exterior'
        with patch.object(batch, 'structural_digest', side_effect=AssertionError('no geometry')):
            with self.assertRaisesRegex(ValueError, 'Pinned source'):
                batch.digest_candidate(target, {'id': 'candidate', 'file': str(source), 'sha256': 'wrong'}, self.root)

    def test_mapping_schema_and_payload_allow_only_actual_physical_view_parts(self):
        target = subject()
        taxonomy = {'archetypes': [target]}
        source = {'id': 'source', 'title': 'Wheelbarrow diagram', 'sha256': 'source-sha'}
        digest = {'version': 'v1', 'viewBox': [0,0,100,100], 'labels': [], 'nodes': [
            {'key': '0.1', 'tag': 'path', 'id': 'wheel', 'label': 'Wheel', 'shapeCount': 1, 'bbox': [0,0,.2,.2]}]}
        digests = {'views': {'noun-barrow/exterior': {'candidates': [{'source': source, 'digest': digest, 'images': []}]}}}
        calls = []
        def transport(payload, key, schema):
            calls.append(payload)
            enum = schema['properties']['groupMap']['items']['properties']['part']['enum']
            self.assertNotIn('noise', enum)
            return {'selectedSource': '', 'groupMap': [], 'reason': 'No reliable image in fixture'}, {}
        result = batch.run_mapping(taxonomy, digests, self.root, api_key='fake', transport=transport)
        self.assertEqual(result['apiCalls'], 1)
        prepared = json.loads(calls[0]['messages'][1]['content'])
        self.assertEqual(prepared['minimumDistinctPhysicalParts'], 3)
        self.assertNotIn('noise', [part['id'] for part in prepared['parts']])

    def test_compile_rejects_unknown_selected_source_and_never_publishes(self):
        target = subject(); target.update(viewId='exterior', viewLabel='Exterior', viewParts=target['parts'][:3])
        view = batch.compile_view(target, [], {'content': {'selectedSource': 'missing', 'reason': 'wrong'}}, self.root)
        self.assertEqual(view['status'], 'list-only')
        self.assertIsNone(view['artUrl'])
        self.assertIn('Compile gate', view['reason'])

    def test_final_report_keeps_no_usable_diagram_as_list_only(self):
        primary = {'archetypes': [{'id': 'noun-barrow', 'label': 'wheelbarrow', 'parts': content()['parts'],
                                 'views': [], 'status': 'list-only'}]}
        report = batch.final_report({'revision': 'test'}, primary, {}, self.root)
        self.assertEqual(report['count'], 1)
        self.assertEqual(report['staged'], 0)
        self.assertFalse(report['published'])
        self.assertIsNone(report['archetypes'][0]['defaultView'])

    def test_paid_accounting_survives_zero_call_replay_and_duplicate_receipt_copies(self):
        paid = {'stage': 'mapping', 'apiCalls': 1, 'entries': [
            {'cacheKey': 'mapping-key', 'apiCalls': 1, 'content': {}}]}
        replay = {'stage': 'mapping', 'apiCalls': 0, 'entries': [
            {'cacheKey': 'mapping-key', 'apiCalls': 0, 'content': {}}]}
        phase = self.root/'passes/primary'
        batch.ai.save(phase/'receipts/paid.json', paid)
        batch.ai.save(self.root/'mapping-primary-receipt.json', paid)
        batch.ai.save(phase/'mapping-receipt.json', replay)
        batch.ai.save(phase/'cache/mapping/response.json',
            {'cacheKey': 'mapping-key', 'usage': {'cost': .04, 'total_tokens': 100}})
        batch.ai.save(self.root/'cache/taxonomy/interrupted.json',
            {'cacheKey': 'taxonomy-key', 'usage': {'cost': .01, 'total_tokens': 20}})
        accounting = batch.lifetime_accounting(self.root)
        self.assertEqual(accounting['knownApiCalls'], 2)
        self.assertEqual(accounting['callsRecoveredFromInterruptedCaches'], 1)
        self.assertAlmostEqual(accounting['acceptedUsage']['cost'], .05)


if __name__ == '__main__':
    unittest.main()
