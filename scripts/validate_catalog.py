#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["lxml==6.1.3"]
# ///
"""Audit the completed real catalog; write an inspectable local quality report."""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

from lxml import html

ROOT = Path(__file__).resolve().parent.parent / "catalog-data"
RUNTIME = ROOT / "runtime"


def main():
    manifest = json.loads((RUNTIME / "manifest.json").read_bytes())
    index_bytes = (RUNTIME / "index.json").read_bytes()
    index = json.loads(index_bytes)
    assert manifest["runtimeComplete"] and manifest["archiveChecksumVerified"] and manifest["zimChecksumVerified"]
    assert manifest["revision"] == index["revision"]
    assert len(index["items"]) == manifest["indexedItems"]
    assert gzip.decompress((RUNTIME / "index.json.gz").read_bytes()) == index_bytes
    ids = {row[0] for row in index["items"]}
    assert len(ids) == len(index["items"]), "Duplicate index IDs"
    categories = {category["id"] for category in index["categories"]}
    media_files = {entry.name for entry in (RUNTIME / "media").iterdir() if entry.is_file()}
    assert len(media_files) == manifest["uniqueMediaFiles"], "Media count differs from complete source extraction"
    allowed = {"a", "abbr", "b", "blockquote", "br", "caption", "code", "dd", "del", "details", "div", "dl", "dt", "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "kbd", "li", "ol", "p", "pre", "s", "section", "small", "span", "strong", "sub", "summary", "sup", "table", "tbody", "td", "th", "thead", "tr", "u", "ul"}

    def audit(row):
        identifier, title, category, kind, device = row
        body = (RUNTIME / "guides" / f"{identifier}.json").read_bytes()
        assert gzip.decompress((RUNTIME / "guides" / f"{identifier}.json.gz").read_bytes()) == body, identifier
        detail = json.loads(body)
        assert [detail[key] for key in ("id", "title", "category", "kind", "device")] == row, identifier
        assert category in categories and detail["license"]["name"] == "CC BY-NC-SA 3.0" and detail["attribution"], identifier
        assert urlsplit(detail["sourceUrl"]).hostname == "www.ifixit.com", identifier
        tree = html.fragment_fromstring(detail["html"], create_parent="div")
        assert tree.text_content().strip() or tree.xpath(".//img"), f"Empty article {identifier}"
        references = set(detail.get("images", []))
        for element in tree.iter():
            if not isinstance(element.tag, str):
                raise AssertionError(f"Unexpected HTML node in {identifier}")
            assert element.tag in allowed, (identifier, element.tag)
            assert not any(name.lower().startswith("on") or name.lower() in {"style", "srcdoc"} for name in element.attrib), identifier
            if element.tag == "img":
                references.add(element.get("src", ""))
            if element.tag == "a" and element.get("href"):
                href = element.get("href")
                assert href.startswith("/?guide=") or urlsplit(href).scheme == "https", (identifier, href)
                if href.startswith("/?guide="):
                    assert href.removeprefix("/?guide=") in ids, (identifier, href)
        for reference in references:
            assert reference.startswith("/media/") and reference.removeprefix("/media/") in media_files, (identifier, reference)
        assert all(item["id"] in ids for item in detail.get("related", [])), identifier
        return category, kind, len(references), len(body)

    category_counts, kind_counts = Counter(), Counter()
    reference_count = json_bytes = 0
    with ThreadPoolExecutor(max_workers=4) as workers:
        # map is consumed immediately; bounded rows are already the compact index.
        for category, kind, references, size in workers.map(audit, index["items"], chunksize=64):
            category_counts[category] += 1
            kind_counts[kind] += 1
            reference_count += references
            json_bytes += size
    assert category_counts == Counter(manifest["categoryCounts"]), "Category totals differ"
    database = sqlite3.connect(f"file:{ROOT / 'catalog.sqlite'}?mode=ro", uri=True)
    assert database.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert database.execute("SELECT count(*) FROM items").fetchone()[0] == len(ids)

    samples = []
    for category in sorted(categories):
        rows = database.execute("SELECT id,source_path,title FROM items WHERE category=? AND kind='guide' ORDER BY id LIMIT 4", (category,)).fetchall()
        for identifier, source_path, title in rows:
            scratch = sqlite3.connect(f"file:{ROOT / 'work.sqlite'}?mode=ro", uri=True)
            source = scratch.execute("SELECT html FROM pages WHERE path=?", (source_path,)).fetchone()[0]
            scratch.close()
            original = html.fromstring(source)
            detail = json.loads((RUNTIME / "guides" / f"{identifier}.json").read_bytes())
            normalized = html.fragment_fromstring(detail["html"], create_parent="div")
            source_steps = original.xpath("//*[contains(concat(' ',normalize-space(@class),' '),' step-title ')]")
            normalized_text = " ".join(normalized.text_content().split())
            source_lines = original.xpath("//*[contains(concat(' ',normalize-space(@class),' '),' step-lines ')]//p")
            for paragraph in source_lines:
                text = " ".join(paragraph.text_content().split())
                assert not text or text in normalized_text, (identifier, "Missing instruction", text[:80])
            conclusion = original.xpath("//*[contains(concat(' ',normalize-space(@class),' '),' conclusionText ')]")
            for element in conclusion:
                text = " ".join(element.text_content().split())
                assert not text or text in normalized_text, (identifier, "Missing conclusion")
            samples.append({"id": identifier, "title": title, "category": category, "sourceSteps": len(source_steps), "sourceInstructionParagraphs": len(source_lines), "instructionsAndConclusionRetained": True})
    database.close()
    report = {"revision": index["revision"], "auditedItems": len(ids), "categoryCounts": dict(category_counts), "kindCounts": dict(kind_counts), "uniqueMediaFiles": len(media_files), "articleMediaReferences": reference_count, "guideJsonBytes": json_bytes, "allJsonAndGzipMatch": True, "allInternalReferencesResolve": True, "allHtmlAllowlisted": True, "sqliteIntegrity": "ok", "instructionSamples": samples}
    (ROOT / "quality-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "instructionSamples"}, indent=2))
    print(f"Source instruction/conclusion samples passed: {len(samples)}")


if __name__ == "__main__":
    main()
