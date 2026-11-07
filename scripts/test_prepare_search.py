import base64
import copy
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import prepare_search as search


def unpack(metadata, key=""):
    terms = metadata["stemTerms" if key else "terms"]
    offsets = metadata["stemOffsets" if key else "offsets"]
    body = base64.b64decode(metadata["stemPostings" if key else "postings"])
    output = {}
    for index, term in enumerate(terms):
        values, value, shift, previous = [], 0, 0, 0
        for byte in body[offsets[index]:offsets[index + 1]]:
            value |= (byte & 127) << shift
            if byte & 128:
                shift += 7
            else:
                previous += value
                values.append(previous)
                value, shift = 0, 0
        if shift:
            raise AssertionError("Truncated posting varint")
        output[term] = values
    return output


def fixture():
    return {"version": 1, "revision": "fixture", "source": {"license": "source-license"},
            "categories": [{"id": "technology", "name": "Technology"},
                           {"id": "household", "name": "Household"}],
            "items": [["one", "MacBook A1708 Battery Repair", "technology", "guide", "MacBook A1708"],
                      ["two", "MacBook A1708 Batteries", "technology", "topic", "MacBook A1708"],
                      ["three", "Refrigerator Thermostat", "household", "guide", "Refrigerator"]],
            "search": {"version": 1, "groups": ["", "laptop\nnotebook", "fridge\nrefrigerator"],
                       "itemGroups": [1, 1, 2], "sources": [{"license": "CC0-1.0"}]}}


class SearchPreparationTests(unittest.TestCase):
    def test_original_porter_forms_and_strict_model_tokens(self):
        expected = {"batteries": "batteri", "battery": "batteri", "repairing": "repair",
                    "repairs": "repair", "caresses": "caress", "ponies": "poni", "ties": "ti",
                    "bled": "bled", "hopping": "hop", "falling": "fall", "filing": "file",
                    "happy": "happi", "sky": "sky", "relational": "relat", "conformabli": "conform",
                    "sensibiliti": "sensibl", "electriciti": "electr", "replacement": "replac",
                    "adoption": "adopt", "probate": "probat", "rate": "rate", "cease": "ceas",
                    "controll": "control", "roll": "roll", "S23": "S23", "s23": "s23",
                    "a1708": "a1708", "1000xm4": "1000xm4", "café": "café", "ab": "ab"}
        for value, stem in expected.items():
            with self.subTest(value=value):
                self.assertEqual(search.porter(value), stem)

    def test_postings_share_alias_groups_and_stem_equivalence(self):
        index = fixture()
        before = copy.deepcopy(index)
        prepared = search.prepare_index(index)
        self.assertEqual(index, before, "Pure preparation must not mutate source records")
        postings, stems = unpack(prepared), unpack(prepared, "stem")
        self.assertEqual(postings["macbook"], [0, 1])  # Duplicate own title/device terms collapse.
        self.assertEqual(postings["technology"], [0, 1])
        self.assertEqual(postings["laptop"], [4])  # N=3 + group1; never duplicate for each item.
        self.assertEqual(postings["fridge"], [5])
        self.assertEqual(postings["1708"], [0, 1])
        self.assertEqual([prepared["terms"][number] for number in stems["batteri"]],
                         ["batteries", "battery"])
        self.assertNotIn("1708", stems, "Numeric/identity forms already have exact postings")
        self.assertNotIn("repair", stems, "Unchanged roots are looked up directly by the worker")
        self.assertEqual(prepared["normalization"], "NFKD-no-marks-lower")
        self.assertEqual(prepared["stemmer"], "porter1980")
        self.assertEqual(search.prepare_index(fixture()), prepared)

    def test_unicode_normalization_numbers_and_browser_order(self):
        self.assertEqual(search.tokens("Café_ＦＩＸ A1708 / WH-1000XM4"),
                         ["cafe", "fix", "a1708", "wh", "1000xm4", "1708", "1000", "4"])
        index = fixture()
        index["items"][0][1] = "\U00010400 \ufff0 \u4e00"
        prepared = search.prepare_index(index)
        self.assertEqual(prepared["terms"], sorted(prepared["terms"], key=search.lexicographic))

    def test_multibyte_delta_varints_decode_and_reject_invalid_identifiers(self):
        offsets, body = search.pack_postings([[0, 127, 128, 16384, 2**32 - 1], [1, 3, 900]])
        self.assertEqual(unpack({"terms": ["first", "second"], "offsets": offsets, "postings": body}),
                         {"first": [0, 127, 128, 16384, 2**32 - 1], "second": [1, 3, 900]})
        with self.assertRaises(ValueError):
            search.pack_postings([[2, 1]])
        with self.assertRaises(ValueError):
            search.pack_postings([[2**32]])

    def test_stage_keeps_rows_provenance_and_deterministic_completed_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            original = fixture()
            (runtime / "index.json").write_text(json.dumps(original))
            (runtime / "manifest.json").write_text(json.dumps({"revision": "fixture", "runtimeComplete": True,
                                                              "searchEnrichment": {"version": 1}}))
            result = search.prepare(root)
            body = (runtime / "index.json").read_bytes()
            packed = (runtime / "index.json.gz").read_bytes()
            rebuilt = json.loads(body)
            manifest = json.loads((runtime / "manifest.json").read_bytes())
            self.assertEqual(rebuilt["items"], original["items"])
            self.assertEqual(rebuilt["source"], original["source"])
            self.assertEqual(rebuilt["search"]["sources"], original["search"]["sources"])
            self.assertEqual(gzip.decompress(packed), body)
            self.assertTrue(manifest["runtimeComplete"])
            self.assertEqual(manifest["indexBytes"], len(body))
            self.assertEqual(manifest["indexGzipBytes"], len(packed))
            self.assertEqual(result["indexSha256"], hashlib.sha256(body).hexdigest())
            self.assertEqual(search.prepare(root), result)
            self.assertEqual((runtime / "index.json").read_bytes(), body)
            self.assertEqual((runtime / "index.json.gz").read_bytes(), packed)

    def test_bad_alias_references_fail_before_writing(self):
        index = fixture()
        for references in ([1], [1, 99, 2], [True, 1, 2]):
            index["search"]["itemGroups"] = references
            with self.assertRaisesRegex(ValueError, "alias format"):
                search.prepare_index(index)

    def test_interrupted_stage_cannot_remain_marked_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "index.json").write_text(json.dumps(fixture()))
            (runtime / "manifest.json").write_text(json.dumps({"revision": "fixture", "runtimeComplete": True,
                                                              "searchEnrichment": {"version": 1}}))
            write = search.atomic_write
            def interrupted(path, body):
                if path.name == "index.json.gz":
                    raise OSError("disk full")
                write(path, body)
            with patch.object(search, "atomic_write", side_effect=interrupted):
                with self.assertRaisesRegex(OSError, "disk full"):
                    search.prepare(root)
            self.assertFalse(json.loads((runtime / "manifest.json").read_bytes())["runtimeComplete"])


if __name__ == "__main__":
    unittest.main()
