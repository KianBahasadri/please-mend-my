"""Source integrity, deterministic preparation, and safe actual-art provenance."""

import hashlib
import json
from pathlib import Path
import unittest

import diagram_svg as svg
from ingest_common import json_bytes


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCES = ROOT / "docs/references/bicycle"
CONFIG = json.loads((HERE / "data/archetype-art.json").read_text())
APPROVED = next(row for row in CONFIG['artifacts'] if row['id'] == 'bicycle')


def approved_art():
    return svg.load_approved_art(ROOT, APPROVED)



class BicycleArtTests(unittest.TestCase):
    def test_pinned_originals_and_recorded_sources_match(self):
        manifest = json.loads((SOURCES / "sources.json").read_text())
        self.assertEqual(len(manifest["sources"]), 2)
        for source in manifest["sources"]:
            original = (SOURCES / source["file"]).read_bytes()
            self.assertEqual(len(original), source["bytes"])
            self.assertEqual(hashlib.sha256(original).hexdigest(), source["sha256"])
            self.assertIn({"file": "docs/references/bicycle/" + source["file"], "sha256": source["sha256"]}, APPROVED["sourcePins"])

    def test_reviewed_snapshot_preserves_the_precleanup_runtime_hash(self):
        data = approved_art()
        data.update(version=1, id="oewn-02837983-n")
        self.assertEqual(hashlib.sha256(json_bytes(data)).hexdigest(),
                         "74e8064664d9ec683744234b66a4a425445b0dc00941bb214582ac118e753bb2")

    def test_each_interactive_part_reuses_source_geometry(self):
        data = approved_art()
        commons = svg.parse_source(SOURCES / "commons-bicycle-diagram.original.svg", APPROVED["sourcePins"][0]["sha256"])
        original = {tuple(map(int, key.removeprefix("0.").split("."))): node for key, node in svg.paths(commons) if key != "0"}
        other = svg.parse_source(SOURCES / "openclipart-bicycle.original.svg", APPROVED["sourcePins"][1]["sha256"])
        headlight = next(node for node in other.iter() if node.get("id") == "path10148")
        self.assertEqual(list(data["parts"]), ["wheels", "tires", "fork", "chain", "gears", "seat", "handlebars",
                                               "brakes", "pedals", "bottom-bracket", "lights"])
        seen_sources = set()
        for name, shapes in [("context", data["context"]), *data["parts"].items()]:
            self.assertTrue(shapes, name)
            for tag, attrs in shapes:
                self.assertIn(tag, {"path", "circle", "rect", "ellipse", "line", "polyline", "polygon"})
                self.assertFalse(set(attrs) - svg.GEOMETRY - svg.PRESENTATION - {
                    "fill", "stroke", "transform", "data-source-id"})
                identifier = attrs["data-source-id"]
                namespace, location = identifier.split(":", 1)
                seen_sources.add(namespace)
                source_node = original[tuple(map(int, location.split(".")))] if namespace == "commons" else headlight
                self.assertEqual(svg.local(source_node.tag), tag)
                for key in svg.GEOMETRY & set(attrs):
                    self.assertEqual(attrs[key], source_node.get(key), f"{identifier}: {key} was redrawn")
                self.assertIn(attrs["fill"], {"none", "currentColor", "var(--paper)"})
                self.assertIn(attrs["stroke"], {"none", "currentColor"})
                self.assertFalse(any("url(" in value.lower() or "javascript:" in value.lower() for value in attrs.values()))
        self.assertEqual(seen_sources, {"commons", "openclipart"})

    def test_references_are_expanded_without_filters_or_external_paints(self):
        data = approved_art()
        wheels = data["parts"]["wheels"]
        # Both wheels reuse the same original hub/spoke paths with distinct
        # inherited transforms. Simply dropping SVG use nodes loses these.
        hubs = [attrs for _, attrs in wheels if attrs["data-source-id"] == "commons:4.2.2.0"]
        self.assertGreaterEqual(len(hubs), 2)
        self.assertGreater(len({node["transform"] for node in hubs}), 1)
        self.assertTrue(all("filter" not in attrs and "href" not in attrs for shapes in data["parts"].values() for _, attrs in shapes))
        credit = data["attribution"]
        self.assertIn("Al2", credit["author"])
        self.assertEqual(credit["license"], "CC BY 3.0")
        self.assertIn("Adapted", credit["changes"])
        self.assertEqual(credit["supplementary"][0]["author"], "WRomey")
        self.assertEqual(credit["supplementary"][0]["license"], "CC0 1.0")


if __name__ == "__main__":
    unittest.main()
