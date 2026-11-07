#!/usr/bin/env python3
"""Merge completed source catalogs into a new unpublished runtime.

Guide JSON and content-addressed media are hardlinked, never rewritten. Only the
small catalog index and completion manifest are rebuilt. Run prepare_search.py on
the result before publishing; source posting offsets cannot survive reordering.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

FIELDS = ["id", "title", "category", "kind", "device"]
GUIDE_NAME = re.compile(r"([0-9a-f]{20})\.json(\.gz)?\Z")
MEDIA_NAME = re.compile(r"[0-9a-f]{64}(?:\.[a-zA-Z0-9]{1,12})?\Z")


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def regular_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Catalog inputs must be regular files, not symlinks: " + str(path))
    return info


def directory(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("Catalog inputs must be directories, not symlinks: " + str(path))
    return info


def read_json(path):
    regular_file(path)
    body = path.read_bytes()
    return json.loads(body), body


def _source_id(index, manifest):
    source = index["source"]
    identifier = source.get("id") or source.get("sourceId") or manifest.get("sourceId")
    if not identifier:
        # The original iFixit runtime predates an explicit source identifier.
        identifier = "ifixit" if index["revision"].startswith("ifixit_") else index["revision"]
    if not isinstance(identifier, str) or not identifier or "/" in identifier or "\\" in identifier:
        raise ValueError("Each source needs a nonempty stable source identifier.")
    return identifier


def _load_source(path):
    path = Path(path).absolute()
    directory(path)
    directory(path / "guides")
    directory(path / "media")
    index, body = read_json(path / "index.json")
    manifest, _ = read_json(path / "manifest.json")
    regular_file(path / "index.json.gz")
    if (not isinstance(index, dict) or index.get("version") != 1 or index.get("fields") != FIELDS
            or not isinstance(index.get("revision"), str) or not index["revision"]
            or not isinstance(index.get("source"), dict)
            or not isinstance(index.get("items"), list) or not index["items"]
            or not isinstance(index.get("categories"), list)):
        raise ValueError("A source must have a version 1 five-field catalog index: " + str(path))
    if not isinstance(manifest, dict):
        raise ValueError("Source completion manifest must be a JSON object: " + str(path))
    checksum = manifest.get("archiveSha256")
    source_format = manifest.get("sourceFormat", "zim")
    content_verified = ((source_format == "zim" and manifest.get("zimChecksumVerified") is True)
                        or (source_format == "pdf" and manifest.get("documentVerified") is True))
    enrichment = manifest.get("searchEnrichment")
    if (manifest.get("version") != 1 or manifest.get("runtimeComplete") is not True
            or manifest.get("revision") != index["revision"]
            or manifest.get("archiveChecksumVerified") is not True
            or not content_verified
            or not isinstance(checksum, str) or not re.fullmatch("[0-9a-f]{64}", checksum)
            or checksum != manifest.get("expectedSha256")
            or manifest.get("indexedItems") != len(index["items"])
            or manifest.get("indexBytes") != len(body)
            or manifest.get("indexGzipBytes") != (path / "index.json.gz").stat().st_size
            or not isinstance(enrichment, dict) or enrichment.get("version") != 1):
        raise ValueError("Source catalog must be complete, verified, and alias-enriched: " + str(path))
    if gzip.decompress((path / "index.json.gz").read_bytes()) != body:
        raise ValueError("Source index gzip differs from its JSON: " + str(path))
    index_hash = hashlib.sha256(body).hexdigest()
    recorded_hash = manifest.get("searchIndex", {}).get("indexSha256")
    if recorded_hash and recorded_hash != index_hash:
        raise ValueError("Source prepared-index checksum differs from its JSON: " + str(path))
    search = index.get("search", {})
    if not isinstance(search, dict):
        raise ValueError("Source search aliases are missing or invalid: " + str(path))
    groups, references = search.get("groups"), search.get("itemGroups")
    if (search.get("version") != 1 or search.get("normalization") != "NFKD-no-marks-lower"
            or not isinstance(groups, list) or not groups or groups[0] != ""
            or not all(isinstance(group, str) for group in groups)
            or not isinstance(references, list) or len(references) != len(index["items"])
            or not all(type(value) is int and 0 <= value < len(groups) for value in references)):
        raise ValueError("Source search aliases are missing or invalid: " + str(path))
    categories = {}
    for category in index["categories"]:
        if (not isinstance(category, dict) or not isinstance(category.get("id"), str)
                or not category["id"] or not isinstance(category.get("name"), str) or not category["name"]
                or category["id"] in categories):
            raise ValueError("Source category definitions are invalid: " + str(path))
        categories[category["id"]] = category
    ids = set()
    for row in index["items"]:
        if (not isinstance(row, list) or len(row) != 5 or not all(isinstance(value, str) for value in row)
                or not re.fullmatch("[0-9a-f]{20}", row[0]) or row[0] in ids
                or not row[1] or row[2] not in categories or not row[3]):
            raise ValueError("Source contains invalid or duplicate catalog rows: " + str(path))
        ids.add(row[0])
    return {"path": path, "index": index, "manifest": manifest, "indexSha256": index_hash,
            "id": _source_id(index, manifest), "ids": ids, "format": source_format}


def _hardlink(source, destination):
    before = regular_file(source)
    if before.st_dev != destination.parent.stat().st_dev:
        raise ValueError("Source and merged runtime must share a filesystem for hardlinks.")
    os.link(source, destination, follow_symlinks=False)
    linked = regular_file(destination)
    if (before.st_dev, before.st_ino) != (linked.st_dev, linked.st_ino):
        raise ValueError("Source file changed while linking: " + str(source))
    return before.st_size


def _same_bytes(left, right):
    left_info, right_info = regular_file(left), regular_file(right)
    if left_info.st_size != right_info.st_size:
        return False
    if (left_info.st_dev, left_info.st_ino) == (right_info.st_dev, right_info.st_ino):
        return True
    # Read only colliding names, not every image in either corpus. Content-addressed
    # inputs should agree; matching lengths alone cannot establish that they do.
    with left.open("rb") as first, right.open("rb") as second:
        while chunk := first.read(1 << 20):
            if chunk != second.read(1 << 20):
                return False
        return not second.read(1)


def _source_descriptor(source):
    descriptor = dict(source["index"]["source"])
    descriptor.update({"id": source["id"], "revision": source["index"]["revision"],
                       "sourceFormat": source["format"],
                       "archiveSha256": source["manifest"]["archiveSha256"],
                       "indexSha256": source["indexSha256"],
                       "indexedItems": len(source["index"]["items"])})
    return descriptor


def merge_catalogs(source_runtimes, output_runtime):
    """Return a new completed alias-enriched runtime; never mutate source files.

    The destination must be absent or empty and share a filesystem with every
    input. A sibling temporary directory is renamed into place only on success.
    This validates/link-copies structure, not a content audit of guide payloads.
    """
    if not source_runtimes:
        raise ValueError("At least one source runtime is required.")
    output = Path(output_runtime).absolute()
    if output.is_symlink():
        raise ValueError("Merged output cannot be a symlink.")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Merged output must be a new or empty unpublished directory.")
    sources = sorted((_load_source(path) for path in source_runtimes), key=lambda source: source["id"])
    if len({source["id"] for source in sources}) != len(sources):
        raise ValueError("Duplicate source identifiers cannot be merged.")
    for source in sources:
        source_path, output_path = source["path"].resolve(), output.resolve()
        if source_path == output_path or source_path in output_path.parents or output_path in source_path.parents:
            raise ValueError("Source and output directories must not overlap.")
    ids, categories, rows, group_texts = set(), {}, [], {""}
    for source in sources:
        index = source["index"]
        if ids & source["ids"]:
            raise ValueError("Catalog item ID collision across sources.")
        ids.update(source["ids"])
        for category in index["categories"]:
            previous = categories.get(category["id"])
            if previous is not None and previous != category:
                raise ValueError("Conflicting category definitions: " + category["id"])
            categories[category["id"]] = category
        groups = index["search"]["groups"]
        for row, reference in zip(index["items"], index["search"]["itemGroups"]):
            group = groups[reference]
            group_texts.add(group)
            rows.append((row, group))
    rows.sort(key=lambda item: (item[0][1].casefold(), item[0][0]))
    groups = [""] + sorted(group_texts - {""})
    group_ids = {group: position for position, group in enumerate(groups)}
    references = [group_ids[group] for _, group in rows]
    descriptors = [_source_descriptor(source) for source in sources]
    pin_body = json_bytes([{key: source[key] for key in ("id", "revision", "archiveSha256", "indexSha256")}
                           for source in descriptors])
    digest = hashlib.sha256(pin_body).hexdigest()
    revision = "combined_" + digest[:24]
    provenance, enrichment_sources = [], {}
    for source in sources:
        metadata = {key: value for key, value in source["index"]["search"].items()
                    if key not in {"groups", "itemGroups", "inverted"}}
        provenance.append({"sourceId": source["id"], "metadata": metadata})
        for reference in metadata.get("sources", []):
            enrichment_sources[json_bytes(reference)] = reference
    search = {"version": 1, "normalization": "NFKD-no-marks-lower", "groups": groups,
              "itemGroups": references, "enrichedItems": sum(reference != 0 for reference in references),
              "sources": [enrichment_sources[key] for key in sorted(enrichment_sources)],
              "provenance": provenance}
    index = {"version": 1, "revision": revision,
             "source": {"name": "Combined source catalog", "sources": [source["id"] for source in sources],
                        "attribution": "Each article retains its original source, attribution, and license."},
             "sources": descriptors, "categories": [categories[key] for key in sorted(categories)],
             "fields": FIELDS, "items": [row for row, _ in rows], "search": search}
    body = json_bytes(index)
    packed = gzip.compress(body, compresslevel=9, mtime=0)
    manifest_sources = []
    for source in sources:
        # Preserve complete source provenance (including archive metadata, license
        # notices, source counts and enrichment pins) outside the browser index.
        entry = dict(source["manifest"])
        entry.update({"id": source["id"], "sourceFormat": source["format"],
                      "source": source["index"]["source"],
                      "indexSha256": source["indexSha256"]})
        manifest_sources.append(entry)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=output.name + ".merge-", dir=output.parent))
    try:
        (stage / "guides").mkdir()
        (stage / "media").mkdir()
        media_count = media_bytes = guide_bytes = 0
        for source in sources:
            counts = [0, 0]
            with os.scandir(source["path"] / "guides") as entries:
                for entry in entries:
                    match = GUIDE_NAME.fullmatch(entry.name)
                    if not match or match[1] not in source["ids"]:
                        raise ValueError("Unexpected source guide filename: " + entry.name)
                    size = _hardlink(Path(entry.path), stage / "guides" / entry.name)
                    counts[bool(match[2])] += 1
                    if not match[2]:
                        guide_bytes += size
            if counts != [len(source["ids"]), len(source["ids"])]:
                raise ValueError("Source index has missing JSON or gzip guide files: " + source["id"])
            source_media_count = 0
            with os.scandir(source["path"] / "media") as entries:
                for entry in entries:
                    if not MEDIA_NAME.fullmatch(entry.name):
                        raise ValueError("Unexpected source media filename: " + entry.name)
                    source_media_count += 1
                    destination = stage / "media" / entry.name
                    if destination.exists():
                        if not _same_bytes(Path(entry.path), destination):
                            raise ValueError("Content-addressed media collision: " + entry.name)
                    else:
                        media_bytes += _hardlink(Path(entry.path), destination)
                        media_count += 1
            if source_media_count != source["manifest"].get("uniqueMediaFiles"):
                raise ValueError("Source media file count differs from completion metadata: " + source["id"])
        category_counts = dict(sorted(Counter(row[2] for row, _ in rows).items()))
        manifest = {"version": 1, "revision": revision, "source": index["source"],
                    "sources": manifest_sources, "sourceFormat": "collection",
                    "archiveDigestKind": "sha256-source-pins-v1",
                    "archiveSha256": digest, "expectedSha256": digest,
                    "archiveChecksumVerified": True, "sourceChecksumVerified": True,
                    "zimChecksumVerified": True if all(source["format"] == "zim" for source in sources) else None,
                    "archiveBytes": sum(source["manifest"].get("archiveBytes", 0) for source in sources),
                    "indexedItems": len(rows), "categoryCounts": category_counts,
                    "uniqueMediaFiles": media_count, "mediaBytes": media_bytes,
                    "guideJsonBytes": guide_bytes, "indexBytes": len(body), "indexGzipBytes": len(packed),
                    "searchEnrichment": {"version": 1, "enrichedItems": search["enrichedItems"],
                                         "sharedGroups": len(groups), "sources": search["sources"],
                                         "sourceCount": len(sources)},
                    "transformations": "Sources combined without altering article or media payloads; aliases remapped and source licenses retained. Prepared postings must be rebuilt before publication.",
                    "runtimeComplete": True}
        (stage / "index.json").write_bytes(body)
        (stage / "index.json.gz").write_bytes(packed)
        (stage / "manifest.json").write_bytes(json_bytes(manifest))
        if output.exists():
            output.rmdir()  # Only a still-empty destination may be replaced.
        stage.rename(output)
    except BaseException:
        shutil.rmtree(stage)
        raise
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-runtime", action="append", required=True, type=Path)
    parser.add_argument("--runtime-dir", required=True, type=Path)
    args = parser.parse_args()
    print(merge_catalogs(args.source_runtime, args.runtime_dir))


if __name__ == "__main__":
    main()
