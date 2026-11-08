"""Release lifecycle tests use tiny generated data, never the real catalog."""
import base64
import fcntl
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import build_catalog as builder

spec = importlib.util.spec_from_file_location("verify_host_key", Path(__file__).resolve().parent.parent / "deploy/verify_host_key.py")
host_keys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host_keys)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data, self.releases = self.root / "build", self.root / "releases"
        # Every mutable input is a tiny fixture, independent of source builds.
        for name in builder.PIPELINE_INPUT_NAMES:
            path = self.root / (name.lower() + ".py")
            path.write_text("# pinned fixture " + name + "\n")
            fixture = patch.object(builder, name, path)
            fixture.start()
            self.addCleanup(fixture.stop)
        bicycle_sources = []
        for number in range(2):
            path = self.root / ("bicycle-source-" + str(number) + ".svg")
            path.write_text("<svg/>")
            bicycle_sources.append(path)
        fixture = patch.object(builder, "BICYCLE_SOURCES", tuple(bicycle_sources))
        fixture.start()
        self.addCleanup(fixture.stop)
        self.ingest, self.wikimed = builder.INGEST, builder.WIKIMED
        self.space = patch.object(builder.shutil, "disk_usage", return_value=type("Space", (), {"free": 64 * 1024**3})())
        self.space.start()
        self.addCleanup(self.space.stop)
        self.commands = []

    @staticmethod
    def script(args):
        return Path(args[args.index("--script") + 1]) if "--script" in args else None

    @staticmethod
    def argument(args, flag):
        return Path(args[args.index(flag) + 1])

    def write_runtime(self, runtime, sources):
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "guides").mkdir(exist_ok=True)
        (runtime / "media").mkdir(exist_ok=True)
        for source in sources:
            (runtime / "guides" / (source + ".json")).write_text(source + " guide")
            (runtime / "media" / (source + ".png")).write_bytes(source.encode())
        index = json.dumps({"items": [[source, source + " fixture"] for source in sources]}).encode()
        packed = gzip.compress(index, mtime=0)
        (runtime / "index.json").write_bytes(index)
        (runtime / "index.json.gz").write_bytes(packed)
        pins = [{"id": source, "archiveChecksumVerified": True, "zimChecksumVerified": True,
                 "archiveSha256": ("a" if source == "ifixit" else "b") * 64,
                 "expectedSha256": ("a" if source == "ifixit" else "b") * 64} for source in sources]
        for pin in pins:
            if pin["id"] == "first-aid":
                pin.update(sourceFormat="pdf", documentVerified=True, zimChecksumVerified=None)
        manifest = {"runtimeComplete": True, "archiveChecksumVerified": True, "zimChecksumVerified": True,
                    "archiveSha256": pins[0]["archiveSha256"], "expectedSha256": pins[0]["expectedSha256"],
                    "revision": "fixture", "indexedItems": len(sources), "uniqueMediaFiles": len(sources),
                    "indexBytes": len(index), "indexGzipBytes": len(packed)}
        if len(sources) > 1:
            manifest["sources"] = pins
            manifest.update(sourceFormat="collection", sourceChecksumVerified=True, zimChecksumVerified=None)
            manifest["searchEnrichment"] = {"version": 1}
        elif sources == ["first-aid"]:
            manifest.update(sourceFormat="pdf", documentVerified=True, zimChecksumVerified=None)
        (runtime / "manifest.json").write_text(json.dumps(manifest))

    @staticmethod
    def update_manifest(runtime, **changes):
        path = runtime / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest.update(changes)
        path.write_text(json.dumps(manifest))

    def fake_command(self, args, **kwargs):
        if args[1:] == ["--version"]:
            return subprocess.CompletedProcess(args, 0, "uv " + builder.UV_VERSION + "\n")
        self.assertEqual(args[1:5], ["run", "--python", builder.PYTHON_VERSION, "--script"])
        script = self.script(args)
        self.commands.append(script)
        if script == builder.SEED_NOUNS:
            self.assertEqual(self.argument(args, "--data-dir"), self.data)
            (self.data / "nouns").mkdir(exist_ok=True)
            (self.data / "nouns/seed.sqlite").write_bytes(b"seed fixture")
        elif script == builder.LINK_NOUNS:
            self.assertEqual(self.argument(args, "--database"), self.data / "nouns/catalog.sqlite")
            self.assertEqual(self.argument(args, "--seed-database"), self.data / "nouns/seed.sqlite")
            self.assertTrue(self.argument(args, "--index").is_file())
            (self.data / "nouns/catalog.sqlite").write_bytes(b"linked fixture")
            (self.data / "nouns/gaps.json").write_text('{"unfilled":[]}')
        elif script == builder.ARCHETYPES:
            runtime = self.argument(args, "--runtime-dir")
            self.assertEqual(self.argument(args, "--database"), self.data / "nouns/catalog.sqlite")
            self.update_manifest(runtime, archetypes={"version": 1, "count": 3, "models": 0},
                                 diagramCredits={"bytes": 0, "sha256": "e" * 64})
        elif script == builder.INGEST:
            self.assertEqual(self.argument(args, "--data-dir"), self.data)
            self.write_runtime(self.data / "runtime", ["ifixit"])
        elif script == builder.ENRICH:
            self.assertEqual(self.argument(args, "--data-dir"), self.data)
            self.update_manifest(self.data / "runtime", searchEnrichment={"version": 1})
        elif script == builder.WIKIMED:
            self.assertEqual(self.argument(args, "--data-dir"), self.data / "wikimed")
            self.write_runtime(self.data / "wikimed/runtime", ["wikimed"])
        elif script == builder.FIRST_AID:
            self.assertEqual(self.argument(args, "--data-dir"), self.data / "first-aid")
            self.write_runtime(self.data / "first-aid/runtime", ["first-aid"])
        elif script == builder.MERGE:
            source_paths = [Path(args[i + 1]) for i, arg in enumerate(args) if arg == "--source-runtime"]
            self.assertEqual(source_paths, [self.data / "runtime", self.data / "wikimed/runtime",
                                            self.data / "first-aid/runtime"])
            runtime = self.argument(args, "--runtime-dir")
            self.assertEqual(runtime.parent, self.data)
            self.assertTrue(runtime.name.startswith(".merge-"))
            self.assertEqual(list(runtime.iterdir()), [], "Each merge needs a fresh empty staging directory")
            self.write_runtime(runtime, ["ifixit", "wikimed", "first-aid"])
        elif script == builder.SEARCH_INDEX:
            self.assertEqual(self.argument(args, "--data-dir"), self.data)
            runtime = self.argument(args, "--runtime-dir")
            self.assertTrue(runtime.name.startswith(".merge-"))
            self.update_manifest(runtime, searchIndex={"version": 1})
        else:
            self.fail("Unexpected pipeline command: " + str(args))
        return subprocess.CompletedProcess(args, 0)

    def previous_release(self):
        previous = self.releases / "previous"
        previous.mkdir(parents=True)
        (previous / "live").write_text("keep")
        return previous

    def assert_not_published(self, previous=None):
        self.assertFalse((self.releases / builder.pipeline_key(self.data / "stage3_cache")).exists())
        self.assertEqual(list(self.data.glob(".merge-*")), [], "Failed staging must be cleaned up")
        if previous:
            self.assertEqual((previous / "live").read_text(), "keep")

    def test_publish_then_reuse_without_running_ingestion(self):
        with patch.object(builder.subprocess, "run", side_effect=self.fake_command) as run:
            release = builder.build_release(self.data, self.releases)
            self.assertEqual(run.call_count, 10)
        self.assertEqual(self.commands, [builder.SEED_NOUNS, builder.INGEST, builder.ENRICH, builder.WIKIMED,
                                         builder.FIRST_AID, builder.MERGE, builder.LINK_NOUNS,
                                         builder.ARCHETYPES, builder.SEARCH_INDEX])
        self.assertEqual(release.name, builder.pipeline_key(self.data / "stage3_cache"))
        self.assertTrue((self.data / "runtime/source-build.json").is_file())
        self.assertTrue((self.data / "wikimed/runtime/manifest.json").is_file())
        self.assertEqual((self.data / "runtime/guides/ifixit.json").read_text(), "ifixit guide")
        self.assertEqual((release / "guides/wikimed.json").read_text(), "wikimed guide")
        receipt = json.loads((release / "ingest.json").read_text())
        self.assertEqual(receipt["pipelineSha256"], release.name)
        self.assertEqual(receipt["pythonVersion"], builder.PYTHON_VERSION)
        self.assertEqual(receipt["uvVersion"], builder.UV_VERSION)
        self.assertEqual(receipt["searchIndex"], {"version": 1})
        self.assertEqual([source["id"] for source in receipt["sources"]], ["ifixit", "wikimed", "first-aid"])
        self.assertTrue(receipt["sources"][2]["documentVerified"])
        self.assertIsNone(receipt["sources"][2]["zimChecksumVerified"])
        with patch.object(builder.subprocess, "run") as run:
            self.assertEqual(builder.build_release(self.data, self.releases), release)
            run.assert_not_called()

    def test_failed_build_leaves_previous_release_untouched(self):
        previous = self.previous_release()
        def fail(args, **kwargs):
            result = self.fake_command(args, **kwargs)
            if self.script(args) == builder.INGEST:
                raise subprocess.CalledProcessError(1, args)
            return result
        with patch.object(builder.subprocess, "run", side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                builder.build_release(self.data, self.releases)
        self.assert_not_published(previous)

    def test_incomplete_metadata_cannot_publish(self):
        previous = self.previous_release()
        def incomplete(args, **kwargs):
            result = self.fake_command(args, **kwargs)
            if self.script(args) == builder.MERGE:
                self.update_manifest(self.argument(args, "--runtime-dir"), runtimeComplete=False)
            return result
        with patch.object(builder.subprocess, "run", side_effect=incomplete):
            with self.assertRaisesRegex(RuntimeError, "complete checksum-verified"):
                builder.build_release(self.data, self.releases)
        self.assert_not_published(previous)

    def test_changed_pipeline_builds_a_separate_release(self):
        with patch.object(builder.subprocess, "run", side_effect=self.fake_command):
            first = builder.build_release(self.data, self.releases)
            first_receipt = (first / "ingest.json").read_bytes()
            self.ingest.write_text("# changed pipeline input\n")
            second = builder.build_release(self.data, self.releases)
        self.assertNotEqual(first, second)
        self.assertEqual((first / "ingest.json").read_bytes(), first_receipt)
        self.assertTrue((second / "ingest.json").is_file())
        self.assertEqual(self.commands.count(builder.INGEST), 2)
        self.assertEqual(self.commands.count(builder.ENRICH), 2)

    def test_only_wikimed_change_reuses_ifixit_source_cache(self):
        with patch.object(builder.subprocess, "run", side_effect=self.fake_command):
            first = builder.build_release(self.data, self.releases)
            first_index = (first / "index.json").read_bytes()
            ifixit_manifest = (self.data / "runtime/manifest.json").read_bytes()
            self.commands.clear()
            self.wikimed.write_text("# changed WikiMed archive pin\n")
            second = builder.build_release(self.data, self.releases)
        self.assertNotEqual(first, second)
        self.assertEqual(self.commands, [builder.SEED_NOUNS, builder.WIKIMED, builder.FIRST_AID, builder.MERGE, builder.LINK_NOUNS, builder.ARCHETYPES, builder.SEARCH_INDEX])
        self.assertEqual((self.data / "runtime/manifest.json").read_bytes(), ifixit_manifest)
        self.assertEqual((first / "index.json").read_bytes(), first_index)
        self.assertTrue((second / "ingest.json").is_file())

    def test_active_overlay_changes_invalidate_release_but_reuse_raw_sources(self):
        with patch.object(builder.subprocess, "run", side_effect=self.fake_command):
            first = builder.build_release(self.data, self.releases)
            original = (first / "ingest.json").read_bytes()
            cache = self.data / "stage3_cache"
            cache.mkdir()
            # An invalid active guide still changes the rollout fingerprint;
            # it is rejected and raw responses survive unchanged.
            (cache / ("a" * 20 + ".json")).write_text("{unfinished")
            self.commands.clear()
            second = builder.build_release(self.data, self.releases)
        self.assertNotEqual(first, second)
        self.assertEqual(self.commands, [builder.SEED_NOUNS, builder.WIKIMED, builder.FIRST_AID, builder.MERGE, builder.LINK_NOUNS, builder.ARCHETYPES, builder.SEARCH_INDEX])
        receipt = json.loads((second / "ingest.json").read_bytes())
        self.assertEqual(receipt["structuredGuides"]["processedItems"], 0)
        self.assertEqual(receipt["structuredGuides"]["rejectedItems"], 1)
        self.assertEqual(receipt["structuredGuides"]["cacheSha256"], builder.cache_fingerprint(cache))
        self.assertEqual((first / "ingest.json").read_bytes(), original)
        self.assertEqual((second / "guides/ifixit.json").read_text(), "ifixit guide")
        with patch.object(builder.subprocess, "run") as run:
            self.assertEqual(builder.build_release(self.data, self.releases), second)
            run.assert_not_called()

    def test_cache_changed_before_publication_cannot_publish_old_key(self):
        original = builder.pipeline_key(self.data / "stage3_cache")
        def change_cache(args, **kwargs):
            result = self.fake_command(args, **kwargs)
            if self.script(args) == builder.SEARCH_INDEX:
                cache = self.data / "stage3_cache"
                cache.mkdir()
                (cache / ("a" * 20 + ".json")).write_text("{new input")
            return result
        with patch.object(builder.subprocess, "run", side_effect=change_cache):
            with self.assertRaisesRegex(RuntimeError, "inputs changed during ingestion"):
                builder.build_release(self.data, self.releases)
        self.assertFalse((self.releases / original).exists())
        self.assert_not_published()

    def test_constituent_bad_pin_cannot_publish(self):
        previous = self.previous_release()
        def wrong_pin(args, **kwargs):
            result = self.fake_command(args, **kwargs)
            if self.script(args) == builder.MERGE:
                runtime = self.argument(args, "--runtime-dir")
                manifest = json.loads((runtime / "manifest.json").read_text())
                manifest["sources"][1]["expectedSha256"] = "c" * 64
                self.update_manifest(runtime, sources=manifest["sources"])
            return result
        with patch.object(builder.subprocess, "run", side_effect=wrong_pin):
            with self.assertRaisesRegex(RuntimeError, "checksum-verified"):
                builder.build_release(self.data, self.releases)
        self.assert_not_published(previous)

    def test_concurrent_build_is_rejected(self):
        self.data.mkdir()
        with (self.data / ".build.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaisesRegex(RuntimeError, "Another catalog build"):
                builder.build_release(self.data, self.releases)

    def test_alias_failure_cannot_publish_completed_core_data(self):
        def fail_aliases(args, **kwargs):
            if self.script(args) == builder.ENRICH:
                raise subprocess.CalledProcessError(1, args)
            return self.fake_command(args, **kwargs)
        with patch.object(builder.subprocess, "run", side_effect=fail_aliases):
            with self.assertRaises(subprocess.CalledProcessError):
                builder.build_release(self.data, self.releases)
        self.assertTrue((self.data / "runtime/manifest.json").is_file())
        self.assertFalse((self.data / "runtime/source-build.json").exists())
        self.assert_not_published()

    def test_wikimed_failure_cannot_publish_or_damage_cached_ifixit(self):
        previous = self.previous_release()
        def fail_wikimed(args, **kwargs):
            if self.script(args) == builder.WIKIMED:
                raise subprocess.CalledProcessError(1, args)
            return self.fake_command(args, **kwargs)
        with patch.object(builder.subprocess, "run", side_effect=fail_wikimed):
            with self.assertRaises(subprocess.CalledProcessError):
                builder.build_release(self.data, self.releases)
        self.assertTrue(builder.ifixit_cached(self.data))
        self.assertEqual((self.data / "runtime/guides/ifixit.json").read_text(), "ifixit guide")
        self.assert_not_published(previous)
        self.commands.clear()
        with patch.object(builder.subprocess, "run", side_effect=self.fake_command):
            builder.build_release(self.data, self.releases)
        self.assertEqual(self.commands, [builder.SEED_NOUNS, builder.WIKIMED, builder.FIRST_AID, builder.MERGE, builder.LINK_NOUNS, builder.ARCHETYPES, builder.SEARCH_INDEX])

    def test_incomplete_wikimed_cannot_reach_merge(self):
        previous = self.previous_release()
        def incomplete(args, **kwargs):
            result = self.fake_command(args, **kwargs)
            if self.script(args) == builder.WIKIMED:
                self.update_manifest(self.data / "wikimed/runtime", zimChecksumVerified=False)
            return result
        with patch.object(builder.subprocess, "run", side_effect=incomplete):
            with self.assertRaisesRegex(RuntimeError, "complete checksum-verified"):
                builder.build_release(self.data, self.releases)
        self.assertNotIn(builder.MERGE, self.commands)
        self.assert_not_published(previous)

    def test_pdf_failure_or_missing_document_proof_cannot_reach_merge(self):
        previous = self.previous_release()
        def fail_pdf(args, **kwargs):
            if self.script(args) == builder.FIRST_AID:
                raise subprocess.CalledProcessError(1, args)
            return self.fake_command(args, **kwargs)
        with patch.object(builder.subprocess, "run", side_effect=fail_pdf):
            with self.assertRaises(subprocess.CalledProcessError):
                builder.build_release(self.data, self.releases)
        self.assertNotIn(builder.MERGE, self.commands)
        self.assert_not_published(previous)
        self.commands.clear()
        def unverified_pdf(args, **kwargs):
            result = self.fake_command(args, **kwargs)
            if self.script(args) == builder.FIRST_AID:
                self.update_manifest(self.data / "first-aid/runtime", documentVerified=False)
            return result
        with patch.object(builder.subprocess, "run", side_effect=unverified_pdf):
            with self.assertRaisesRegex(RuntimeError, "checksum-verified"):
                builder.build_release(self.data, self.releases)
        self.assertNotIn(builder.MERGE, self.commands)
        self.assert_not_published(previous)

    def test_missing_merged_aliases_cannot_reach_index_preparation(self):
        def missing_aliases(args, **kwargs):
            result = self.fake_command(args, **kwargs)
            if self.script(args) == builder.MERGE:
                self.update_manifest(self.argument(args, "--runtime-dir"), searchEnrichment={})
            return result
        with patch.object(builder.subprocess, "run", side_effect=missing_aliases):
            with self.assertRaisesRegex(RuntimeError, "Merged alias enrichment did not finish"):
                builder.build_release(self.data, self.releases)
        self.assertNotIn(builder.SEARCH_INDEX, self.commands)
        self.assert_not_published()

    def test_search_index_failure_cannot_publish_enriched_data(self):
        previous = self.previous_release()
        def fail_index(args, **kwargs):
            if self.script(args) == builder.SEARCH_INDEX:
                raise subprocess.CalledProcessError(1, args)
            return self.fake_command(args, **kwargs)
        with patch.object(builder.subprocess, "run", side_effect=fail_index):
            with self.assertRaises(subprocess.CalledProcessError):
                builder.build_release(self.data, self.releases)
        manifest = json.loads((self.data / "runtime/manifest.json").read_bytes())
        self.assertEqual(manifest["searchEnrichment"], {"version": 1})
        self.assert_not_published(previous)

    def test_missing_search_index_metadata_cannot_publish(self):
        def skip_index(args, **kwargs):
            if self.script(args) == builder.SEARCH_INDEX:
                return subprocess.CompletedProcess(args, 0)
            return self.fake_command(args, **kwargs)
        with patch.object(builder.subprocess, "run", side_effect=skip_index):
            with self.assertRaisesRegex(RuntimeError, "Inverted search index did not finish"):
                builder.build_release(self.data, self.releases)
        self.assert_not_published()

    def test_pipeline_key_tracks_every_script_and_pinned_runtime(self):
        original = builder.pipeline_key(self.data / "stage3_cache")
        for name in builder.PIPELINE_INPUT_NAMES:
            with self.subTest(input=name):
                path = getattr(builder, name)
                data = path.read_bytes()
                path.write_bytes(data + b"# changed\n")
                self.assertNotEqual(builder.pipeline_key(self.data / "stage3_cache"), original)
                path.write_bytes(data)
                self.assertEqual(builder.pipeline_key(self.data / "stage3_cache"), original)
        for name in ("PYTHON_VERSION", "UV_VERSION"):
            with self.subTest(input=name), patch.object(builder, name, "changed"):
                self.assertNotEqual(builder.pipeline_key(self.data / "stage3_cache"), original)

    def test_inputs_changed_during_build_cannot_publish_under_old_fingerprint(self):
        previous = self.previous_release()
        original_key = builder.pipeline_key(self.data / "stage3_cache")
        def changed_inputs(args, **kwargs):
            result = self.fake_command(args, **kwargs)
            if self.script(args) == builder.SEARCH_INDEX:
                builder.WIKIMED.write_text("# changed while building\n")
            return result
        with patch.object(builder.subprocess, "run", side_effect=changed_inputs):
            with self.assertRaisesRegex(RuntimeError, "inputs changed during ingestion"):
                builder.build_release(self.data, self.releases)
        self.assertFalse((self.releases / original_key).exists())
        self.assert_not_published(previous)

    def test_repack_snapshot_uses_same_noun_stages_without_ingestion_or_body_reads(self):
        source = self.root / "existing"
        self.write_runtime(source, ["ifixit", "wikimed", "first-aid"])
        before_manifest = (source / "manifest.json").read_bytes()
        read = Path.read_bytes
        def compact_only(path):
            self.assertNotIn(path.parent.name, {"guides", "media"}, "Repacking must never parse raw bodies")
            return read(path)
        with patch.object(builder.subprocess, "run", side_effect=self.fake_command), patch.object(Path, "read_bytes", compact_only):
            release = builder.build_release(self.data, self.releases, source_runtime=source)
        self.assertEqual(self.commands, [builder.SEED_NOUNS, builder.LINK_NOUNS,
                                         builder.ARCHETYPES, builder.SEARCH_INDEX])
        self.assertEqual((source / "manifest.json").read_bytes(), before_manifest)
        self.assertEqual((release / "guides/wikimed.json").stat().st_ino,
                         (source / "guides/wikimed.json").stat().st_ino)
        self.assertFalse((release / "gaps.json").exists())
        self.assertFalse((release / "catalog.sqlite").exists())
        self.assertEqual(release.stat().st_mode & 0o777, 0o755)
        with patch.object(builder.subprocess, "run") as run:
            self.assertEqual(builder.build_release(self.data, self.releases, source_runtime=source), release)
            run.assert_not_called()

    def test_repack_source_identity_changes_key(self):
        source = self.root / "existing"
        self.write_runtime(source, ["ifixit", "wikimed", "first-aid"])
        key = builder.pipeline_key(self.data / "stage3_cache", source)
        manifest = json.loads((source / "manifest.json").read_bytes())
        manifest["extraSourceMetadata"] = "changed"
        (source / "manifest.json").write_text(json.dumps(manifest))
        self.assertNotEqual(builder.pipeline_key(self.data / "stage3_cache", source), key)

    def test_processed_repack_rejects_changed_overlay_cache(self):
        source = self.root / "existing"
        self.write_runtime(source, ["ifixit", "wikimed", "first-aid"])
        self.update_manifest(source, structuredGuides={"processedItems": 1, "cacheSha256": "different"})
        with patch.object(builder.subprocess, "run", side_effect=self.fake_command):
            with self.assertRaisesRegex(RuntimeError, "Processed source cache differs"):
                builder.build_release(self.data, self.releases, source_runtime=source)
        self.assertNotIn(builder.LINK_NOUNS, self.commands)
        self.assertEqual(list(self.data.glob(".merge-*")), [])

    def test_failed_archetype_stage_never_publishes(self):
        def fail(args, **kwargs):
            if self.script(args) == builder.ARCHETYPES:
                raise subprocess.CalledProcessError(1, args)
            return self.fake_command(args, **kwargs)
        with patch.object(builder.subprocess, "run", side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                builder.build_release(self.data, self.releases)
        self.assert_not_published()


class ConsoleKeyTests(unittest.TestCase):
    def test_key_must_come_from_console_host_key_block(self):
        wire = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + b"x" * 32
        key = "ssh-ed25519 " + base64.b64encode(wire).decode()
        console = "-----BEGIN SSH HOST KEY KEYS-----\n" + key + " root@host\n-----END SSH HOST KEY KEYS-----"
        expected = "32.193.25.68 " + key
        self.assertEqual(host_keys.console_host_key(console, "32.193.25.68"), expected)
        self.assertEqual(host_keys.console_host_key(base64.b64encode(console.encode()).decode() + "\n", "32.193.25.68"), expected)
        self.assertIsNone(host_keys.console_host_key(key, "32.193.25.68"))


if __name__ == "__main__":
    unittest.main()
