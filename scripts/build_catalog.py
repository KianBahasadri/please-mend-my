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
import tempfile

PYTHON_VERSION = "3.12.12"
UV_VERSION = "0.13.5"
ROOT = Path(__file__).resolve().parent.parent
INGEST = ROOT / "scripts/ingest_ifixit.py"
ENRICH = ROOT / "scripts/enrich_search.py"
VOCABULARY = ROOT / "scripts/search-vocabulary.json"
SEARCH_INDEX = ROOT / "scripts/prepare_search.py"
WIKIMED = ROOT / "scripts/ingest_wikimed.py"
FIRST_AID = ROOT / "scripts/ingest_first_aid.py"
MERGE = ROOT / "scripts/merge_catalogs.py"


def input_key(paths):
    digest = hashlib.sha256()
    digest.update((PYTHON_VERSION + "\0" + UV_VERSION + "\0").encode())
    for path in paths:
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def pipeline_key():
    return input_key((Path(__file__).resolve(), INGEST, ENRICH, VOCABULARY,
                      WIKIMED, FIRST_AID, MERGE, SEARCH_INDEX))


def verified_source(manifest):
    if not (manifest.get("archiveChecksumVerified") and manifest.get("archiveSha256") and
            manifest["archiveSha256"] == manifest.get("expectedSha256")):
        return False
    kind = manifest.get("sourceFormat", "zim")
    if kind == "collection":
        sources = manifest.get("sources", [])
        return bool(manifest.get("sourceChecksumVerified") and sources and
                    all(verified_source(source) for source in sources))
    if kind == "pdf":
        return manifest.get("documentVerified") is True
    return kind == "zim" and manifest.get("zimChecksumVerified") is True


def completed_manifest(runtime):
    manifest = json.loads((runtime / "manifest.json").read_bytes())
    index_path, packed_path = runtime / "index.json", runtime / "index.json.gz"
    if not (manifest.get("runtimeComplete") and verified_source(manifest) and
            manifest.get("indexedItems", 0) > 0 and manifest.get("uniqueMediaFiles", 0) > 0):
        raise RuntimeError("Ingestion has not produced a complete checksum-verified catalog.")
    if (index_path.stat().st_size != manifest["indexBytes"] or
            packed_path.stat().st_size != manifest["indexGzipBytes"] or
            not (runtime / "guides").is_dir() or not (runtime / "media").is_dir()):
        raise RuntimeError("Completed catalog metadata or required runtime files are missing.")
    for source in manifest.get("sources", []):
        if not verified_source(source):
            raise RuntimeError("A constituent source is not checksum-verified.")
    return manifest


def ifixit_cached(data_dir):
    """Check small completion receipts only; never scan a completed source corpus."""
    try:
        receipt = json.loads((data_dir / "runtime/source-build.json").read_bytes())
        if receipt.get("sourceSha256") != input_key((INGEST, ENRICH, VOCABULARY)):
            return False
        manifest = completed_manifest(data_dir / "runtime")
        return manifest.get("searchEnrichment", {}).get("version") == 1
    except (OSError, ValueError, KeyError, RuntimeError):
        return False


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
        cached_ifixit = ifixit_cached(data_dir)
        # iFixit needs ~18 GiB on a cold build; merges hardlink cached payloads.
        if not cached_ifixit and shutil.disk_usage(data_dir).free < 20 * 1024**3:
            raise RuntimeError("Cold iFixit ingestion needs at least 20 GiB free on its data volume.")
        result = subprocess.run([uv, "--version"], check=True, capture_output=True, text=True)
        if result.stdout.split()[:2] != ["uv", UV_VERSION]:
            raise RuntimeError("Install pinned uv " + UV_VERSION + " before building this release.")
        def run_script(script, *arguments):
            subprocess.run([uv, "run", "--python", PYTHON_VERSION, "--script", str(script),
                            *map(str, arguments)], check=True, stdout=sys.stderr)

        if cached_ifixit:
            print("Reusing completed iFixit source", file=sys.stderr, flush=True)
        else:
            run_script(INGEST, "--data-dir", data_dir)
            completed_manifest(data_dir / "runtime")
            run_script(ENRICH, "--data-dir", data_dir)
            manifest = completed_manifest(data_dir / "runtime")
            if manifest.get("searchEnrichment", {}).get("version") != 1:
                raise RuntimeError("Search alias enrichment did not finish; refusing to publish.")
            receipt = {"sourceSha256": input_key((INGEST, ENRICH, VOCABULARY))}
            temporary = data_dir / "runtime/source-build.json.tmp"
            temporary.write_text(json.dumps(receipt, separators=(",", ":")) + "\n")
            temporary.replace(data_dir / "runtime/source-build.json")

        wikimed_data = data_dir / "wikimed"
        run_script(WIKIMED, "--data-dir", wikimed_data)
        completed_manifest(wikimed_data / "runtime")
        first_aid_data = data_dir / "first-aid"
        run_script(FIRST_AID, "--data-dir", first_aid_data)
        completed_manifest(first_aid_data / "runtime")
        # Each attempt owns an empty staging directory. Published releases and
        # source payloads stay intact on any download/merge/indexing failure.
        runtime = Path(tempfile.mkdtemp(prefix=".merge-", dir=data_dir))
        try:
            run_script(MERGE, "--source-runtime", data_dir / "runtime",
                       "--source-runtime", wikimed_data / "runtime",
                       "--source-runtime", first_aid_data / "runtime", "--runtime-dir", runtime)
            manifest = completed_manifest(runtime)
            if manifest.get("searchEnrichment", {}).get("version") != 1:
                raise RuntimeError("Merged alias enrichment did not finish; refusing to publish.")
            run_script(SEARCH_INDEX, "--runtime-dir", runtime, "--data-dir", data_dir)
            manifest = completed_manifest(runtime)
            if manifest.get("searchIndex", {}).get("version") != 1:
                raise RuntimeError("Inverted search index did not finish; refusing to publish.")
            if pipeline_key() != key:
                raise RuntimeError("Pipeline inputs changed during ingestion; rerun to publish matching inputs.")
            receipt = {"pipelineSha256": key, "pythonVersion": PYTHON_VERSION, "uvVersion": UV_VERSION,
                       "archiveSha256": manifest["archiveSha256"], "revision": manifest["revision"],
                       "indexSha256": hashlib.sha256((runtime / "index.json").read_bytes()).hexdigest(),
                       "indexedItems": manifest["indexedItems"], "uniqueMediaFiles": manifest["uniqueMediaFiles"],
                       "sources": manifest["sources"], "searchEnrichment": manifest["searchEnrichment"],
                       "searchIndex": manifest["searchIndex"]}
            temporary = runtime / "ingest.json.tmp"
            temporary.write_text(json.dumps(receipt, separators=(",", ":")) + "\n")
            temporary.replace(runtime / "ingest.json")
            runtime.rename(release)
        finally:
            if runtime.exists():
                shutil.rmtree(runtime)
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
