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
        self.ingest = self.root / "ingest.py"
        self.ingest.write_text("# pinned fixture input\n")
        self.input = patch.object(builder, "INGEST", self.ingest)
        self.input.start()
        self.addCleanup(self.input.stop)
        self.space = patch.object(builder.shutil, "disk_usage", return_value=type("Space", (), {"free": 64 * 1024**3})())
        self.space.start()
        self.addCleanup(self.space.stop)

    def fake_command(self, args, **kwargs):
        if args[1:] == ["--version"]:
            return subprocess.CompletedProcess(args, 0, "uv " + builder.UV_VERSION + "\n")
        self.assertIn(builder.PYTHON_VERSION, args)
        self.assertEqual(args[-2:], ["--data-dir", str(self.data)])
        runtime = self.data / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "guides").mkdir(exist_ok=True)
        (runtime / "media").mkdir(exist_ok=True)
        index = b'{"items":[["fixture","Example repair"]]}'
        packed = gzip.compress(index, mtime=0)
        (runtime / "index.json").write_bytes(index)
        (runtime / "index.json.gz").write_bytes(packed)
        manifest = {"runtimeComplete": True, "archiveChecksumVerified": True, "zimChecksumVerified": True,
                    "archiveSha256": "a" * 64, "expectedSha256": "a" * 64,
                    "revision": "fixture", "indexedItems": 1, "uniqueMediaFiles": 1,
                    "indexBytes": len(index), "indexGzipBytes": len(packed)}
        (runtime / "manifest.json").write_text(json.dumps(manifest))
        return subprocess.CompletedProcess(args, 0)

    def test_publish_then_reuse_without_running_ingestion(self):
        with patch.object(builder.subprocess, "run", side_effect=self.fake_command) as run:
            release = builder.build_release(self.data, self.releases)
            self.assertEqual(run.call_count, 2)
        self.assertEqual(release.name, builder.pipeline_key())
        self.assertFalse((self.data / "runtime").exists())
        receipt = json.loads((release / "ingest.json").read_text())
        self.assertEqual(receipt["pipelineSha256"], release.name)
        self.assertEqual(receipt["pythonVersion"], builder.PYTHON_VERSION)
        with patch.object(builder.subprocess, "run") as run:
            self.assertEqual(builder.build_release(self.data, self.releases), release)
            run.assert_not_called()

    def test_failed_build_leaves_previous_release_untouched(self):
        previous = self.releases / "previous"
        previous.mkdir(parents=True)
        (previous / "live").write_text("keep")
        def fail(args, **kwargs):
            result = self.fake_command(args, **kwargs)
            if args[1] == "run":
                raise subprocess.CalledProcessError(1, args)
            return result
        with patch.object(builder.subprocess, "run", side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                builder.build_release(self.data, self.releases)
        self.assertEqual((previous / "live").read_text(), "keep")
        self.assertFalse((self.releases / builder.pipeline_key()).exists())

    def test_incomplete_metadata_cannot_publish(self):
        def incomplete(args, **kwargs):
            result = self.fake_command(args, **kwargs)
            if args[1] == "run":
                manifest = self.data / "runtime/manifest.json"
                data = json.loads(manifest.read_text())
                data["runtimeComplete"] = False
                manifest.write_text(json.dumps(data))
            return result
        with patch.object(builder.subprocess, "run", side_effect=incomplete):
            with self.assertRaisesRegex(RuntimeError, "complete checksum-verified"):
                builder.build_release(self.data, self.releases)
        self.assertFalse((self.releases / builder.pipeline_key()).exists())

    def test_changed_pipeline_builds_a_separate_release(self):
        with patch.object(builder.subprocess, "run", side_effect=self.fake_command):
            first = builder.build_release(self.data, self.releases)
            first_receipt = (first / "ingest.json").read_bytes()
            self.ingest.write_text("# changed pipeline input\n")
            second = builder.build_release(self.data, self.releases)
        self.assertNotEqual(first, second)
        self.assertEqual((first / "ingest.json").read_bytes(), first_receipt)
        self.assertTrue((second / "ingest.json").is_file())

    def test_concurrent_build_is_rejected(self):
        self.data.mkdir()
        with (self.data / ".build.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaisesRegex(RuntimeError, "Another catalog build"):
                builder.build_release(self.data, self.releases)


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
