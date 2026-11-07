import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import enrich_search as search


class AliasTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "catalog.sqlite"
        self.index = {"source": {"name": "iFixit", "license": {"name": "CC BY-NC-SA 3.0"}}, "items": [
            ["mac", "MacBook Air", "technology", "topic", "MacBook Air"],
            ["battery", "MacBook Air Battery Replacement", "technology", "guide", "MacBook Air"],
            ["keyboard", "MacBook Air Keyboard Replacement", "technology", "guide", "MacBook Air"],
            ["fridge", "Refrigerator", "household", "topic", "Refrigerator"],
            ["thermostat", "Thermostat Replacement", "household", "guide", "Refrigerator"],
            ["unlinked", "MacBook-like unrelated article", "other", "reference", ""],
        ]}
        connection = sqlite3.connect(self.database)
        connection.executescript("CREATE TABLE items(id TEXT,source_path TEXT,title TEXT,kind TEXT); CREATE TABLE category_edges(parent TEXT,child TEXT); CREATE TABLE guide_categories(guide TEXT,category TEXT);")
        connection.executemany("INSERT INTO items VALUES(?,?,?,?)", [
            ("laptop-root", "Device/Mac_Laptop", "Mac Laptop", "topic"),
            *[(row[0], ("Device/" if row[3] == "topic" else "Guide/") + row[0], row[1], row[3]) for row in self.index["items"]],
        ])
        connection.executemany("INSERT INTO category_edges VALUES(?,?)", [
            ("Device/Mac_Laptop", "Device/mac"),
            ("Device/mac", "Device/Mac_Laptop"),  # Source cycles must terminate.
        ])
        connection.executemany("INSERT INTO guide_categories VALUES(?,?)", [
            ("Guide/battery", "Device/mac"), ("Guide/keyboard", "Device/mac"),
            ("Guide/thermostat", "Device/fridge"),
        ])
        connection.commit()
        connection.close()

    def test_ancestry_aliases_and_shared_groups_without_guessed_matches(self):
        vocabulary = {"version": 1, "language": "en", "concepts": [
            {"id": "Q3962", "ifixitPaths": ["Device/Mac_Laptop"]},
            {"id": "Q37828", "ifixitPaths": ["Device/fridge"]},
        ]}
        def fetch(concept, cache):
            terms = ["laptop", "notebook"] if concept["id"] == "Q3962" else ["refrigerator", "fridge"]
            return terms, {"id": concept["id"], "license": "CC0-1.0"}
        prepared = search.prepare_search(self.index, self.database, vocabulary, self.root, fetch)
        group = lambda index: prepared["groups"][prepared["itemGroups"][index]]
        self.assertIn("laptop", group(0))
        self.assertIn("notebook", group(1))
        self.assertEqual(prepared["itemGroups"][1], prepared["itemGroups"][2])
        self.assertIn("fridge", group(4))
        self.assertNotIn("laptop", group(5))
        self.assertEqual(prepared["ancestrySource"]["license"]["name"], "CC BY-NC-SA 3.0")

    def test_pinned_cache_keeps_only_english_and_preserves_provenance(self):
        rule = {"id": "Q37828", "revision": 123, "label": "refrigerator", "ifixitPaths": ["Device/Refrigerator"], "curated": ["icebox"], "excludeAliases": ["bad synonym"]}
        entity = {"id": "Q37828", "lastrevid": 123, "labels": {"en": {"language": "en", "value": "refrigerator"}},
                  "aliases": {"en": [{"language": "en", "value": "fridge"}, {"language": "de", "value": "Kühlschrank"}, {"language": "en", "value": "bad synonym"}], "de": [{"language": "de", "value": "Kühlschrank"}]}}
        (self.root / "Q37828-123.json").write_text(json.dumps({"entities": {"Q37828": entity}}))
        with patch.object(search, "urlopen") as network:
            terms, source = search.wikidata_entity(rule, self.root)
            network.assert_not_called()
        self.assertEqual(terms, ["fridge", "icebox", "refrigerator"])
        self.assertEqual(source["revision"], 123)
        self.assertEqual(source["license"], "CC0-1.0")
        self.assertIn("revision=123", source["url"])

    def test_wrong_cached_revision_fails_closed(self):
        rule = {"id": "Q37828", "revision": 123, "label": "refrigerator", "ifixitPaths": []}
        entity = {"id": "Q37828", "lastrevid": 999, "labels": {"en": {"language": "en", "value": "refrigerator"}}}
        (self.root / "Q37828-123.json").write_text(json.dumps({"entities": {"Q37828": entity}}))
        with self.assertRaisesRegex(ValueError, "pinned English identity/revision"):
            search.wikidata_entity(rule, self.root)


if __name__ == "__main__":
    unittest.main()
