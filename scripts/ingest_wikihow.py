#!/usr/bin/env -S uv run --script
# /// script
# requires-python = "==3.12.*"
# dependencies = []
# ///
"""Stream the pinned complete wikiHow Large CSV into a reusable source runtime."""
from __future__ import annotations

import argparse
import csv
import fcntl
import html
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote

from ingest_common import (CatalogWriter, classify, completed, download, ingestion_key,
                           short_id, verify)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(__file__).with_name("wikihow-source.json")


def configuration():
    return json.loads(CONFIG.read_bytes())


def rows(path, allow_invalid=False):
    csv.field_size_limit(16 * 1024**2)
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if reader.fieldnames != ["headline", "title", "text"]:
            raise ValueError("wikiHow CSV header differs from pinned headline,title,text schema")
        for number, row in enumerate(reader, 1):
            if not allow_invalid and (None in row or any(value is None for value in row.values())):
                raise ValueError("Malformed wikiHow CSV row " + str(number))
            yield number, row


def record_for(number, row, config):
    raw_title = row["title"]
    title = " ".join(raw_title.split())
    if not title or not row["text"].strip():
        return None
    # Numbered method variants remain distinguishable in the original dataset
    # title and source row; do not guess away model/number intent from titles.
    slug = re.sub(r"^how to\s+", "", title, flags=re.I).replace(" ", "-")
    source_url = "https://www.wikihow.com/" + quote(slug, safe="-")
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", row["text"]) if part.strip()]
    body = "<article>" + "".join("<p>" + html.escape(part).replace("\n", "<br>") + "</p>" for part in paragraphs) + "</article>"
    category = classify(title)
    return {"id": short_id(config["id"], str(number) + ":" + raw_title),
            "title": title, "category": category, "kind": "guide", "device": "",
            "sourceId": config["id"], "sourceName": "wikiHow", "sourceUrl": config["url"],
            "sourceRevision": config["revision"], "sourceRow": number, "sourceTitle": raw_title,
            "sourceArticleUrlCandidate": source_url, "sourceUrlInferred": True, "datasetUrl": config["url"],
            "sourceHeadline": row["headline"], "license": config["license"],
            "licenseNotice": config["licenseNotice"], "attribution": config["attribution"],
            "summary": " ".join(row["headline"].split())[:500], "html": body,
            "images": [], "mediaSources": [], "related": []}


def build(data_dir, archive, config=None):
    config = configuration() if config is None else config
    verify(archive, config)
    key = ingestion_key(__file__, config)
    excluded = malformed = total = 0
    quarantine_path = Path(data_dir) / 'quarantine.jsonl.building'
    with CatalogWriter(data_dir, config, key) as writer, quarantine_path.open('w') as quarantine:
        for number, row in rows(archive, allow_invalid=True):
            total += 1
            if None in row or any(value is None for value in row.values()):
                malformed += 1
                quarantine.write(json.dumps({'sourceRow': number, 'reason': 'CSV field count differs from header', 'fields': row}, ensure_ascii=False) + '\n')
                continue
            record = record_for(number, row, config)
            if record is None:
                excluded += 1
                continue
            writer.add(record, str(number))
            if total % 10000 == 0:
                print(f"wikiHow: {total:,} CSV rows streamed", file=sys.stderr, flush=True)
        quarantine.flush()
        quarantine_path.replace(Path(data_dir) / 'quarantine.jsonl')
        return writer.finish({"sourceRows": total, "excludedEmptyRows": excluded,
                              "excludedMalformedRows": malformed, "quarantine": "quarantine.jsonl (build-only)",
                              "rawTextPreservation": "Exact original CSV retained; sourceRow maps every guide to original title/text/headline.",
                              "licenseNotice": config["licenseNotice"]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "catalog-data/build/wikihow")
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument("--download-only", action="store_true")
    operations.add_argument("--build-only", action="store_true")
    args = parser.parse_args()
    config = configuration()
    data = args.data_dir.resolve()
    data.mkdir(parents=True, exist_ok=True)
    with (data / ".ingest.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not args.download_only and completed(data, ingestion_key(__file__, config), config):
            print("Reusing completed wikiHow source", file=sys.stderr)
            return
        archive = download(data, config, args.build_only)
        if not args.download_only:
            result = build(data, archive, config)
            print(json.dumps({key: result[key] for key in ("sourceRows", "indexedItems", "excludedEmptyRows", "excludedMalformedRows")}))


if __name__ == "__main__":
    main()
