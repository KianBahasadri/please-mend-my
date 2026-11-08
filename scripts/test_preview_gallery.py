"""A local gallery includes rejected evidence without exposing arbitrary files."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import prepare_diagram_preview as preview


class PreviewGalleryTests(unittest.TestCase):
    def test_rejected_candidates_are_visible_escaped_and_use_only_pinned_local_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch = root / 'diagram-batch-10'; batch.mkdir()
            output = root / 'gallery'
            def save(name, value):
                (batch / name).write_text(json.dumps(value))
            image = batch / 'original.png'
            body = preview.PNG + b'synthetic raster evidence'
            image.write_bytes(body)
            source = {'id': 'source-one', 'title': '<script>candidate</script>',
                      'source': 'https://example.org/source', 'author': 'A & B',
                      'license': 'CC BY', 'licenseUrl': 'https://example.org/license'}
            save('selection.json', {'archetypes': [{'id': 'noun', 'label': '<noun>', 'definition': 'Physical object'}]})
            save('discovery-primary.json', {'views': {'noun/exterior': {'candidates': [source]}}})
            save('digests-primary.json', {'views': {'noun/exterior': {
                'candidates': [{'source': source, 'images': [{'id': 'Original colors', 'path': str(image),
                              'sha256': hashlib.sha256(body).hexdigest()}]}],
                'rejected': [{'id': source['id'], 'reason': 'Wrong geometry <unsafe>'}]}}})
            manifest = preview.build([batch], output)
            page = (output / 'batch-10-1.html').read_text()
            self.assertIn('geometry rejected', page)
            self.assertIn('Wrong geometry &lt;unsafe&gt;', page)
            self.assertIn('&lt;script&gt;candidate&lt;/script&gt;', page)
            self.assertNotIn('<script>', page)
            self.assertIn('loading="lazy"', page)
            self.assertIn('noopener noreferrer', page)
            self.assertEqual(manifest['batches'][0]['candidates'], 1)
            self.assertEqual(manifest['batches'][0]['images'], 1)
            copied = output / (hashlib.sha256(body).hexdigest() + '.png')
            self.assertEqual(copied.read_bytes(), body)
            before = (output / 'batch-10-1.html').read_bytes()
            preview.build([batch], output)
            self.assertEqual((output / 'batch-10-1.html').read_bytes(), before)
            image.write_bytes(preview.PNG + b'different evidence')
            result = preview.build([batch], output)
            self.assertEqual(result['batches'][0]['images'], 0, 'a stale SHA pin must not fall back to unpinned bytes')

    def test_images_cannot_escape_the_batch_or_use_active_formats(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch = root / 'diagram-batch-10'; batch.mkdir()
            output = root / 'gallery'; output.mkdir()
            outside = root / 'private.png'; outside.write_bytes(preview.PNG + b'PRIVATE')
            self.assertIsNone(preview.image_copy(outside, batch, output))
            link = batch / 'link.png'; link.symlink_to(outside)
            self.assertIsNone(preview.image_copy(link, batch, output))
            active = batch / 'image.svg'; active.write_text('<svg><script/></svg>')
            self.assertIsNone(preview.image_copy(active, batch, output))
            self.assertEqual(list(output.iterdir()), [])
            self.assertEqual(preview.href('javascript:alert(1)'), '')


if __name__ == '__main__':
    unittest.main()
