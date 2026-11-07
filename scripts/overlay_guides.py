#!/usr/bin/env python3
"""Overlay validated Stage 3 guides into an unpublished, static catalog snapshot.

Only active ``<id>.json`` cache entries are inspected. Raw guides without a cache
entry remain byte-for-byte legacy responses. No serving-time routing is needed.
"""
from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
CACHE_NAME = re.compile(r"([0-9a-f]{20})\.json\Z")
MAX_CACHE_BYTES = 4 * 1024 * 1024


def _regular(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Overlay input must be a regular file: " + str(path))


def _directory(path):
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError("Overlay input must be a directory, not a symlink: " + str(path))


def cache_snapshot(cache_dir):
    """Return a small active-cache snapshot and its content fingerprint.

    SHA-addressed objects, reports, locks, and temporary files are not active
    overlays. Invalid active files still affect the fingerprint, but are never
    followed or published. The source corpus is never enumerated here.
    """
    cache_dir = Path(cache_dir)
    digest = hashlib.sha256(b"structured-guide-cache-v2\0")
    entries = {}
    if not cache_dir.exists() and not cache_dir.is_symlink():
        return entries, digest.hexdigest()
    _directory(cache_dir)
    with os.scandir(cache_dir) as files:
        names = sorted(entry.name for entry in files if CACHE_NAME.fullmatch(entry.name))
    for name in names:
        path = cache_dir / name
        info = path.lstat()
        body = None
        if stat.S_ISREG(info.st_mode) and info.st_size <= MAX_CACHE_BYTES:
            body = path.read_bytes()
            marker = b"json\0" + body
        elif stat.S_ISREG(info.st_mode):
            marker = ("oversize\0" + str(info.st_size) + "\0" + str(info.st_mtime_ns)).encode()
        else:
            marker = b"nonregular"
        digest.update(name.encode() + b"\0" + hashlib.sha256(marker).digest())
        entries[name[:-5]] = body
    return entries, digest.hexdigest()


def cache_fingerprint(cache_dir):
    return cache_snapshot(cache_dir)[1]


def _replace(path, body):
    """Break hardlinks through rename; never truncate a source or live inode."""
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def overlay_guides(runtime, cache_dir, expected_fingerprint=None):
    """Choose validated v2 or original v1 JSON in a fresh unpublished runtime.

    Callers must discard the whole stage on failure. A changed cache aborts
    publication rather than giving an immutable release a misleading input key.
    """
    from structured_guides import canonical_bytes, validate_v2

    runtime, cache_dir = Path(runtime), Path(cache_dir)
    _directory(runtime)
    _directory(runtime / "guides")
    _regular(runtime / "manifest.json")
    manifest = json.loads((runtime / "manifest.json").read_bytes())
    if manifest.get("runtimeComplete") is not True:
        raise ValueError("Overlay requires a completed source snapshot.")
    if manifest.get("structuredGuides", {}).get("processedItems", 0):
        raise ValueError("Overlay requires a legacy base snapshot; never use processed guides as raw inputs.")
    candidates, fingerprint = cache_snapshot(cache_dir)
    if expected_fingerprint is not None and fingerprint != expected_fingerprint:
        raise RuntimeError("Stage 3 cache changed during the catalog build; rerun with stable inputs.")
    applied = rejected = delta = 0
    for identifier, cached in candidates.items():
        raw_path = runtime / "guides" / (identifier + ".json")
        try:
            if cached is None:
                raise ValueError("Invalid active cache file.")
            _regular(raw_path)
            raw_bytes = raw_path.read_bytes()
            raw = json.loads(raw_bytes)
            detail = json.loads(cached)
            if not isinstance(detail, dict) or detail.get("id") != identifier:
                raise ValueError("Cached guide ID differs from its filename.")
            validate_v2(detail, raw=raw)
            body = canonical_bytes(detail)
            packed = gzip.compress(body, compresslevel=9, mtime=0)
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            rejected += 1
            continue
        _replace(raw_path, body)
        _replace(raw_path.with_name(raw_path.name + ".gz"), packed)
        applied += 1
        delta += len(body) - len(raw_bytes)
    if cache_fingerprint(cache_dir) != fingerprint:
        raise RuntimeError("Stage 3 cache changed during the catalog build; refusing to publish.")
    metadata = {"version": 2, "cacheSha256": fingerprint, "processedItems": applied,
                "legacyItems": manifest["indexedItems"] - applied, "rejectedItems": rejected}
    manifest["structuredGuides"] = metadata
    if "guideJsonBytes" in manifest:
        manifest["guideJsonBytes"] += delta
    _replace(runtime / "manifest.json", canonical_bytes(manifest))
    return metadata


def _link_directory(source, destination):
    _directory(source)
    destination.mkdir(mode=0o755)
    destination.chmod(0o755)
    with os.scandir(source) as entries:
        for entry in entries:
            path = Path(entry.path)
            _regular(path)
            os.link(path, destination / entry.name, follow_symlinks=False)


def overlay_release(source_runtime, cache_dir, release_root):
    """Publish an overlay snapshot from existing raw data, without reingestion.

    Unmodified media/guides are hardlinked. This enumerates filenames to construct
    the snapshot, never scans their content. The original runtime stays intact.
    """
    from build_catalog import completed_manifest, input_key
    from structured_guides import canonical_bytes

    source = Path(source_runtime).absolute()
    cache_dir = Path(cache_dir).absolute()
    release_root = Path(release_root).absolute()
    _directory(source)
    manifest = completed_manifest(source)
    if manifest.get("structuredGuides", {}).get("processedItems", 0):
        raise ValueError("Choose the original legacy source release, not a previous overlay.")
    if manifest.get("searchIndex", {}).get("version") != 1:
        raise ValueError("Overlay source needs its completed browser search index.")
    candidates, fingerprint = cache_snapshot(cache_dir)
    if not candidates:
        # A rollout with no processed guides needs no new snapshot or corpus walk.
        return source
    if release_root == source or source in release_root.parents:
        raise ValueError("Overlay releases must be outside the source snapshot.")
    release_root.mkdir(parents=True, exist_ok=True)
    if source.stat().st_dev != release_root.stat().st_dev:
        raise ValueError("Overlay releases must share a filesystem with their raw source.")
    inputs = input_key((Path(__file__).resolve(), ROOT / "scripts/structured_guides.py"))
    source_identity = {"manifest": manifest, "indexSha256": hashlib.sha256((source / "index.json").read_bytes()).hexdigest()}
    key = hashlib.sha256(canonical_bytes({"overlayInputs": inputs, "cache": fingerprint,
                                         "source": source_identity})).hexdigest()
    release = release_root / key
    with (release_root / ".overlay.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another overlay build is using this release root.") from error
        if release.exists():
            receipt = json.loads((release / "ingest.json").read_bytes())
            if receipt.get("pipelineSha256") != key or receipt.get("structuredGuides", {}).get("cacheSha256") != fingerprint:
                raise RuntimeError("Existing overlay release has different inputs.")
            completed_manifest(release)
            return release
        stage = Path(tempfile.mkdtemp(prefix=".overlay-", dir=release_root))
        try:
            for name in ("index.json", "index.json.gz", "manifest.json"):
                _regular(source / name)
                os.link(source / name, stage / name, follow_symlinks=False)
            _link_directory(source / "guides", stage / "guides")
            _link_directory(source / "media", stage / "media")
            metadata = overlay_guides(stage, cache_dir, fingerprint)
            receipt = {"pipelineSha256": key, "overlayOf": str(source), "overlayInputsSha256": inputs,
                       "sourceIndexSha256": source_identity["indexSha256"], "structuredGuides": metadata}
            _replace(stage / "ingest.json", canonical_bytes(receipt))
            if cache_fingerprint(cache_dir) != fingerprint:
                raise RuntimeError("Stage 3 cache changed before overlay publication.")
            if input_key((Path(__file__).resolve(), ROOT / "scripts/structured_guides.py")) != inputs:
                raise RuntimeError("Overlay pipeline inputs changed before publication.")
            completed_manifest(stage)
            stage.chmod(0o755)
            stage.rename(release)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    return release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "catalog-data/build/stage3_cache")
    parser.add_argument("--source-runtime", type=Path, required=True,
                        help="Existing legacy release to hardlink into a new immutable snapshot.")
    parser.add_argument("--release-root", type=Path, default=ROOT / "catalog-data/releases")
    args = parser.parse_args()
    print(overlay_release(args.source_runtime, args.cache_dir, args.release_root))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print("Guide overlay failed: " + str(error), file=sys.stderr)
        sys.exit(1)
