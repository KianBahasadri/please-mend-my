import unittest

from diagram_credits import render


class DiagramCreditsTest(unittest.TestCase):
    def manifest(self):
        return {"version": 1, "archetypes": [{
            "id": "phone", "label": "Phone", "artUrl": "/assets/art/phone-hash.json",
            "attribution": {"title": "Phone <diagram>", "author": "Alice & Bob",
                "source": "https://example.org/source", "license": "CC BY 3.0",
                "licenseUrl": "https://creativecommons.org/licenses/by/3.0/",
                "changes": "Adapted: <labels> removed; regions grouped."},
        }]}

    def test_selected_art_receives_complete_escaped_static_credits(self):
        output = render(self.manifest())
        for value in ("Phone &lt;diagram&gt;", "Alice &amp; Bob", "https://example.org/source",
                      "https://creativecommons.org/licenses/by/3.0/", "CC BY 3.0",
                      "Adapted: &lt;labels&gt; removed; regions grouped."):
            self.assertIn(value, output)
        self.assertIn('rel="noopener noreferrer"', output)
        self.assertNotIn("<labels>", output)

    def test_list_only_fallbacks_have_no_fabricated_artist_credit(self):
        manifest = self.manifest()
        manifest["archetypes"][0]["artUrl"] = None
        self.assertEqual(render(manifest), "")

    def test_bad_source_urls_fail_before_release_staging(self):
        manifest = self.manifest()
        manifest["archetypes"][0]["attribution"]["source"] = "javascript:alert(1)"
        with self.assertRaises(ValueError):
            render(manifest)

    def test_regeneration_is_deterministic_without_mutating_the_registry(self):
        manifest = self.manifest()
        import copy
        before = copy.deepcopy(manifest)
        first = render(manifest)
        self.assertEqual(render(manifest), first)
        self.assertEqual(manifest, before)
        self.assertIn("Alice &amp; Bob", first)


if __name__ == "__main__":
    unittest.main()
