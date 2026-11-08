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

    def test_brand_and_os_dominance_rejects_contradictory_senses_but_not_real_computers(self):
        examples = [{'title': brand + ' Screen Replacement', 'device': brand + ' A123'}
                    for brand in ('HP Pavilion', 'Toshiba Satellite', 'Acer Iconia', 'ASUS', 'Lenovo')]
        for label, definition in [('pavilion', 'a large and sumptuous tent'),
                                  ('satellite', 'man-made equipment that orbits the earth'),
                                  ('tablet', 'a slab of stone suitable for bearing an inscription'),
                                  ('screen', 'a silvered surface on which pictures are projected'),
                                  ('display', 'a visual representation of something')]:
            with self.subTest(label=label):
                rejected = batch.sense_rejection({'label': label, 'definition': definition,
                                                 'domain': 'artifact'}, examples)
                self.assertEqual(rejected['observedFamily'], 'computing')
        windows = [{'title': 'How to activate Windows ' + str(n), 'device': ''} for n in range(4)]
        self.assertIsNotNone(batch.sense_rejection({'label': 'window', 'domain': 'artifact',
            'definition': 'a framework containing glass windowpanes built into a wall'}, windows))
        self.assertIsNone(batch.sense_rejection({'label': 'laptop', 'domain': 'artifact',
            'definition': 'a portable computer small enough to use in your lap'}, examples))
        self.assertIsNone(batch.sense_rejection({'label': 'pavilion', 'domain': 'artifact',
            'definition': 'a large sumptuous tent'}, [{'title': 'Repair tent canopy', 'device': ''}] * 4))
        self.assertIsNone(batch.sense_rejection({'label': 'pavilion', 'domain': 'artifact',
            'definition': 'a large sumptuous tent'}, examples[:2]), 'weak evidence stays for taxonomy review')

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

    def test_numbered_mapping_converts_only_pinned_regions_and_rejects_unknown_or_repeated_numbers(self):
        target = subject()
        source = {'id': 'source', 'title': 'Wheelbarrow', 'sha256': 'pin'}
        candidate = {'source': source, 'images': [], 'candidateGroups': [
            {'candidate': 1, 'node': '0.3', 'sourceId': 'wheel', 'sourceLabel': 'Wheel', 'shapeCount': 2}],
            'digest': {'version': 'fixture', 'viewBox': [0, 0, 100, 100], 'labels': []}}
        digests = {'views': {'noun-barrow/exterior': {'candidates': [candidate]}}}
        for numbers in ([1], [2], [1, 1]):
            with self.subTest(numbers=numbers):
                def transport(*_):
                    return {'selectedSource': 'source', 'reason': 'Lit wheel region', 'groupMap': [
                        {'candidate': number, 'part': 'wheel', 'confidence': 'high'} for number in numbers]}, {'cost': .01}
                run = self.root / ('mapping-' + '-'.join(map(str, numbers)))
                receipt = batch.run_mapping({'archetypes': [target]}, digests, run,
                    api_key='fake', transport=transport)
                entry = receipt['entries'][0]
                if numbers == [1]:
                    self.assertEqual(entry['content']['groupMap'][0]['node'], '0.3')
                    self.assertEqual(entry['markedContent']['groupMap'][0]['candidate'], 1)
                else:
                    self.assertNotIn('content', entry)
                    self.assertIn('numbered candidate', entry['error'])
                self.assertEqual(batch.lifetime_accounting(run)['knownApiCalls'], 1,
                                 'converted receipts must not double-count paid responses')

    def test_visual_verification_prunes_bad_parts_and_replays_frozen_evidence_offline(self):
        target = subject()
        target['parts'].insert(3, {'id': 'axle', 'label': 'Axle', 'query': 'axle', 'aliases': [],
                                   'kind': 'physical', 'viewId': 'exterior'})
        target.update(viewId='exterior', viewLabel='Exterior', viewParts=target['parts'][:4], previouslyMapped=[])
        leaves = [{'key': '0.' + str(i), 'bbox': [i * .22, .1, .15, .2],
            'ast': ['rect', {'x': str(i * 22), 'y': '10', 'width': '15', 'height': '20',
                             'fill': 'currentColor', 'data-source-id': '0.' + str(i)}]} for i in range(4)]
        leaves_path = self.root / 'leaves.json'; batch.ai.save(leaves_path, leaves)
        source = {'id': 'source', 'sha256': 'source-pin', 'title': 'Barrow', 'author': 'Artist',
                  'source': 'https://example.org/source', 'license': 'CC0', 'licenseUrl': 'https://example.org/license'}
        digest = {'viewBox': [0, 0, 100, 100], 'nodes': [{'key': leaf['key'], 'bbox': leaf['bbox']} for leaf in leaves]}
        candidate = {'source': source, 'digest': digest, 'leavesPath': str(leaves_path)}
        mapping = {'selectedSource': 'source', 'reason': 'Fixture physical parts', 'groupMap': [
            {'node': leaf['key'], 'part': part['id'], 'confidence': 'high'}
            for leaf, part in zip(leaves, target['viewParts'])]}
        receipt = {'entries': [{'id': 'noun-barrow/exterior', 'content': mapping}]}
        taxonomy = {'archetypes': [target]}
        digests = {'views': {'noun-barrow/exterior': {'candidates': [candidate]}}}
        calls = []
        def render_preview(art, output, *args, **kwargs):
            Path(output).write_bytes(batch.canonical(art))
        def review_sheet(art, output, **kwargs):
            Path(output).write_bytes(batch.canonical(art))
        def transport(payload, *_):
            calls.append(payload)
            return {'objectMatches': True, 'reason': 'Actual wheelbarrow visible', 'parts': [
                {'part': name, 'verdict': 'fail' if name == 'axle' else 'pass',
                 'reason': 'Wrong nearby region' if name == 'axle' else 'Actual assembly visible'}
                for name in ('wheel', 'handle', 'tray', 'axle')]}, {'cost': .02}
        with patch.object(batch, 'render_preview', side_effect=render_preview), \
             patch('review_diagram_batch.write_review_sheet', side_effect=review_sheet):
            compiled = batch.run_compile(taxonomy, digests, receipt, self.root)
            view = compiled['archetypes'][0]['views'][0]
            self.assertEqual(view['status'], 'needs-visual-review')
            result = batch.run_verification(taxonomy, digests, compiled, receipt, self.root,
                api_key='fake', transport=transport)
            view = result['archetypes'][0]['views'][0]
            self.assertEqual(view['status'], 'visual-pass')
            self.assertEqual(view['mappedParts'], ['handle', 'tray', 'wheel'])
            self.assertEqual(view['omittedGeometryParts'], ['axle'])
            self.assertFalse(view['visualVerification']['publicationApproval'])
            self.assertTrue(view['requiresHumanApproval'])
            art = batch.read(view['artPath'])
            self.assertIn(leaves[3]['ast'], art['context'], 'failed geometry remains unclickable context')
            self.assertEqual(set(art['parts']), {'wheel', 'handle', 'tray'})
            # Restore original pre-prune compile before checking the exact cached evidence.
            replay_compiled = batch.run_compile(taxonomy, digests, receipt, self.root)
            replay = batch.run_verification(taxonomy, digests, replay_compiled, receipt, self.root,
                api_key=None, transport=transport)
            self.assertEqual(replay['archetypes'][0]['views'][0]['status'], 'visual-pass')
            self.assertEqual(len(calls), 1)
        self.assertFalse((self.root / 'archetypes.json').exists())

    def test_visual_gate_rejects_wrong_object_and_fewer_than_three_passes(self):
        target = subject()
        taxonomy = {'archetypes': [target]}
        wanted = ['wheel', 'handle', 'tray']
        for case in ('wrong-object', 'two-passed', 'duplicate-part', 'missing-part'):
            with self.subTest(case=case):
                directory = self.root / case; directory.mkdir()
                image = directory / 'highlights.png'; image.write_bytes(b'compiled highlight evidence')
                art_path = directory / 'art.json'; art_path.write_bytes(b'compiled-art')
                compiled = {'archetypes': [{'id': 'noun-barrow', 'views': [{
                    'id': 'exterior', 'status': 'needs-visual-review', 'mappedParts': wanted[:],
                    'previewDir': str(directory), 'artPath': str(art_path), 'artSha256': 'art-pin',
                    'sourceSha256': 'source-pin', 'mappingSha256': 'mapping-pin'}]}]}
                checks = [{'part': name, 'verdict': 'fail' if case == 'two-passed' and name == 'tray' else 'pass',
                           'reason': 'Visible region review'} for name in wanted]
                if case == 'duplicate-part':
                    checks[-1]['part'] = 'wheel'
                if case == 'missing-part':
                    checks.pop()
                def transport(*_):
                    return {'objectMatches': case != 'wrong-object', 'parts': checks,
                            'reason': 'Fixture verification'}, {}
                with patch.object(batch, 'compile_view', side_effect=AssertionError('insufficient geometry cannot compile')):
                    result = batch.run_verification(taxonomy, {'views': {}}, compiled, {'entries': []}, directory,
                        api_key='fake', transport=transport)
                view = result['archetypes'][0]['views'][0]
                self.assertEqual(view['status'], 'list-only')
                self.assertEqual(view['mappedParts'], [])
                self.assertFalse(view['visualVerification']['publicationApproval'])

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
