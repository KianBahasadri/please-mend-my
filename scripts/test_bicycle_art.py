"""Source integrity, deterministic preparation, and safe actual-art provenance."""

import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("prepare_bicycle_art", HERE / "prepare_bicycle_art.py")
prepare_art = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare_art)


class BicycleArtTests(unittest.TestCase):
    def test_pinned_originals_and_recorded_sources_match(self):
        manifest = json.loads((prepare_art.SOURCES / "sources.json").read_text())
        self.assertEqual(len(manifest["sources"]), 2)
        for source in manifest["sources"]:
            original = (prepare_art.SOURCES / source["file"]).read_bytes()
            self.assertEqual(len(original), source["bytes"])
            self.assertEqual(hashlib.sha256(original).hexdigest(), source["sha256"])
            self.assertEqual(source["sha256"], prepare_art.PINNED[source["file"]])

    def test_generated_module_is_exact_offline_output(self):
        self.assertEqual(prepare_art.OUTPUT.read_bytes(), prepare_art.javascript(prepare_art.prepare()))

    def test_each_interactive_part_reuses_source_geometry(self):
        data = prepare_art.prepare()
        commons = prepare_art.read_source("commons-bicycle-diagram.original.svg")
        original = {path: node for node, path in prepare_art.descendants(commons)}
        other = prepare_art.read_source("openclipart-bicycle.original.svg")
        headlight = next(node for node in other.iter() if node.get("id") == "path10148")
        self.assertEqual(list(data["parts"]), prepare_art.ORDER)
        seen_sources = set()
        for name, shapes in [("context", data["context"]), *data["parts"].items()]:
            self.assertTrue(shapes, name)
            for tag, attrs in shapes:
                self.assertIn(tag, {"path", "circle", "rect", "ellipse", "line", "polyline", "polygon"})
                self.assertFalse(set(attrs) - prepare_art.GEOMETRY - prepare_art.PRESENTATION - {
                    "fill", "stroke", "transform", "data-source-id"})
                identifier = attrs["data-source-id"]
                namespace, location = identifier.split(":", 1)
                seen_sources.add(namespace)
                source_node = original[tuple(map(int, location.split(".")))] if namespace == "commons" else headlight
                self.assertEqual(prepare_art.local(source_node.tag), tag)
                for key in prepare_art.GEOMETRY & set(attrs):
                    self.assertEqual(attrs[key], source_node.get(key), f"{identifier}: {key} was redrawn")
                self.assertIn(attrs["fill"], {"none", "currentColor", "var(--paper)"})
                self.assertIn(attrs["stroke"], {"none", "currentColor"})
                self.assertFalse(any("url(" in value.lower() or "javascript:" in value.lower() for value in attrs.values()))
        self.assertEqual(seen_sources, {"commons", "openclipart"})

    def test_references_are_expanded_without_filters_or_external_paints(self):
        data = prepare_art.prepare()
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
