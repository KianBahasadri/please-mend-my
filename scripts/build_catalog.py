#!/usr/bin/env python3
"""Build/reuse an immutable catalog locally or on EC2, without transferring data.

Only the final release path goes to stdout; download/build progress goes to stderr.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from overlay_guides import cache_fingerprint, overlay_guides

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
OVERLAY = ROOT / "scripts/overlay_guides.py"
STRUCTURED_GUIDES = ROOT / "scripts/structured_guides.py"
SEED_NOUNS = ROOT / "scripts/seed_nouns.py"
LINK_NOUNS = ROOT / "scripts/link_nouns.py"
NOUN_SOURCE = ROOT / "scripts/nouns-source.json"
ARCHETYPES = ROOT / "scripts/build_archetypes.py"
ART_CONFIG = ROOT / "scripts/data/archetype-art.json"
PHONE_ART = ROOT / "scripts/data/approved-phone-art.json"
BICYCLE = ROOT / "scripts/prepare_bicycle_art.py"
DIAGRAM_CREDITS = ROOT / "scripts/diagram_credits.py"
DIAGRAM_COMPILER = ROOT / "scripts/diagram_svg.py"
DIAGRAM_SOURCES = ROOT / "scripts/find_diagram_sources.py"
BICYCLE_SOURCES = (ROOT / "docs/references/bicycle/commons-bicycle-diagram.original.svg",
                   ROOT / "docs/references/bicycle/openclipart-bicycle.original.svg")
SOURCE_NOTICES = (ROOT / "docs/references/wordnet/OEWN-LICENSE.md",
                  ROOT / "docs/references/wordnet/wordfreq-NOTICE.md")
PIPELINE_INPUT_NAMES = ("INGEST", "ENRICH", "VOCABULARY", "WIKIMED", "FIRST_AID", "MERGE",
                        "SEARCH_INDEX", "OVERLAY", "STRUCTURED_GUIDES", "SEED_NOUNS", "LINK_NOUNS",
                        "NOUN_SOURCE", "ARCHETYPES", "ART_CONFIG", "PHONE_ART", "BICYCLE",
                        "DIAGRAM_CREDITS", "DIAGRAM_COMPILER", "DIAGRAM_SOURCES")


def input_key(paths):
    digest = hashlib.sha256()
    digest.update((PYTHON_VERSION + "\0" + UV_VERSION + "\0").encode())
    for path in paths:
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def source_fingerprint(runtime):
    """Read completion metadata/index only, never the guide or media corpus."""
    runtime = Path(runtime)
    completed_manifest(runtime)
    digest = hashlib.sha256()
    for name in ("manifest.json", "index.json"):
        with (runtime / name).open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        digest.update(b"\0")
    return digest.hexdigest()


def pipeline_files():
    """All code, configuration, and reviewed source pins needed on a fresh host."""
    return (Path(__file__).resolve(), *(globals()[name] for name in PIPELINE_INPUT_NAMES), *BICYCLE_SOURCES, *SOURCE_NOTICES)


def pipeline_key(cache_dir=None, source_runtime=None):
    cache_dir = cache_dir if cache_dir is not None else ROOT / "catalog-data/build/stage3_cache"
    scripts = input_key(pipeline_files())
    source = source_fingerprint(source_runtime) if source_runtime is not None else "raw-source-build"
    return hashlib.sha256((scripts + "\0" + cache_fingerprint(cache_dir) + "\0" + source).encode()).hexdigest()


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


def prepared_release(manifest):
    return (manifest.get("searchIndex", {}).get("version") == 1 and
            manifest.get("archetypes", {}).get("version") == 1 and
            manifest.get("diagramCredits", {}).get("sha256"))


def clone_snapshot(source, destination):
    """Hardlink payload filenames once; do not parse or audit raw article bodies."""
    from overlay_guides import _directory, _link_directory, _regular
    source, destination = Path(source), Path(destination)
    _directory(source)
    completed_manifest(source)
    for name in ("index.json", "index.json.gz", "manifest.json"):
        _regular(source / name)
        os.link(source / name, destination / name, follow_symlinks=False)
    for name in ("guides", "media"):
        _link_directory(source / name, destination / name)


def build_release(data_dir, release_root, uv="uv", cache_dir=None, source_runtime=None):
    data_dir, release_root = Path(data_dir).resolve(), Path(release_root).resolve()
    cache_dir = Path(cache_dir).absolute() if cache_dir is not None else data_dir / "stage3_cache"
    source_runtime = Path(source_runtime).resolve() if source_runtime is not None else None
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
    with (data_dir / ".build.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another catalog build is using this data directory.") from error
        key = pipeline_key(cache_dir, source_runtime)
        cache_key = cache_fingerprint(cache_dir)
        release = release_root / key
        if release.exists():
            receipt = json.loads((release / "ingest.json").read_bytes())
            if receipt.get("pipelineSha256") != key:
                raise RuntimeError("Existing release has different ingestion inputs.")
            manifest = completed_manifest(release)
            if not prepared_release(manifest):
                raise RuntimeError("Existing release is missing prepared search/archetype metadata.")
            print("Reusing completed catalog " + key, file=sys.stderr, flush=True)
            return release
        if data_dir.stat().st_dev != release_root.stat().st_dev:
            raise RuntimeError("Build and release directories must share a filesystem for atomic publication.")
        if source_runtime is not None and source_runtime.stat().st_dev != data_dir.stat().st_dev:
            raise RuntimeError("Existing source snapshot must share the build filesystem for hardlinks.")
        cached_ifixit = ifixit_cached(data_dir)
        if source_runtime is None and not cached_ifixit and shutil.disk_usage(data_dir).free < 20 * 1024**3:
            raise RuntimeError("Cold iFixit ingestion needs at least 20 GiB free on its data volume.")
        if shutil.disk_usage(data_dir).free < 1024**3:
            raise RuntimeError("Catalog staging needs at least 1 GiB free; previous releases remain intact.")
        result = subprocess.run([uv, "--version"], check=True, capture_output=True, text=True)
        if result.stdout.split()[:2] != ["uv", UV_VERSION]:
            raise RuntimeError("Install pinned uv " + UV_VERSION + " before building this release.")
        def run_script(script, *arguments):
            subprocess.run([uv, "run", "--python", PYTHON_VERSION, "--script", str(script),
                            *map(str, arguments)], check=True, stdout=sys.stderr)

        # Stage 0 is independent of source ingestion; its own SHA receipt avoids
        # rebuilding the noun lexicon on unchanged runs.
        run_script(SEED_NOUNS, "--data-dir", data_dir)
        if source_runtime is None:
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
        else:
            completed_manifest(source_runtime)
        runtime = Path(tempfile.mkdtemp(prefix=".merge-", dir=data_dir))
        try:
            if source_runtime is None:
                run_script(MERGE, "--source-runtime", data_dir / "runtime",
                           "--source-runtime", wikimed_data / "runtime",
                           "--source-runtime", first_aid_data / "runtime", "--runtime-dir", runtime)
            else:
                print("Repacking completed source snapshot without reingestion", file=sys.stderr, flush=True)
                clone_snapshot(source_runtime, runtime)
            manifest = completed_manifest(runtime)
            if manifest.get("searchEnrichment", {}).get("version") != 1:
                raise RuntimeError("Merged alias enrichment did not finish; refusing to publish.")
            processed = manifest.get("structuredGuides", {})
            if processed.get("processedItems", 0):
                if processed.get("cacheSha256") != cache_key:
                    raise RuntimeError("Processed source cache differs; choose its original legacy snapshot to apply a new overlay.")
            else:
                overlay_guides(runtime, cache_dir, expected_fingerprint=cache_key)
            noun_database = data_dir / "nouns/catalog.sqlite"
            arguments = ["--index", runtime / "index.json", "--database", noun_database,
                         "--seed-database", data_dir / "nouns/seed.sqlite"]
            if (data_dir / "catalog.sqlite").is_file():
                arguments.extend(["--ifixit-database", data_dir / "catalog.sqlite"])
            run_script(LINK_NOUNS, *arguments)
            run_script(ARCHETYPES, "--runtime-dir", runtime, "--database", noun_database)
            run_script(SEARCH_INDEX, "--runtime-dir", runtime, "--data-dir", data_dir,
                       "--noun-database", noun_database)
            manifest = completed_manifest(runtime)
            if manifest.get("searchIndex", {}).get("version") != 1:
                raise RuntimeError("Inverted search index did not finish; refusing to publish.")
            if not prepared_release(manifest):
                raise RuntimeError("Noun/archetype release packaging did not finish; refusing to publish.")
            if pipeline_key(cache_dir, source_runtime) != key:
                raise RuntimeError("Pipeline inputs changed during ingestion; rerun to publish matching inputs.")
            receipt = {"pipelineSha256": key, "pythonVersion": PYTHON_VERSION, "uvVersion": UV_VERSION,
                       "archiveSha256": manifest["archiveSha256"], "revision": manifest["revision"],
                       "indexSha256": hashlib.sha256((runtime / "index.json").read_bytes()).hexdigest(),
                       "indexedItems": manifest["indexedItems"], "uniqueMediaFiles": manifest["uniqueMediaFiles"],
                       "sources": manifest.get("sources", []), "searchEnrichment": manifest["searchEnrichment"],
                       "searchIndex": manifest["searchIndex"], "structuredGuides": manifest["structuredGuides"],
                       "archetypes": manifest["archetypes"], "diagramCredits": manifest["diagramCredits"]}
            if source_runtime is not None:
                receipt["sourceRuntimeSha256"] = source_fingerprint(source_runtime)
            temporary = runtime / "ingest.json.tmp"
            temporary.write_text(json.dumps(receipt, separators=(",", ":")) + "\n")
            temporary.chmod(0o644)
            temporary.replace(runtime / "ingest.json")
            runtime.chmod(0o755)
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
    parser.add_argument("--source-runtime", type=Path,
                        help="Repack a completed snapshot with the same noun/search stages, without source downloads.")
    parser.add_argument("--stage3-cache", type=Path, help="Validated active guide cache (default: <data-dir>/stage3_cache).")
    parser.add_argument("--print-key", action="store_true", help="Print the source/version fingerprint without building anything.")
    args = parser.parse_args()
    if args.print_key:
        print(pipeline_key(args.stage3_cache or args.data_dir / "stage3_cache", args.source_runtime))
    else:
        print(build_release(args.data_dir, args.release_root, args.uv, args.stage3_cache, args.source_runtime), flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print("Catalog build failed: " + str(error), file=sys.stderr)
        sys.exit(1)
