"""Frozen normalization/index behavior across the shared-helper extraction."""
import hashlib
import json
import unittest

import ingest_common as text
import prepare_search as search
import seed_nouns


class SharedTextTests(unittest.TestCase):
    def test_distinct_alias_key_contracts_preserve_unicode_and_punctuation(self):
        source = "  Straße_ＣＡＦÉ\tSM-G991B  / it's\n"
        self.assertEqual(text.clean(source), "  straße_cafe\tsm-g991b  / it's\n")
        self.assertEqual(text.normalize(source), "straße_cafe sm-g991b / it's")
        self.assertEqual(text.normalize_words(source), "straße cafe sm g991b it s")
        self.assertEqual(text.normalize_noun(source), "strasse cafe sm g991b it s")
        self.assertEqual(text.normalize_ascii_words(source), "stra e sm g991b it s")
        self.assertEqual(seed_nouns.normalize(source), text.normalize_noun(source))
        # Search helpers keep their existing permissive value handling.
        self.assertEqual(text.clean(None), "")
        self.assertEqual(text.clean(0), "")
        self.assertEqual(text.clean(1708), "1708")

    def test_model_numbers_remain_tokens_not_stemmed_or_fuzzed(self):
        self.assertEqual(text.tokens("Café_ＦＩＸ A1708 / WH-1000XM4 SM-G991B"),
                         ["cafe", "fix", "a1708", "wh", "1000xm4", "sm", "g991b",
                          "1708", "1000", "4", "991"])
        for literal in ("a1708", "1000xm4", "g991b", "1708", "café", "S23"):
            self.assertEqual(text.porter(literal), literal)
        self.assertEqual(text.normalize_tokens("SM-G991B"), "sm g991b 991")
        self.assertEqual(text.porter("batteries"), "batteri")
        self.assertEqual(text.porter("repairing"), "repair")
        # These distinguish original Porter from later BLI/LOGI revisions.
        self.assertEqual(text.porter("conformabli"), "conform")
        self.assertEqual(text.porter("geologi"), "geologi")

    def test_pre_refactor_index_bytes_cover_unicode_models_symptoms_and_aliases(self):
        index = {"version": 1, "revision": "shared-text-fixture",
                 "categories": [{"id": "technology", "name": "Technology"},
                                {"id": "health", "name": "Health & medicine"}],
                 "items": [
                     ["a" * 20, "Café_ＦＩＸ A1708 Battery Replacement", "technology", "guide", "MacBook A1708"],
                     ["b" * 20, "Straße SM-G991B Batteries", "technology", "article", "Samsung SM-G991B"],
                     ["c" * 20, "Broken arm", "health", "article", ""],
                     ["d" * 20, "Phone", "technology", "topic", "Phone"]],
                 "search": {"version": 1, "groups": ["", "notebook\nlaptop", "smartphone\ncellphone"],
                            "itemGroups": [1, 2, 0, 2]}}
        records = {"c" * 20: {"id": "c" * 20, "version": 2,
                             "symptoms": ["it's swollen", "no sensation", "café"],
                             "materials": ["½ cup", "WH-1000XM4"]}}
        prepared = search.prepare_index(index, records)
        self.assertEqual(prepared.pop("structuredTerms"),
                         [[2, ["1", "1000", "1000xm4", "2", "4", "cafe", "cup", "it", "no", "s", "sensation", "swollen", "wh"]]])
        body = json.dumps(prepared, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        # Measured before moving any helper; a search-format change must be an
        # explicit behavior change instead of slipping through this refactor.
        # Source-backed evidence is an additive field; the original postings
        # and token normalization must still match their frozen byte fixture.
        self.assertEqual(hashlib.sha256(body).hexdigest(),
                         "51681dff4c3817a0be08b220c221e8f133efb4a824987d3c55eedb3f6af95ec7")


if __name__ == "__main__":
    unittest.main()
