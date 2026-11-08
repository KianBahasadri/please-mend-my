from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import review_diagram_batch as review


def fixture():
    parts = [{"id": name, "kind": "physical"} for name in ("chain", "wheel", "brake")]
    art = {"version": 1, "id": "example", "viewBox": "0 0 100 100", "context": [], "parts": {}}
    leaves = []
    for index, part in enumerate(parts):
        key = "0." + str(index)
        art["parts"][part["id"]] = [["rect", {"x": str(index * 30), "y": "10", "width": "20", "height": "20",
                                  "fill": "currentColor", "data-source-id": key}]]
        leaves.append({"key": key, "bbox": [index * .3, .1, .2, .2]})
    return art, parts, leaves


class DiagramReviewTests(unittest.TestCase):
    def test_distinct_measured_parts_pass_as_unapproved_review_candidates(self):
        art, parts, leaves = fixture()
        result = review.validate_geometry(art, parts, leaves=leaves)
        self.assertTrue(result["valid"], result)
        self.assertTrue(result["metrics"]["requiresVisualReview"])
        self.assertEqual(result["metrics"]["mappedPhysicalParts"], 3)
        self.assertEqual(result["metrics"]["physicalCoverage"], 1)

    def test_symptom_low_confidence_and_whole_object_assignments_are_held(self):
        art, parts, leaves = fixture()
        parts[0]["kind"] = "symptom"
        leaves[0]["bbox"] = [0, 0, 1, 1]
        mapping = {"groupMap": [{"node": "0.0", "confidence": "low"}]}
        result = review.validate_geometry(art, parts, leaves=leaves, mapping=mapping)
        self.assertFalse(result["valid"])
        self.assertTrue(any("nonphysical" in item for item in result["issues"]))
        self.assertTrue(any("low-confidence" in item for item in result["issues"]))
        self.assertTrue(any("entire drawing" in item for item in result["issues"]))

    def test_duplicate_geometry_and_almost_identical_regions_are_rejected(self):
        art, parts, leaves = fixture()
        art["parts"]["wheel"] = deepcopy(art["parts"]["chain"])
        art["parts"]["wheel"][0][1]["data-source-id"] = "0.1"
        leaves[1]["bbox"] = leaves[0]["bbox"]
        result = review.validate_geometry(art, parts, leaves=leaves)
        self.assertFalse(result["valid"])
        self.assertTrue(any("identical geometry" in item for item in result["issues"]))
        self.assertTrue(any("identical bounding" in item for item in result["issues"]))

    def test_missing_bounds_or_empty_buckets_fail_when_geometry_digest_exists(self):
        art, parts, leaves = fixture()
        leaves[0]["bbox"] = [float("nan"), 0, 1, 1]
        art["parts"]["brake"] = []
        result = review.validate_geometry(art, parts, leaves=leaves)
        self.assertFalse(result["valid"])
        self.assertTrue(any("lacks measured bounds" in item for item in result["issues"]))
        self.assertTrue(any("empty mapped part" in item for item in result["issues"]))

    def test_secondary_views_must_add_two_new_physical_parts_and_maximum_three(self):
        _, parts, _ = fixture()
        parts.extend([{"id": "pedal", "kind": "physical"}, {"id": "pain", "kind": "symptom"}])
        valid = review.validate_views([{"id": "outer", "mappedParts": ["chain", "wheel"]},
                                       {"id": "inner", "mappedParts": ["brake", "pedal"]}], parts)
        self.assertTrue(valid["valid"])
        invalid = review.validate_views([{"id": "outer", "mappedParts": ["chain", "wheel"]},
                                         {"id": "inner", "mappedParts": ["wheel", "brake", "pain"]}], parts)
        self.assertFalse(invalid["valid"])
        self.assertTrue(any("nonphysical" in item for item in invalid["issues"]))
        self.assertFalse(review.validate_views([{"id": str(i), "mappedParts": []} for i in range(4)], parts)["valid"])

    def test_two_parts_require_explicit_secondary_gate(self):
        art, parts, leaves = fixture()
        del art['parts']['brake']
        self.assertFalse(review.validate_geometry(art, parts, leaves=leaves)['valid'])
        self.assertTrue(review.validate_geometry(art, parts, leaves=leaves, minimum_parts=2)['valid'])
        self.assertRaises(ValueError, review.validate_geometry, art, parts, minimum_parts=1)

    def test_contact_sheet_is_one_bounded_render_and_escapes_labels(self):
        art, _, _ = fixture()
        with tempfile.TemporaryDirectory() as directory, patch("review_diagram_batch.subprocess.run") as render:
            output = Path(directory) / "sheet.png"
            result = review.write_review_sheet(art, output, "<untrusted & title>")
            self.assertEqual(result["panels"], 4)
            self.assertEqual(result["height"], 512)
            self.assertTrue(result["requiresVisualReview"])
            render.assert_called_once()
            self.assertEqual(render.call_args.args[0][0], "rsvg-convert")
            self.assertEqual(render.call_args.kwargs["timeout"], 45)
            root = ET.parse(output.with_suffix(".svg")).getroot()
            self.assertIn("<untrusted & title>", "".join(root.itertext()))
            self.assertEqual(len(root.findall("{" + review.NS + "}svg")), 4)


if __name__ == "__main__":
    unittest.main()
