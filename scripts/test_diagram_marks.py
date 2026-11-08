"""Numbered visual evidence, aspect ratio and bounded candidate region gates."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import diagram_svg as svg
import review_diagram_batch as review


def marked_fixture(count=4):
    leaves, nodes = [], []
    for at in range(count):
        key = '0.' + str(at)
        box = [at / count, .2, .7 / count, .2]
        leaves.append({'key': key, 'bbox': box, 'ast': ['rect', {
            'x': str(at * 100 / count), 'y': '20', 'width': str(70 / count),
            'height': '20', 'fill': 'currentColor', 'data-source-id': key}]})
        nodes.append({'key': key, 'bbox': box, 'id': 'assembly-' + str(at), 'shapeCount': 1})
    return {'viewBox': [0, 0, 100, 100], 'nodes': nodes}, leaves


class SetOfMarkTests(unittest.TestCase):
    def test_candidate_numbers_are_stable_bounded_and_exclude_full_object_and_invisible_stroke(self):
        digest, leaves = marked_fixture(6)
        digest['nodes'].insert(0, {'key': '0', 'bbox': [0, .2, 1, .2], 'id': 'whole-object'})
        leaves.append({'key': '0.99', 'bbox': [.1, .2, .3, .001],
            'ast': ['path', {'fill': 'none', 'stroke': 'currentColor', 'stroke-width': '.001',
                              'd': 'M10 20 L40 20'}]})
        digest['nodes'].append({'key': '0.99', 'bbox': leaves[-1]['bbox'], 'id': 'thin-leader'})
        groups = review.candidate_groups(digest, leaves, limit=3)
        self.assertEqual(len(groups), 3)
        self.assertEqual([row['candidate'] for row in groups], [1, 2, 3])
        self.assertEqual(groups, review.candidate_groups(digest, leaves, limit=3))
        self.assertFalse({'0', '0.99'} & {row['node'] for row in groups})
        self.assertTrue(all(row['leafKeys'] == [row['node']] for row in groups))
        with self.assertRaises(ValueError):
            review.candidate_groups(digest, leaves, limit=100)

    def test_nested_duplicate_nodes_do_not_duplicate_regions(self):
        digest, leaves = marked_fixture()
        digest['nodes'] += [{'key': '0.0@wrapper', 'bbox': leaves[0]['bbox'], 'id': 'unused'}]
        # An SVG group containing only one shape has the same region as its leaf.
        leaves[0]['key'] = '0.0.1'
        digest['nodes'] += [{'key': '0.0.1', 'bbox': leaves[0]['bbox'], 'id': 'nested'}]
        groups = review.candidate_groups(digest, leaves)
        keys = [tuple(row['leafKeys']) for row in groups]
        self.assertEqual(len(keys), len(set(keys)))

    def test_paper_only_regions_are_context_but_visible_ink_borders_remain_candidates(self):
        digest, leaves = marked_fixture()
        leaves[0]['ast'][1]['fill'] = 'var(--paper)'
        leaves[1]['ast'][1].update(fill='var(--paper)', stroke='currentColor', **{'stroke-width': '1'})
        groups = review.candidate_groups(digest, leaves)
        self.assertNotIn('0.0', [group['node'] for group in groups])
        self.assertIn('0.1', [group['node'] for group in groups])
        self.assertEqual(len(groups), 3)

    def test_sheets_label_each_actual_region_and_paginate_without_raw_paths_in_manifest(self):
        digest, leaves = marked_fixture(16)
        with tempfile.TemporaryDirectory() as directory, patch.object(review.subprocess, 'run') as render:
            result = review.write_candidate_sheets(digest, leaves, directory, title='<untrusted & title>')
            self.assertEqual(len(result['groups']), 16)
            self.assertEqual(len(result['images']), 2)
            self.assertEqual(render.call_count, 2)
            captions = []
            for image in result['images']:
                root = ET.parse(image.with_suffix('.svg')).getroot()
                self.assertLessEqual(int(root.attrib['width']) * int(root.attrib['height']), 1_400_000)
                captions += [node.text for node in root.findall('{' + review.NS + '}text')
                             if node.text and node.text.startswith('Candidate ')]
                self.assertIn('<untrusted & title>', ''.join(root.itertext()))
                lit = [node for node in root.iter() if node.attrib.get('fill') == '#80ffcc']
                self.assertGreater(len(lit), 0)
            self.assertEqual(captions, ['Candidate ' + str(at) for at in range(1, 17)])

    def test_preview_viewport_keeps_true_portrait_and_landscape_aspect(self):
        self.assertEqual(svg.preview_size([0, 0, 100, 200]), (360, 720))
        self.assertEqual(svg.preview_size([0, 0, 300, 100]), (720, 240))
        for box in ([0, 0, 0, 1], [0, 0, float('nan'), 1], [0, 0, 1000, 1]):
            with self.assertRaises(ValueError):
                svg.preview_size(box)
        art = {'viewBox': '0 0 100 200', 'context': [], 'parts': {}}
        with tempfile.TemporaryDirectory() as directory, patch.object(svg.subprocess, 'run'):
            output = Path(directory) / 'portrait.png'
            svg.render_preview(art, output)
            root = ET.parse(output.with_suffix('.svg')).getroot()
            self.assertEqual((root.attrib['width'], root.attrib['height']), ('360', '720'))


if __name__ == '__main__':
    unittest.main()
