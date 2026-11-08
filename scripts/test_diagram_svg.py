from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

import diagram_svg as svg


def source_svg(extra=""):
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
            '<g id="parts" transform="translate(3 4)" style="fill:none;stroke:#000">'
            + ''.join(f'<rect id="part{i}" x="{i * 10}" y="10" width="8" height="10" />' for i in range(8))
            + '</g>' + extra + '</svg>')


class DiagramSVGTests(unittest.TestCase):
    def test_shape_geometry_and_ancestor_transforms_survive(self):
        root = ET.fromstring(source_svg())
        leaves, dropped = svg.flatten(root)
        self.assertEqual(len(leaves), 8)
        attrs = leaves[0]["ast"][1]
        self.assertEqual(attrs["transform"], "translate(3 4)")
        self.assertEqual(attrs["x"], "0")
        self.assertEqual(attrs["fill"], "none")
        self.assertEqual(attrs["stroke"], "currentColor")

    def test_xml_source_pins_and_active_or_raster_elements_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.svg"
            body = source_svg().encode(); source.write_bytes(body)
            self.assertRaises(ValueError, svg.parse_source, source, "wrong-sha")
            svg.parse_source(source, hashlib.sha256(body).hexdigest())
        for extra in ('<image href="data:image/png;base64,eA=="/>', '<script>alert(1)</script>'):
            self.assertRaises(ValueError, svg.flatten, ET.fromstring(source_svg(extra)))

    def test_local_use_expansion_retains_offsets_and_rejects_external_or_cycles(self):
        text = source_svg().replace('</svg>', '<use href="#part0" x="5" y="6"/></svg>')
        leaves, _ = svg.flatten(ET.fromstring(text))
        self.assertEqual(len(leaves), 9)
        self.assertIn("translate(5 6)", leaves[-1]["ast"][1]["transform"])
        self.assertRaises(ValueError, svg.flatten, ET.fromstring(source_svg('<use href="https://example.com/a.svg#p"/>')))
        self.assertRaises(ValueError, svg.flatten, ET.fromstring(source_svg('<use id="loop" href="#loop"/>')))

    def test_mapping_requires_known_paths_parts_and_three_assemblies(self):
        leaves, _ = svg.flatten(ET.fromstring(source_svg()))
        digest = {"viewBox": [0, 0, 100, 100], "nodes": [{"key": leaf["key"]} for leaf in leaves]}
        subject = {"id": "test", "parts": [{"id": name} for name in ('a', 'b', 'c')]}
        source = {"id": "source", "title": "Pinned", "author": "Artist", "source": "https://example.com/source",
                  "license": "CC BY-SA 3.0", "licenseUrl": "https://creativecommons.org/licenses/by-sa/3.0/"}
        mapping = {"selectedSource": "source", "groupMap": [
            {"node": leaves[i]["key"], "part": name, "confidence": "high"} for i, name in enumerate(('a', 'b', 'c'))]}
        art = svg.compile_art(subject, source, mapping, digest, leaves)
        self.assertEqual(set(art["parts"]), {'a', 'b', 'c'})
        self.assertEqual(len(art["context"]), 5)
        self.assertIn("distributed under CC BY-SA 3.0", art["attribution"]["changes"])
        unknown = deepcopy(mapping); unknown["groupMap"][0]["node"] = "missing"
        self.assertRaises(ValueError, svg.compile_art, subject, source, unknown, digest, leaves)
        incomplete = deepcopy(mapping); incomplete["groupMap"].pop()
        self.assertRaises(ValueError, svg.compile_art, subject, source, incomplete, digest, leaves)
        secondary = svg.compile_art(subject, source, incomplete, digest, leaves, minimum_parts=2)
        self.assertEqual(set(secondary["parts"]), {'a', 'b'})
        self.assertRaises(ValueError, svg.compile_art, subject, source, incomplete, digest, leaves,
                          minimum_parts=1)
        single = deepcopy(incomplete); single['groupMap'].pop()
        self.assertRaises(ValueError, svg.compile_art, subject, source, single, digest, leaves,
                          minimum_parts=2)

    def test_absolute_dimensions_and_simple_css_are_resolved(self):
        self.assertEqual(svg.length('72pt'), 96)
        self.assertEqual(svg.length('1in'), 96)
        self.assertRaises(ValueError, svg.length, '50%')
        root = ET.fromstring(source_svg().replace('style="fill:none;stroke:#000"', '').replace('<g id=', '<style>.line {fill:white;stroke:black;stroke-dasharray:none}</style><g class="line" id='))
        leaves, _ = svg.flatten(root)
        self.assertEqual(leaves[0]["ast"][1]["fill"], 'var(--paper)')
        self.assertEqual(leaves[0]["ast"][1]["stroke-dasharray"], 'none')

    def test_reviewed_geometry_omission_keeps_shapes_as_context_and_preserves_raw_mapping(self):
        leaves, _ = svg.flatten(ET.fromstring(source_svg()))
        digest = {"viewBox": [0, 0, 100, 100], "nodes": [{"key": leaf["key"]} for leaf in leaves]}
        names = ('screen', 'buttons', 'speaker', 'camera')
        subject = {"id": "phone", "parts": [{"id": name} for name in names]}
        source = {"id": "source", "title": "Phone", "author": "Artist", "source": "https://example.com/source",
                  "license": "CC0", "licenseUrl": "https://creativecommons.org/publicdomain/zero/1.0/"}
        mapping = {"selectedSource": "source", "groupMap": [
            {"node": leaves[index]["key"], "part": name, "confidence": "high"} for index, name in enumerate(names)]}
        before = deepcopy(mapping)
        art = svg.compile_art(subject, source, mapping, digest, leaves, omit_geometry_parts=['camera'])
        self.assertEqual(set(art["parts"]), {'screen', 'buttons', 'speaker'})
        self.assertIn(leaves[3]["ast"], art["context"])
        self.assertEqual(mapping, before)
        self.assertRaises(ValueError, svg.compile_art, subject, source, mapping, digest, leaves,
                          omit_geometry_parts=['camera', 'speaker'])


if __name__ == "__main__":
    unittest.main()
