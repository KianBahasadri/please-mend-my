import tempfile
from pathlib import Path
import unittest

from diagram_credits import END, START, render, update_credits


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

    def test_bad_source_urls_fail_before_overwriting_page(self):
        manifest = self.manifest()
        manifest["archetypes"][0]["attribution"]["source"] = "javascript:alert(1)"
        with tempfile.TemporaryDirectory() as directory:
            page = Path(directory) / "credits.html"
            original = "before" + START + "\n" + END + "after"
            page.write_text(original)
            with self.assertRaises(ValueError):
                update_credits(page, manifest)
            self.assertEqual(page.read_text(), original)

    def test_regeneration_is_deterministic_and_keeps_existing_credits(self):
        with tempfile.TemporaryDirectory() as directory:
            page = Path(directory) / "credits.html"
            page.write_text("existing bicycle credits\n" + START + "\n" + END + "\nfont notices")
            update_credits(page, self.manifest())
            first = page.read_bytes()
            update_credits(page, self.manifest())
            self.assertEqual(page.read_bytes(), first)
            self.assertTrue(page.read_text().startswith("existing bicycle credits"))
            self.assertTrue(page.read_text().endswith("font notices"))


if __name__ == "__main__":
    unittest.main()
