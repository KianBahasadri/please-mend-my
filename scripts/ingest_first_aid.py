#!/usr/bin/env -S uv run --script
# /// script
# requires-python = "==3.12.*"
# dependencies = ["pymupdf==1.28.2"]
# ///
"""Preserve the pinned U.S. military First Aid PDF and make rough chapter entries.

This is source ingestion, not a medical-content rewrite. The exact PDF, every
page's text, the complete outline, source metadata, and page images survive for
later processing. Local and EC2 builds run this same pinned script.
"""
from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import html
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import pymupdf

from ingest_common import TEXT_HELPERS, json_bytes, normalize

ROOT = Path(__file__).resolve().parent.parent
SOURCE_ID = "first-aid"
ARCHIVE_NAME = "UsArmyFirstAidManual.pdf"
ARCHIVE_URL = "https://archive.org/download/UsArmyFirstAidManualFm4-25.11/" + ARCHIVE_NAME
MIRROR_URL = "https://ia801406.us.archive.org/34/items/UsArmyFirstAidManualFm4-25.11/" + ARCHIVE_NAME
METADATA_URL = "https://archive.org/metadata/UsArmyFirstAidManualFm4-25.11"
EXPECTED_BYTES = 2621944
EXPECTED_SHA256 = "95fb01c941cdb385c152d045081ae011b59805931524e03192e953dc4e57af2f"
EXPECTED_SHA1 = "e68f3196fe7e310cc96dbdb9ecf9184dd521e83f"
EXPECTED_MD5 = "dab078c0b7a42e9a241f3a4602320751"
EXPECTED_PAGES = 227
PYMUPDF_VERSION = "1.28.2"
RENDER_DPI = 96
PUBLICATION_DATE = "2002-12-23"
CHANGE_DATE = "2004-07-15"
EDITION = "2002; Change 1, 2004"
REVISION = "first_aid_fm4-25.11_2002_c1_2004_" + EXPECTED_SHA256[:12]
LICENSE = {"name": "Public domain (U.S. federal government)",
           "url": "https://www.copyright.gov/title17/92chap1.html#105"}
ATTRIBUTION = "U.S. Departments of the Army, the Navy, and the Air Force, and Commandant, Marine Corps."
CATEGORIES = [{"id": "health", "name": "Health & medicine"}]
FIELDS = ["id", "title", "category", "kind", "device"]


def log(message):
    print(message, file=sys.stderr, flush=True)


def atomic_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def source_descriptor():
    return {"id": SOURCE_ID, "name": "U.S. military First Aid (" + EDITION + ")",
            "url": ARCHIVE_URL, "archive": ARCHIVE_NAME, "sourceFormat": "pdf",
            "publicationDate": PUBLICATION_DATE, "changeDate": CHANGE_DATE,
            "edition": EDITION, "license": LICENSE, "attribution": ATTRIBUTION}


def script_fingerprint():
    digest = hashlib.sha256(Path(__file__).read_bytes())
    digest.update(b"\0" + TEXT_HELPERS.read_bytes() + b"\0")
    digest.update((PYMUPDF_VERSION + "\0" + str(RENDER_DPI)).encode())
    return digest.hexdigest()


def verify_pdf(path):
    """Strongly verify source bytes; unrelated to a ZIM container checksum."""
    hashes = {name: hashlib.new(name) for name in ("sha256", "sha1", "md5")}
    size = 0
    with Path(path).open("rb") as source:
        while chunk := source.read(1 << 20):
            size += len(chunk)
            for digest in hashes.values():
                digest.update(chunk)
    actual = {name: digest.hexdigest() for name, digest in hashes.items()}
    if (size != EXPECTED_BYTES or actual["sha256"] != EXPECTED_SHA256
            or actual["sha1"] != EXPECTED_SHA1 or actual["md5"] != EXPECTED_MD5):
        raise ValueError("First Aid PDF differs from its pinned size/checksums; refusing ingestion.")
    return actual["sha256"]


def validate_archive_metadata(metadata):
    entries = [entry for entry in metadata.get("files", []) if entry.get("name") == ARCHIVE_NAME]
    if len(entries) != 1:
        raise ValueError("Internet Archive metadata does not identify the pinned PDF.")
    entry = entries[0]
    if (str(entry.get("size")) != str(EXPECTED_BYTES) or entry.get("sha1") != EXPECTED_SHA1
            or entry.get("md5") != EXPECTED_MD5):
        raise ValueError("Internet Archive metadata differs from the pinned PDF.")


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "please-mend-my offline-source-ingestion/1"})
    return urllib.request.urlopen(request, timeout=45)


def download_pdf(data_dir, build_only=False):
    source_dir = Path(data_dir) / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    path, metadata_path = source_dir / ARCHIVE_NAME, source_dir / "archive-metadata.json"
    if not metadata_path.exists() and not build_only:
        with fetch(METADATA_URL) as response:
            metadata_bytes = response.read(8 << 20)
        metadata = json.loads(metadata_bytes)
        validate_archive_metadata(metadata)
        atomic_write(metadata_path, metadata_bytes)
    elif metadata_path.exists():
        validate_archive_metadata(json.loads(metadata_path.read_bytes()))
    if path.exists():
        verify_pdf(path)
        return path
    if build_only:
        raise FileNotFoundError("Download the pinned First Aid PDF before --build-only.")
    errors = []
    partial = source_dir / (ARCHIVE_NAME + ".part")
    for attempt in range(2):
        for url in (ARCHIVE_URL, MIRROR_URL):
            try:
                log("Downloading First Aid PDF: " + url)
                with fetch(url) as response, partial.open("wb") as output:
                    size = 0
                    while chunk := response.read(1 << 20):
                        size += len(chunk)
                        if size > EXPECTED_BYTES:
                            raise ValueError("First Aid download exceeds its pinned size.")
                        output.write(chunk)
                verify_pdf(partial)
                partial.replace(path)
                return path
            except (OSError, ValueError) as error:
                errors.append(str(error))
                partial.unlink(missing_ok=True)
        if attempt == 0:
            time.sleep(1)
    raise RuntimeError("Unable to fetch the pinned First Aid PDF: " + "; ".join(errors))


def chapter_ranges(outline, page_count):
    """Use top-level real PDF bookmarks; retain unbookmarked front matter too."""
    valid = [(int(level), str(title).strip(), int(page)) for level, title, page, *_ in outline
             if isinstance(page, int) and 1 <= page <= page_count and str(title).strip()]
    if not valid:
        return [{"heading": "First Aid manual", "startPage": 1, "endPage": page_count}]
    level = min(entry[0] for entry in valid)
    starts = {}
    for _, title, page in sorted((entry for entry in valid if entry[0] == level), key=lambda entry: entry[2]):
        starts[page] = starts.get(page, "") + (" / " if page in starts else "") + title
    if 1 not in starts:
        starts[1] = "Front matter"
    ordered = sorted(starts)
    return [{"heading": starts[page], "startPage": page,
             "endPage": ordered[position + 1] - 1 if position + 1 < len(ordered) else page_count}
            for position, page in enumerate(ordered)]


def section_aliases(section, outline):
    text = " ".join(str(title) for _, title, page, *_ in outline
                    if isinstance(page, int) and section["startPage"] <= page <= section["endPage"])
    heading = section["heading"].lower()
    additions = []
    for trigger, words in (
        ("basic measures", "airway breathing choking bleeding wounds tourniquet shock"),
        ("specific injuries", "head neck chest abdominal wounds burns dressings bandages"),
        ("fractures", "broken bones splints immobilization"),
        ("climatic", "heat cold heatstroke frostbite hypothermia"),
        ("bites and stings", "snakebite animal bites insect stings"),
        ("psychological", "stress psychological reactions"),
        ("transportation", "rescue transportation casualty carrying"),
    ):
        if trigger in heading:
            additions.append(words)
    return normalize(" ".join(["first aid emergency care", text, *additions]))


def cached_runtime(data_dir):
    """Only inspect small completion metadata and fixed paths, never the corpus."""
    data_dir = Path(data_dir)
    runtime = data_dir / "runtime"
    try:
        manifest = json.loads((runtime / "manifest.json").read_bytes())
        if (manifest.get("runtimeComplete") is not True or manifest.get("sourceFormat") != "pdf"
                or manifest.get("documentVerified") is not True
                or manifest.get("archiveChecksumVerified") is not True
                or manifest.get("archiveSha256") != EXPECTED_SHA256
                or manifest.get("expectedSha256") != EXPECTED_SHA256
                or manifest.get("scriptSha256") != script_fingerprint()
                or manifest.get("indexedItems", 0) < 1
                or manifest.get("searchEnrichment", {}).get("version") != 1
                or (runtime / "index.json").stat().st_size != manifest.get("indexBytes")
                or (runtime / "index.json.gz").stat().st_size != manifest.get("indexGzipBytes")
                or not (runtime / "guides").is_dir() or not (runtime / "media").is_dir()
                or (data_dir / "source" / ARCHIVE_NAME).stat().st_size != EXPECTED_BYTES
                or not (data_dir / "source/pages.jsonl").is_file()
                or not (data_dir / "source/outline.json").is_file()):
            return None
        return runtime
    except (OSError, ValueError, KeyError, TypeError):
        return None


def build_runtime(data_dir, pdf_path):
    data_dir = Path(data_dir).resolve()
    source_dir = data_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    verified_hash = verify_pdf(pdf_path)
    stage = Path(tempfile.mkdtemp(prefix=".runtime-", dir=data_dir))
    runtime = data_dir / "runtime"
    raw_stage = Path(tempfile.mkdtemp(prefix=".extract-", dir=source_dir))
    try:
        (stage / "guides").mkdir()
        (stage / "media").mkdir()
        atomic_write(stage / "manifest.json", json_bytes({"version": 1, "runtimeComplete": False}))
        page_records = []
        media_bytes = 0
        with pymupdf.open(pdf_path) as document:
            if not document.is_pdf or document.needs_pass or len(document) != EXPECTED_PAGES:
                raise ValueError("The pinned First Aid PDF cannot be read completely.")
            outline = document.get_toc()
            sections = chapter_ranges(outline, len(document))
            metadata = dict(document.metadata)
            with (raw_stage / "pages.jsonl").open("wb") as raw_pages:
                for offset in range(len(document)):
                    page = document[offset]
                    text = page.get_text("text", sort=True)
                    image = page.get_pixmap(dpi=RENDER_DPI, alpha=False).tobytes("png")
                    filename = hashlib.sha256(image).hexdigest() + ".png"
                    media_path = stage / "media" / filename
                    if not media_path.exists():
                        media_path.write_bytes(image)
                        media_bytes += len(image)
                    record = {"page": offset + 1, "text": text, "image": "/media/" + filename}
                    raw_pages.write(json_bytes(record) + b"\n")
                    page_records.append(record)
                    if (offset + 1) % 50 == 0:
                        log(f"First Aid: preserved {offset + 1}/{len(document)} PDF pages")
        atomic_write(raw_stage / "outline.json", json_bytes(outline))
        document_metadata = {"source": source_descriptor(), "archiveSha256": verified_hash,
                             "archiveBytes": EXPECTED_BYTES, "pageCount": len(page_records),
                             "pdfMetadata": metadata, "publicationDate": PUBLICATION_DATE,
                             "changeDate": CHANGE_DATE, "sections": sections,
                             "extractor": "PyMuPDF " + PYMUPDF_VERSION, "renderDpi": RENDER_DPI}
        atomic_write(raw_stage / "document.json", json_bytes(document_metadata))
        rows, aliases, total_json = [], [], 0
        identifiers = [hashlib.sha256((SOURCE_ID + "\0" + str(section["startPage"])).encode()).hexdigest()[:20]
                       for section in sections]
        for section, identifier in zip(sections, identifiers):
            heading = section["heading"].title()
            title = heading + " — First Aid (" + EDITION + ")"
            start, end = section["startPage"], section["endPage"]
            pages = page_records[start - 1:end]
            fragments = []
            for page in pages:
                fragments.append('<section><h2>PDF page ' + str(page["page"]) + '</h2><pre style="white-space:pre-wrap">'
                                 + html.escape(page["text"]) + '</pre><figure><img src="' + page["image"]
                                 + '" alt="First Aid manual, PDF page ' + str(page["page"])
                                 + '" loading="lazy" decoding="async"></figure></section>')
            record = {"id": identifier, "title": title, "category": "health", "kind": "article", "device": "First aid",
                      "sourceName": "U.S. military", "sourceUrl": ARCHIVE_URL + "#page=" + str(start),
                      "license": LICENSE, "attribution": ATTRIBUTION,
                      "publicationDate": PUBLICATION_DATE, "changeDate": CHANGE_DATE, "sourceFormat": "pdf",
                      "summary": "First Aid, FM 4-25.11, 23 December 2002; Change 1, 15 July 2004. PDF pages "
                                 + str(start) + "–" + str(end) + ".",
                      "html": "".join(fragments), "images": [page["image"] for page in pages], "related": [],
                      "provenance": {"sourceId": SOURCE_ID, "archiveSha256": verified_hash,
                                     "startPage": start, "endPage": end, "heading": section["heading"]}}
            payload = json_bytes(record)
            atomic_write(stage / "guides" / (identifier + ".json"), payload)
            atomic_write(stage / "guides" / (identifier + ".json.gz"), gzip.compress(payload, compresslevel=6, mtime=0))
            rows.append([record[field] for field in FIELDS])
            aliases.append(section_aliases(section, outline))
            total_json += len(payload)
        ordered = sorted(zip(rows, aliases), key=lambda item: (item[0][1].casefold(), item[0][0]))
        groups = [""] + sorted(set(aliases) - {""})
        group_ids = {group: index for index, group in enumerate(groups)}
        search = {"version": 1, "normalization": "NFKD-no-marks-lower", "groups": groups,
                  "itemGroups": [group_ids[alias] for _, alias in ordered],
                  "enrichedItems": len(rows), "sources": [{"id": SOURCE_ID, "url": ARCHIVE_URL,
                        "basis": "Source bookmark headings and curated chapter synonyms"}]}
        index = {"version": 1, "revision": REVISION, "source": source_descriptor(), "categories": CATEGORIES,
                 "fields": FIELDS, "items": [row for row, _ in ordered], "search": search}
        body = json_bytes(index)
        packed = gzip.compress(body, compresslevel=9, mtime=0)
        atomic_write(stage / "index.json", body)
        atomic_write(stage / "index.json.gz", packed)
        media_count = len({record["image"] for record in page_records})
        manifest = {"version": 1, "revision": REVISION, "sourceId": SOURCE_ID, "sourceFormat": "pdf",
                    "source": source_descriptor(), "archiveUrl": ARCHIVE_URL, "archiveBytes": EXPECTED_BYTES,
                    "archiveSha256": verified_hash, "expectedSha256": EXPECTED_SHA256,
                    "archiveChecksumVerified": True, "documentVerified": True,
                    "documentVerification": {"reader": "PyMuPDF " + PYMUPDF_VERSION, "pageCount": len(page_records)},
                    "metadataUrl": METADATA_URL, "scriptSha256": script_fingerprint(),
                    "publicationDate": PUBLICATION_DATE, "changeDate": CHANGE_DATE,
                    "indexedItems": len(rows), "categoryCounts": {"health": len(rows)},
                    "uniqueMediaFiles": media_count, "mediaBytes": media_bytes, "guideJsonBytes": total_json,
                    "indexBytes": len(body), "indexGzipBytes": len(packed),
                    "searchEnrichment": {"version": 1, "basis": "Source headings and curated chapter synonyms"},
                    "rawArtifacts": {"pdf": "source/" + ARCHIVE_NAME, "pages": "source/pages.jsonl",
                                     "outline": "source/outline.json", "metadata": "source/document.json"},
                    "transformations": "Every PDF page retained as extracted text and rendered image; chapter boundaries from PDF bookmarks. No medical rewriting or content filtering.",
                    "runtimeComplete": True}
        # Raw source and extraction artifacts remain outside the served runtime.
        for filename in ("pages.jsonl", "outline.json", "document.json"):
            (raw_stage / filename).replace(source_dir / filename)
        atomic_write(stage / "manifest.json", json_bytes(manifest))
        if runtime.exists():
            old_runtime = data_dir / (stage.name + ".previous")
            runtime.rename(old_runtime)
            try:
                stage.rename(runtime)
            except BaseException:
                old_runtime.rename(runtime)
                raise
            shutil.rmtree(old_runtime)
        else:
            stage.rename(runtime)
        log(f"First Aid complete: {len(rows)} entries, {len(page_records)} preserved pages, {media_count} images")
        return runtime
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        shutil.rmtree(raw_stage)


def assert_output_untracked(data_dir):
    try:
        relative = Path(data_dir).resolve().relative_to(ROOT)
    except ValueError:
        return
    result = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--quiet", "--",
                             str(relative / "runtime/index.json")], capture_output=True)
    if result.returncode:
        raise ValueError("PDF source/output inside the checkout must be Git-ignored.")


def ingest(data_dir, download_only=False, build_only=False):
    data_dir = Path(data_dir).resolve()
    assert_output_untracked(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / ".ingest.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another First Aid ingestion uses this directory.") from error
        if not download_only:
            cached = cached_runtime(data_dir)
            if cached:
                log("Reusing completed First Aid source")
                return cached
        path = download_pdf(data_dir, build_only=build_only)
        return path if download_only else build_runtime(data_dir, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "catalog-data/build/first-aid")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--download-only", action="store_true")
    mode.add_argument("--build-only", action="store_true")
    args = parser.parse_args()
    print(ingest(args.data_dir, args.download_only, args.build_only), flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print("First Aid ingestion failed: " + str(error), file=sys.stderr)
        sys.exit(1)
