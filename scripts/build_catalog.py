#!/usr/bin/env python3
"""Build/reuse an immutable catalog locally or on EC2, without transferring data.

Only the final release path goes to stdout; download/build progress goes to stderr.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

PYTHON_VERSION = "3.12.12"
UV_VERSION = "0.13.5"
ROOT = Path(__file__).resolve().parent.parent
INGEST = ROOT / "scripts/ingest_ifixit.py"
ENRICH = ROOT / "scripts/enrich_search.py"
VOCABULARY = ROOT / "scripts/search-vocabulary.json"
SEARCH_INDEX = ROOT / "scripts/prepare_search.py"


def pipeline_key():
    digest = hashlib.sha256()
    for path in (Path(__file__).resolve(), INGEST, ENRICH, VOCABULARY, SEARCH_INDEX):
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def completed_manifest(runtime):
    manifest = json.loads((runtime / "manifest.json").read_bytes())
    index_path, packed_path = runtime / "index.json", runtime / "index.json.gz"
    if not (manifest.get("runtimeComplete") and manifest.get("archiveChecksumVerified") and
            manifest.get("zimChecksumVerified") and manifest.get("archiveSha256") and
            manifest["archiveSha256"] == manifest.get("expectedSha256") and
            manifest.get("indexedItems", 0) > 0 and manifest.get("uniqueMediaFiles", 0) > 0):
        raise RuntimeError("Ingestion has not produced a complete checksum-verified catalog.")
    if (index_path.stat().st_size != manifest["indexBytes"] or
            packed_path.stat().st_size != manifest["indexGzipBytes"] or
            not (runtime / "guides").is_dir() or not (runtime / "media").is_dir()):
        raise RuntimeError("Completed catalog metadata or required runtime files are missing.")
    return manifest


def build_release(data_dir, release_root, uv="uv"):
    data_dir, release_root = Path(data_dir).resolve(), Path(release_root).resolve()
    if data_dir == release_root or data_dir in release_root.parents:
        raise ValueError("Release storage must be separate from the mutable ingestion directory.")
    for path in (data_dir, release_root):
        try:
            relative = path.relative_to(ROOT.resolve())
        except ValueError:
            continue
        ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--quiet", "--",
                                  str(relative / "catalog-output-probe")], capture_output=True)
        if ignored.returncode != 0:
            raise ValueError("Catalog output inside the source checkout must be Git-ignored: " + str(path))
    data_dir.mkdir(parents=True, exist_ok=True)
    release_root.mkdir(parents=True, exist_ok=True)
    key = pipeline_key()
    release = release_root / key
    with (data_dir / ".build.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another catalog build is using this data directory.") from error
        if release.exists():
            receipt = json.loads((release / "ingest.json").read_bytes())
            if receipt.get("pipelineSha256") != key:
                raise RuntimeError("Existing release has different ingestion inputs.")
            manifest = completed_manifest(release)
            if manifest.get("searchIndex", {}).get("version") != 1:
                raise RuntimeError("Existing release is missing its prepared search index.")
            print("Reusing completed catalog " + key, file=sys.stderr, flush=True)
            return release
        if data_dir.stat().st_dev != release_root.stat().st_dev:
            raise RuntimeError("Build and release directories must share a filesystem for atomic publication.")
        # The pinned archive, scratch SQLite, runtime, and dependencies need ~18 GiB.
        if shutil.disk_usage(data_dir).free < 20 * 1024**3:
            raise RuntimeError("Catalog ingestion needs at least 20 GiB free on its data volume.")
        result = subprocess.run([uv, "--version"], check=True, capture_output=True, text=True)
        if result.stdout.split()[:2] != ["uv", UV_VERSION]:
            raise RuntimeError("Install pinned uv " + UV_VERSION + " before building this release.")
        subprocess.run([uv, "run", "--python", PYTHON_VERSION, "--script", str(INGEST),
                        "--data-dir", str(data_dir)], check=True, stdout=sys.stderr)
        runtime = data_dir / "runtime"
        completed_manifest(runtime)
        subprocess.run([uv, "run", "--python", PYTHON_VERSION, "--script", str(ENRICH),
                        "--data-dir", str(data_dir)], check=True, stdout=sys.stderr)
        manifest = completed_manifest(runtime)
        if manifest.get("searchEnrichment", {}).get("version") != 1:
            raise RuntimeError("Search alias enrichment did not finish; refusing to publish.")
        subprocess.run([uv, "run", "--python", PYTHON_VERSION, "--script", str(SEARCH_INDEX),
                        "--data-dir", str(data_dir)], check=True, stdout=sys.stderr)
        manifest = completed_manifest(runtime)
        if manifest.get("searchIndex", {}).get("version") != 1:
            raise RuntimeError("Inverted search index did not finish; refusing to publish.")
        receipt = {"pipelineSha256": key, "pythonVersion": PYTHON_VERSION, "uvVersion": UV_VERSION,
                   "archiveSha256": manifest["archiveSha256"], "revision": manifest["revision"],
                   "indexSha256": hashlib.sha256((runtime / "index.json").read_bytes()).hexdigest(),
                   "indexedItems": manifest["indexedItems"], "uniqueMediaFiles": manifest["uniqueMediaFiles"],
                   "searchEnrichment": manifest["searchEnrichment"], "searchIndex": manifest["searchIndex"]}
        temporary = runtime / "ingest.json.tmp"
        temporary.write_text(json.dumps(receipt, separators=(",", ":")) + "\n")
        temporary.replace(runtime / "ingest.json")
        runtime.rename(release)
        return release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "catalog-data/build")
    parser.add_argument("--release-root", type=Path, default=ROOT / "catalog-data/releases")
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--print-key", action="store_true", help="Print the source/version fingerprint without building anything.")
    args = parser.parse_args()
    if args.print_key:
        print(pipeline_key())
    else:
        print(build_release(args.data_dir, args.release_root, args.uv), flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print("Catalog build failed: " + str(error), file=sys.stderr)
        sys.exit(1)
