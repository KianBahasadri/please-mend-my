"""The same v2 contract protects API responses, cached outputs, and publication."""
from copy import deepcopy
import unittest

from structured_guides import (CONTENT_SCHEMA, cache_key, make_v2, raw_digest,
                               source_assets, validate_content, validate_v2)


def raw_guide():
    image = "/media/" + "a" * 64 + ".jpg"
    return {"id": "1" * 20, "title": "Fix a hinge", "category": "household",
            "kind": "guide", "device": "Door", "summary": "Old source summary",
            "sourceUrl": "https://example.org/hinge", "sourceName": "Source author",
            "license": {"name": "CC BY-SA", "url": "https://example.org/license"},
            "attribution": "Original author", "publicationDate": "2004-07-15",
            "provenance": {"archiveSha256": "b" * 64}, "images": [image], "related": [],
            "html": '<h2>Warning</h2><p>Do not remove the door.</p><img src="' + image
                    + '"><a href="https://example.org/manual">Manual</a>'}


def content_guide():
    return {"summary": "Lubricate the hinge.", "tools": ["Cloth"], "materials": ["Oil"],
            "safety": [{"level": "warning", "text": "Do not remove the door."}],
            "symptoms": ["Squeaky hinge"],
            "steps": [{"title": "Clean the hinge", "body": "Wipe it with the cloth.",
                       "warnings": [], "images": ["/media/" + "a" * 64 + ".jpg"], "code": ""}],
            "references": [{"title": "Manual", "url": "https://example.org/manual"}]}


class StructuredContractTests(unittest.TestCase):
    def test_preserves_source_identity_dates_credit_and_raw_input(self):
        raw, content = raw_guide(), content_guide()
        before = deepcopy(raw)
        result = make_v2(raw, content, "test/model", config={"temperature": 0})
        validate_v2(result, raw)
        self.assertEqual(raw, before)
        for field in ("sourceUrl", "sourceName", "license", "attribution", "publicationDate", "provenance"):
            self.assertEqual(result[field], raw[field])
        self.assertEqual(result["processing"]["rawSha256"], raw_digest(raw))
        self.assertEqual(result["summary"], content["summary"])
        self.assertNotIn("html", result)

    def test_digest_is_stable_and_covers_every_generation_input(self):
        raw = raw_guide()
        key = cache_key(raw, "test/model", config={"temperature": 0, "promptSha256": "abc"})
        self.assertEqual(key, cache_key(dict(reversed(list(raw.items()))), "test/model",
                                       config={"promptSha256": "abc", "temperature": 0}))
        variations = [cache_key({**raw, "html": "Changed"}, "test/model", config={"temperature": 0, "promptSha256": "abc"}),
                      cache_key(raw, "different/model", config={"temperature": 0, "promptSha256": "abc"}),
                      cache_key(raw, "test/model", "stage3-next", config={"temperature": 0, "promptSha256": "abc"}),
                      cache_key(raw, "test/model", config={"temperature": 1, "promptSha256": "abc"})]
        self.assertNotIn(key, variations)
        with self.assertRaises(ValueError):
            make_v2(raw, content_guide(), "test/model", key="0" * 64)

    def test_missing_extra_and_wrongly_typed_fields_rejected(self):
        for change in (lambda c: c.pop("safety"), lambda c: c.update(html="<script>"),
                       lambda c: c.update(steps="text"), lambda c: c["steps"][0].update(body=""),
                       lambda c: c["safety"][0].update(level="whatever")):
            with self.subTest(change=change):
                content = content_guide()
                change(content)
                with self.assertRaises(ValueError):
                    validate_content(content)

    def test_rejects_stale_raw_and_tampered_metadata_or_cache_inputs(self):
        raw = raw_guide()
        result = make_v2(raw, content_guide(), "test/model", config={"temperature": 0})
        for path, value in (("title", "Invented title"), ("license", {}), ("id", "2" * 20),
                            ("html", "<b>legacy</b>"), ("version", True), ("extra", "unknown")):
            changed = deepcopy(result)
            changed[path] = value
            with self.subTest(field=path), self.assertRaises(ValueError):
                validate_v2(changed, raw)
        for field, value in (("cacheKey", "0" * 64), ("rawSha256", "0" * 64),
                             ("promptVersion", "old"), ("configuration", {"temperature": 1})):
            changed = deepcopy(result)
            changed["processing"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_v2(changed, raw)
        with self.assertRaises(ValueError):
            validate_v2(result, {**raw, "html": "A changed safety instruction"})

    def test_source_assets_are_whitelisted_not_invented(self):
        raw, content = raw_guide(), content_guide()
        self.assertIn(raw["sourceUrl"], source_assets(raw)["references"])
        for value in ("javascript:alert(1)", "https://example.org/invented", "//evil.org/page"):
            changed = deepcopy(content)
            changed["references"][0]["url"] = value
            with self.subTest(url=value), self.assertRaises(ValueError):
                make_v2(raw, changed, "test/model")
        for value in ("https://example.org/image.jpg", "/media/" + "c" * 64 + ".png", "/media/../secret"):
            changed = deepcopy(content)
            changed["steps"][0]["images"] = [value]
            with self.subTest(image=value), self.assertRaises(ValueError):
                make_v2(raw, changed, "test/model")

    def test_nonprocedural_source_can_have_no_steps(self):
        content = content_guide()
        content["steps"] = []
        validate_v2(make_v2(raw_guide(), content, "test/model"), raw_guide())

    def test_all_schema_objects_reject_extra_fields_and_require_all_properties(self):
        def visit(schema):
            if schema["type"] == "object":
                self.assertIs(schema["additionalProperties"], False)
                self.assertEqual(set(schema["properties"]), set(schema["required"]))
                for value in schema["properties"].values():
                    visit(value)
            elif schema["type"] == "array":
                visit(schema["items"])
        visit(CONTENT_SCHEMA)


if __name__ == "__main__":
    unittest.main()
