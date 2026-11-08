import base64
import copy
from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import prepare_search as search

GUIDE_ID = "a" * 20
OTHER_ID = "b" * 20


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


class NounEnrichmentTests(unittest.TestCase):
    def test_aliases_stay_on_linked_guides_and_part_aliases_require_direct_component(self):
        index = fixture()
        index["items"].append(["four", "MacBook A1708 Screen Repair", "technology", "guide", "MacBook A1708"])
        index["search"]["itemGroups"].append(1)
        index["archetypes"] = {"version": 1, "archetypes": [{
            "id": "laptop", "aliases": ["laptop", "notebook", "Model A1709"],
            "parts": [{"query": "battery", "aliases": ["power cell", "cells", "cell CR2032"]}],
        }]}
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "catalog.sqlite"
            with closing(sqlite3.connect(database)) as db:
                db.executescript("CREATE TABLE items(id,title,device,kind); CREATE TABLE guide_nouns(guide_id,noun_id);")
                db.executemany("INSERT INTO items VALUES(?,?,?,?)", [(row[0], row[1], row[4], row[3]) for row in index["items"]])
                db.executemany("INSERT INTO guide_nouns VALUES(?,?)", [("one", "laptop"), ("two", "laptop"), ("four", "laptop")])
                db.commit()
            extra = search.noun_terms(index, database)
            self.assertEqual(set(extra), {"one", "four"}, "Topic rows must not receive semantic postings")
            self.assertEqual(extra["one"], {"laptop", "notebook", "power", "cell", "cells"})
            self.assertEqual(extra["four"], {"laptop", "notebook"}, "A shared laptop noun cannot give every guide battery aliases")
            postings = unpack(search.prepare_index(index, noun_records=extra))
            self.assertEqual(postings["cell"], [0])
            self.assertEqual(postings["notebook"], [0, 3, 5], "Keep old alias-group posting positions intact")
            self.assertNotIn("a1709", postings, "Numeric aliases must not weaken the strict literal model gate")
            self.assertNotIn("cr2032", postings)
            self.assertNotIn("model", postings)

    def test_link_missing_from_compact_index_is_rejected(self):
        index = fixture()
        index["archetypes"] = {"version": 1, "archetypes": [{"id": "noun", "aliases": ["object"], "parts": []}]}
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "catalog.sqlite"
            with closing(sqlite3.connect(database)) as db:
                db.executescript("CREATE TABLE items(id,title,device,kind); CREATE TABLE guide_nouns(guide_id,noun_id);")
                db.execute("INSERT INTO items VALUES('missing','Unknown','Object','guide')")
                db.execute("INSERT INTO guide_nouns VALUES('missing','noun')")
                db.commit()
            with self.assertRaisesRegex(ValueError, "absent from the catalog"):
                search.noun_terms(index, database)


class SearchPreparationTests(unittest.TestCase):
    def test_structured_term_evidence_excludes_generated_noun_aliases_and_categories(self):
        index = fixture()
        records = {'one': {'id': 'one', 'version': 2,
                           'symptoms': ['No sound', 'Unresponsive screen'], 'materials': ['Mineral oil']}}
        result = search.prepare_index(index, records, {'one': {'paw', 'synthetic-alias'}})
        self.assertEqual(result['structuredTerms'], [[0, ['mineral', 'no', 'oil', 'screen', 'sound', 'unresponsive']]])
        self.assertIn('paw', unpack(result), 'ordinary semantic postings remain available')
        self.assertNotIn('paw', result['structuredTerms'][0][1], 'aliases are never symptom evidence')
        self.assertNotIn('technology', result['structuredTerms'][0][1])
        self.assertNotIn('structuredTerms', search.prepare_index(index))

    def write_runtime(self, directory, index=None, structured=None):
        root = Path(directory)
        runtime = root / "runtime"
        runtime.mkdir()
        original = fixture() if index is None else index
        for row in original["items"]:
            row[0] = {"one": GUIDE_ID, "two": OTHER_ID, "three": "c" * 20}.get(row[0], row[0])
        (runtime / "index.json").write_text(json.dumps(original))
        manifest = {"revision": "fixture", "runtimeComplete": True,
                    "searchEnrichment": {"version": 1}}
        if structured is not None:
            manifest["structuredGuides"] = structured
        (runtime / "manifest.json").write_text(json.dumps(manifest))
        return root, runtime, original

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

    def test_v2_symptoms_and_materials_are_document_postings_not_shared_aliases(self):
        index = fixture()
        index["items"][0][1] = "Anaphylaxis"
        records = {"one": {"id": "one", "version": 2,
                           "symptoms": ["throat swelling", "shortness of breath", "hives", "impending doom", "HIVES"],
                           "materials": ["Mineral oil", "WH-1000XM4 battery", "mineral oil"],
                           "html": "Do not index this arbitrary body text"}}
        before_index, before_records = copy.deepcopy(index), copy.deepcopy(records)
        prepared = search.prepare_index(index, records)
        postings, stems = unpack(prepared), unpack(prepared, "stem")
        for term in ("throat", "swelling", "shortness", "breath", "hives", "impending", "doom", "mineral", "oil"):
            self.assertEqual(postings[term], [0], term)
        self.assertEqual(postings["laptop"], [4], "An unchanged shared alias may still belong to both MacBooks")
        self.assertEqual(postings["1000xm4"], [0])
        self.assertEqual(postings["1000"], [0])
        self.assertEqual(prepared["guideLiterals"], [[0, "wh-1000xm4 battery"]])
        self.assertNotIn("1000xm4", stems, "Model identifiers must not be stemmed")
        self.assertIn("swelling", [prepared["terms"][number] for number in stems["swell"]])
        self.assertIn("hives", [prepared["terms"][number] for number in stems["hive"]])
        self.assertNotIn("arbitrary", postings, "Only explicitly requested fields are indexed")
        self.assertEqual(index, before_index)
        self.assertEqual(records, before_records)
        self.assertEqual(prepared, search.prepare_index(index, list(records.values())))
        self.assertEqual(prepared, search.prepare_index(index, dict(reversed(list(records.items())))))

    def test_sparse_numeric_literals_keep_punctuation_and_stay_on_their_guide(self):
        records = {"three": {"id": "three", "version": 2, "symptoms": ["Buzzing sound"],
                             "materials": ["3/32 inch Flathead Screwdriver", "Café Oil Ｈ１"]},
                   "one": {"id": "one", "version": 2, "symptoms": ["Hives"], "materials": ["H1 antihistamine"]},
                   "two": {"id": "two", "version": 2, "symptoms": ["Swelling"], "materials": ["Oil"]}}
        prepared = search.prepare_index(fixture(), records)
        self.assertEqual(prepared["guideLiterals"], [[0, "h1 antihistamine"],
                         [2, "3/32 inch flathead screwdriver cafe oil h1"]])
        postings = unpack(prepared)
        self.assertEqual(postings["h1"], [0, 2])
        self.assertEqual(postings["antihistamine"], [0])
        self.assertEqual(postings["screwdriver"], [2])
        self.assertEqual(postings["32"], [2])
        self.assertEqual(prepared, search.prepare_index(fixture(), dict(reversed(list(records.items())))))

    def test_numeric_literal_detection_follows_normalization_for_fractions(self):
        record = {"id": "one", "version": 2,
                  "materials": ["½ cup", "Ⅳ litres", "四 cups"], "symptoms": []}
        prepared = search.prepare_index(fixture(), [record])
        self.assertEqual(prepared["guideLiterals"], [[0, "1⁄2 cup"]])
        postings = unpack(prepared)
        self.assertEqual(postings["1"], [0])
        self.assertEqual(postings["2"], [0])

    def test_no_or_empty_structured_fields_leave_the_original_postings_unchanged(self):
        index = fixture()
        baseline = search.prepare_index(index)
        self.assertEqual(search.prepare_index(index, {}), baseline)
        self.assertEqual(search.prepare_index(index, []), baseline)
        self.assertEqual(search.prepare_index(index, {"one": {"id": "one", "version": 2}}), baseline)
        self.assertEqual(search.prepare_index(index, {"one": {"id": "one", "version": 2,
                                                             "symptoms": [], "materials": []}}), baseline)

    def test_malformed_unknown_or_duplicate_structured_records_are_rejected(self):
        record = {"id": "one", "version": 2, "symptoms": ["hives"], "materials": []}
        cases = [1, [None], {"one": "text"}, {"two": record},
                 {"one": {**record, "version": 1}}, {"one": {**record, "version": 2.0}},
                 {"one": {**record, "symptoms": "hives"}},
                 {"one": {**record, "materials": [{"invented": "string coercion"}]}},
                 {"one": {**record, "symptoms": [None]}},
                 {"unknown": {**record, "id": "unknown"}}, [record, record]]
        for records in cases:
            with self.subTest(records=records), self.assertRaises(ValueError):
                search.prepare_index(fixture(), records)
        index = fixture()
        index["items"][1][0] = "one"
        with self.assertRaisesRegex(ValueError, "more than once"):
            search.prepare_index(index, {"one": record})

    def test_runtime_reads_only_validated_structured_ids_and_keeps_payloads_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root, runtime, original = self.write_runtime(directory, structured={
                "processedItems": 1, "itemIds": [GUIDE_ID]})
            guides = runtime / "guides"
            guides.mkdir()
            record = {"id": GUIDE_ID, "version": 2, "symptoms": ["hives", "throat swelling"],
                      "materials": ["mineral oil"]}
            source_body = json.dumps(record).encode()
            (guides / (GUIDE_ID + ".json")).write_bytes(source_body)
            (guides / (OTHER_ID + ".json")).write_bytes(b"Unselected raw payload must not be opened")
            reads = []
            read = Path.read_bytes
            def traced(path):
                if path.parent == guides:
                    reads.append(path.name)
                return read(path)
            with patch.object(Path, "read_bytes", traced), patch.object(Path, "iterdir", side_effect=AssertionError("No guide scan")):
                metadata = search.prepare(root)
            self.assertEqual(reads, [GUIDE_ID + ".json"])
            rebuilt = json.loads((runtime / "index.json").read_bytes())
            postings = unpack(rebuilt["search"]["inverted"])
            self.assertEqual(postings["hives"], [0])
            self.assertEqual(postings["mineral"], [0])
            self.assertEqual(rebuilt["items"], original["items"])
            self.assertEqual((guides / (GUIDE_ID + ".json")).read_bytes(), source_body)
            self.assertEqual(metadata["structuredDocuments"], 1)
            self.assertEqual(metadata["guideFields"], ["symptoms", "materials"])
            self.assertEqual(gzip.decompress((runtime / "index.json.gz").read_bytes()),
                             (runtime / "index.json").read_bytes())
            self.assertEqual(search.prepare(root), metadata)

    def test_legacy_and_empty_processed_metadata_never_read_guide_files(self):
        for structured in (None, {}, {"processedItems": 0}, {"processedItems": 0, "itemIds": []}):
            with self.subTest(structured=structured), tempfile.TemporaryDirectory() as directory:
                root, runtime, _ = self.write_runtime(directory, structured=structured)
                read = Path.read_bytes
                def traced(path):
                    self.assertNotEqual(path.parent.name, "guides", "Legacy guides need no content reads")
                    return read(path)
                with patch.object(Path, "read_bytes", traced):
                    metadata = search.prepare(root)
                self.assertEqual(metadata["structuredDocuments"], 0)
                self.assertFalse((runtime / "guides").exists())

    def test_invalid_or_incomplete_structured_id_metadata_fails_before_writing(self):
        cases = [{"processedItems": 1}, {"processedItems": 2, "itemIds": [GUIDE_ID]},
                 {"processedItems": 2, "itemIds": [GUIDE_ID, GUIDE_ID]},
                 {"processedItems": 1, "itemIds": ["f" * 20]},
                 {"processedItems": 1, "itemIds": ["../outside"]},
                 {"processedItems": 1, "itemIds": ["one"]},
                 {"processedItems": True, "itemIds": [GUIDE_ID]},
                 {"processedItems": -1, "itemIds": []}, {"processedItems": 1, "itemIds": GUIDE_ID}]
        for structured in cases:
            with self.subTest(structured=structured), tempfile.TemporaryDirectory() as directory:
                root, runtime, _ = self.write_runtime(directory, structured=structured)
                before_index = (runtime / "index.json").read_bytes()
                before_manifest = (runtime / "manifest.json").read_bytes()
                with self.assertRaises(ValueError):
                    search.prepare(root)
                self.assertEqual((runtime / "index.json").read_bytes(), before_index)
                self.assertEqual((runtime / "manifest.json").read_bytes(), before_manifest)
                self.assertFalse((runtime / "index.json.gz").exists())

    def test_missing_malformed_or_symlink_structured_guide_cannot_publish(self):
        for mode in ("missing", "wrong-id", "legacy-version", "non-string", "invalid-json", "symlink"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root, runtime, _ = self.write_runtime(directory, structured={"processedItems": 1, "itemIds": [GUIDE_ID]})
                guides = runtime / "guides"
                guides.mkdir()
                record = {"id": GUIDE_ID, "version": 2, "symptoms": ["hives"], "materials": []}
                path = guides / (GUIDE_ID + ".json")
                if mode == "wrong-id": record["id"] = OTHER_ID
                elif mode == "legacy-version": record["version"] = 1
                elif mode == "non-string": record["materials"] = [False]
                if mode == "symlink":
                    target = root / "elsewhere.json"
                    target.write_text(json.dumps(record))
                    path.symlink_to(target)
                elif mode == "invalid-json": path.write_text("{truncated")
                elif mode != "missing": path.write_text(json.dumps(record))
                before = (runtime / "manifest.json").read_bytes()
                with self.assertRaises((OSError, ValueError)):
                    search.prepare(root)
                self.assertEqual((runtime / "manifest.json").read_bytes(), before)

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
