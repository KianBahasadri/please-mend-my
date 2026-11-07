import asyncio
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import restructure_catalog as processor
from structured_guides import cache_key, canonical_bytes, raw_digest, validate_v2


def raw_guide(number=1):
    return {"id": f"{number:020x}", "title": "Test repair " + str(number), "category": "technology",
            "kind": "guide", "device": "Test device", "summary": "Source summary",
            "html": '<h2>Safety</h2><p>Do not work while powered. Wait 30 seconds.</p>'
                    '<h2>Steps</h2><p>Unplug the device.</p><pre>if ready:\n    repair()\n</pre>'
                    '<a href="https://example.org/manual">Manual</a>',
            "sourceUrl": "https://example.org/source", "sourceName": "Test source",
            "license": {"name": "Public domain", "url": "https://example.org/license"},
            "attribution": "Test author", "images": [], "related": []}


def generated():
    return {"summary": "Unplug before repair.", "tools": [], "materials": [],
            "safety": [{"level": "warning", "text": "Do not work while powered. Wait 30 seconds."}],
            "symptoms": [], "steps": [{"title": "Unplug", "body": "Unplug the device.",
                                        "warnings": [], "images": [], "code": ""}],
            "references": [{"title": "Manual", "url": "https://example.org/manual"}]}


class ProcessorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.runtime = self.root / "runtime"
        (self.runtime / "guides").mkdir(parents=True)
        self.cache = self.root / "cache"

    def store(self, raw):
        (self.runtime / "guides" / (raw["id"] + ".json")).write_bytes(canonical_bytes(raw))
        return {"id": raw["id"], "rawSha256": raw_digest(raw)}

    def batch(self, entries, **options):
        return asyncio.run(processor.process_batch(self.runtime, entries, self.cache, **options))

    def test_cleaner_preserves_safety_negation_quantities_and_literal_code(self):
        raw = raw_guide()
        raw["html"] = '<nav><div>Skip this menu</div></nav>' + raw["html"] + '<table><tr><td>3</td><td>mg</td></tr></table>'
        source = processor.prepare_source(raw)
        text = "\n".join(section["text"] for section in source["sections"])
        self.assertNotIn("Skip this menu", text)
        self.assertIn("Do not work while powered. Wait 30 seconds.", text)
        self.assertIn("3\tmg", text)
        self.assertIn({"heading": "Steps", "kind": "code", "text": "if ready:\n    repair()\n"}, source["sections"])
        self.assertEqual(source["sections"][0]["kind"], "safety")
        self.assertEqual(source["allowedReferences"], [
            {"title": "Manual", "url": "https://example.org/manual"},
            {"title": "Original source", "url": "https://example.org/source"}])

    def test_cleaner_retains_context_and_rejects_oversize_without_truncation(self):
        raw = raw_guide()
        raw["html"] += '<h2>History</h2><p>Historical source context.</p>'
        prepared = processor.prepare_source(raw)
        self.assertEqual(prepared["sections"][-1]["text"], "Historical source context.")
        with self.assertRaisesRegex(ValueError, "no truncation"):
            processor.prepare_source(raw, 10)

    def test_cleaner_filters_unsafe_assets_and_keeps_source_dates(self):
        raw = raw_guide()
        good = "/media/" + "a" * 64 + ".png"
        raw.update(publicationDate="2002-12-23", changeDate="2004-07-15")
        raw["html"] += f'<img src="{good}"><img src="https://evil.example/x.png"><a href="javascript:alert(1)">Bad</a>'
        prepared = processor.prepare_source(raw)
        self.assertEqual(prepared["allowedImages"], [good])
        self.assertEqual(prepared["publicationDate"], "2002-12-23")
        self.assertTrue(all(ref["url"].startswith("https:") for ref in prepared["allowedReferences"]))

    def test_request_enforces_schema_parameters_and_no_dynamic_tools(self):
        prepared = processor.prepare_source(raw_guide())
        config = processor.request_config(prepared, 160000, 16384, 0)
        payload = processor.api_payload(prepared, processor.DEFAULT_MODEL, config)
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertTrue(payload["provider"]["require_parameters"])
        self.assertEqual(payload["model"], "qwen/qwen3.6-35b-a3b")
        self.assertNotIn("tools", payload)
        self.assertIn("untrusted data", payload["messages"][0]["content"])

    def test_success_caches_content_and_repeat_uses_zero_api_calls_without_key(self):
        raw = raw_guide()
        entry = self.store(raw)
        calls = []
        def transport(payload, key, timeout):
            calls.append(payload)
            return generated(), {"cost": 0.01}
        result = self.batch([entry], api_key="test-key", transport=transport)
        self.assertEqual(result["counts"], {"processed": 1})
        self.assertEqual(result["apiCalls"], 1)
        active = json.loads((self.cache / (raw["id"] + ".json")).read_bytes())
        self.assertEqual(active["version"], 2)
        self.assertNotIn("html", active)
        self.assertEqual(active["attribution"], raw["attribution"])
        repeated = self.batch([entry], transport=lambda *_: self.fail("Cache hit called API"))
        self.assertEqual(repeated["counts"], {"cached": 1})
        self.assertEqual(repeated["apiCalls"], 0)
        self.assertEqual(len(calls), 1)

    def test_missing_active_record_is_recovered_from_sha_object(self):
        raw = raw_guide()
        entry = self.store(raw)
        self.batch([entry], api_key="test-key", transport=lambda *_: (generated(), {}))
        active = self.cache / (raw["id"] + ".json")
        before = active.read_bytes()
        active.unlink()
        result = self.batch([entry])
        self.assertEqual(result["counts"], {"cached": 1})
        self.assertEqual(active.read_bytes(), before)

    def test_raw_or_request_config_change_does_not_reuse_cached_output(self):
        raw = raw_guide()
        entry = self.store(raw)
        self.batch([entry], api_key="test-key", transport=lambda *_: (generated(), {}))
        changed_config = self.batch([entry], max_tokens=8192, dry_run=True)
        self.assertEqual(changed_config["counts"], {"pending": 1})
        raw["summary"] = "Changed source"
        new_entry = self.store(raw)
        changed_raw = self.batch([new_entry], dry_run=True)
        self.assertEqual(changed_raw["counts"], {"pending": 1})
        self.assertNotEqual(changed_config["entries"][0]["cacheKey"], changed_raw["entries"][0]["cacheKey"])

    def test_dry_run_never_calls_api_or_writes_activation(self):
        entry = self.store(raw_guide())
        result = self.batch([entry], api_key="unused", dry_run=True,
                            transport=lambda *_: self.fail("Dry run called API"))
        self.assertEqual(result["counts"], {"pending": 1})
        self.assertFalse((self.cache / (entry["id"] + ".json")).exists())

    def test_retry_backoff_only_for_transient_errors_and_counts_all_calls(self):
        entry = self.store(raw_guide())
        calls, waits = [], []
        def transport(*_):
            calls.append(1)
            if len(calls) < 3:
                raise processor.APIError("Busy", retryable=True, retry_after=3)
            return generated(), {}
        async def sleep(seconds):
            waits.append(seconds)
        result = self.batch([entry], api_key="test-key", transport=transport, sleep=sleep)
        self.assertEqual(result["counts"], {"processed": 1})
        self.assertEqual(result["apiCalls"], 3)
        self.assertEqual(waits, [3, 3])
        self.assertEqual(processor.retry_delay("9999"), 30)
        self.assertEqual(processor.retry_delay("not a number"), 0)

    def test_failed_api_requests_are_counted_and_never_cached(self):
        entry = self.store(raw_guide())
        async def sleep(_):
            pass
        def transport(*_):
            raise processor.APIError("Busy", retryable=True)
        result = self.batch([entry], api_key="test-key", transport=transport, sleep=sleep)
        self.assertEqual(result["counts"], {"failed": 1})
        self.assertEqual(result["apiCalls"], 3)
        self.assertEqual(list(self.cache.glob("*.json")), [])
        self.assertEqual(list((self.cache / "objects").glob("*.json")), [])
        self.assertEqual(list((self.cache / "responses").glob("*.json")), [])

    def test_invalid_generation_does_not_retry_or_cache_and_other_entries_continue(self):
        entries = [self.store(raw_guide(i)) for i in (1, 2)]
        calls = []
        def transport(payload, *_):
            calls.append(1)
            if "Test repair 1" in payload["messages"][1]["content"]:
                result = generated()
                result["steps"][0]["body"] = {"invalid": "not a plain string"}
                return result, {}
            return generated(), {}
        result = self.batch(entries, api_key="test-key", transport=transport)
        self.assertEqual(result["counts"], {"failed": 1, "processed": 1})
        self.assertEqual(result["apiCalls"], 2)
        self.assertFalse((self.cache / (entries[0]["id"] + ".json")).exists())
        self.assertEqual(len(list((self.cache / "responses").glob("*.json"))), 1)

    def test_optional_asset_filter_preserves_prose_and_source_assets_without_mutation(self):
        raw, content = raw_guide(), generated()
        good_image = "/media/" + "a" * 64 + ".png"
        bad_image = "/media/" + "b" * 64 + ".png"
        raw["html"] += '<img src="' + good_image + '">'
        content["steps"][0].update(images=[good_image, bad_image, good_image, "https://example.org/image.png"],
                                   warnings=["Do not remove more than 3 mg."], code="if ready:\n    repair()")
        content["references"] += [
            {"title": "Duplicate manual", "url": "https://example.org/manual"},
            {"title": "Original", "url": raw["sourceUrl"]},
            {"title": "Invented", "url": "https://example.org/invented"},
            {"title": "Unsafe", "url": "javascript:alert(1)"}]
        before_raw, before_content = copy.deepcopy(raw), copy.deepcopy(content)
        filtered, removed = processor.filter_optional_assets(raw, content)
        self.assertEqual(removed, {"references": 3, "images": 3})
        self.assertEqual(filtered["references"], [content["references"][0], content["references"][2]])
        self.assertEqual(filtered["steps"][0]["images"], [good_image])
        for field in ("summary", "tools", "materials", "safety", "symptoms"):
            self.assertEqual(filtered[field], content[field])
        for field in ("title", "body", "warnings", "code"):
            self.assertEqual(filtered["steps"][0][field], content["steps"][0][field])
        self.assertEqual(raw, before_raw)
        self.assertEqual(content, before_content)

    def test_filtered_generation_retains_original_response_but_only_activates_valid_assets(self):
        raw, content = raw_guide(), generated()
        entry = self.store(raw)
        content["references"].append({"title": "Invented", "url": "https://example.org/invented"})
        content["steps"][0]["images"] = ["/media/" + "b" * 64 + ".png"]
        result = self.batch([entry], api_key="test-key", transport=lambda *_: (content, {"cost": 0.01}))
        self.assertEqual(result["counts"], {"processed": 1})
        self.assertEqual(result["entries"][0]["assetsRemoved"], {"references": 1, "images": 1})
        active = json.loads((self.cache / (entry["id"] + ".json")).read_bytes())
        validate_v2(active, raw)
        self.assertEqual(active["steps"][0]["images"], [])
        self.assertEqual(active["references"], generated()["references"])
        response = json.loads(next((self.cache / "responses").glob("*.json")).read_bytes())
        self.assertEqual(response["content"], content)
        self.assertEqual(response["usage"], {"cost": 0.01})

    def test_cached_response_recovers_after_publication_failure_without_an_api_call(self):
        raw, content = raw_guide(), generated()
        entry = self.store(raw)
        content["references"].append({"title": "Unsupported", "url": "https://example.org/not-in-source"})
        original_write = processor.atomic_json
        def fail_after_response(path, value):
            if Path(path).parent.name == "objects":
                raise OSError("Simulated interruption before activation")
            original_write(path, value)
        with patch.object(processor, "atomic_json", side_effect=fail_after_response):
            first = self.batch([entry], api_key="test-key", transport=lambda *_: (content, {}))
        self.assertEqual(first["counts"], {"failed": 1})
        self.assertEqual(first["apiCalls"], 1)
        self.assertEqual(len(list((self.cache / "responses").glob("*.json"))), 1)
        self.assertFalse((self.cache / (entry["id"] + ".json")).exists())
        preflight = self.batch([entry], dry_run=True, transport=lambda *_: self.fail("Preflight paid for cached response"))
        self.assertEqual(preflight["counts"], {"response-cached": 1})
        self.assertFalse((self.cache / (entry["id"] + ".json")).exists())
        recovered = self.batch([entry], transport=lambda *_: self.fail("Recovery paid for cached response"))
        self.assertEqual(recovered["counts"], {"reprocessed": 1})
        self.assertEqual(recovered["apiCalls"], 0)
        self.assertEqual(recovered["entries"][0]["assetsRemoved"], {"references": 1, "images": 0})
        validate_v2(json.loads((self.cache / (entry["id"] + ".json")).read_bytes()), raw)

    def test_response_cache_rejects_changed_pins_invalid_shape_and_unknown_metadata(self):
        raw = raw_guide()
        prepared = processor.prepare_source(raw)
        config = processor.request_config(prepared, processor.MAX_INPUT_CHARS, 16384, 0)
        key = cache_key(raw, processor.DEFAULT_MODEL, config=config)
        record = processor.response_record(raw, generated(), processor.DEFAULT_MODEL, key, config)
        path = self.root / "response.json"
        processor.atomic_json(path, record)
        self.assertEqual(processor.valid_response(path, raw, key, processor.DEFAULT_MODEL, config), record)
        for field, value in (("id", "f" * 20), ("rawSha256", "f" * 64), ("cacheKey", "f" * 64),
                             ("model", "different/model"), ("promptVersion", "future"),
                             ("schemaVersion", 3), ("configuration", {}), ("content", {}),
                             ("extra", "not accepted"), ("usage", {"secret": "never accepted"})):
            with self.subTest(field=field):
                changed = copy.deepcopy(record)
                changed[field] = value
                processor.atomic_json(path, changed)
                self.assertIsNone(processor.valid_response(path, raw, key, processor.DEFAULT_MODEL, config))
        with self.assertRaisesRegex(ValueError, "key does not match"):
            processor.response_record(raw, generated(), processor.DEFAULT_MODEL, "f" * 64, config)

    def test_incomplete_and_schema_invalid_http_responses_never_enter_any_cache(self):
        entry = self.store(raw_guide())
        cases = [("length", json.dumps(generated()), None),
                 ("stop", json.dumps(generated()), "private refusal text"),
                 ("stop", "{truncated", None), ("stop", "{}", None),
                 (["untrusted remote value"], "{}", None)]
        for reason, content, refusal in cases:
            with self.subTest(reason=reason, refusal=bool(refusal)):
                response = {"model": processor.DEFAULT_MODEL, "choices": [{"finish_reason": reason,
                            "message": {"content": content, "refusal": refusal}}]}
                with patch.object(processor, "urlopen", return_value=io.BytesIO(json.dumps(response).encode())):
                    result = self.batch([entry], api_key="test-key")
                self.assertEqual(result["counts"], {"failed": 1})
                self.assertEqual(result["apiCalls"], 1)
                if reason == "length" or refusal or isinstance(reason, list):
                    self.assertEqual(result["entries"][0]["finishReason"], reason if isinstance(reason, str) else "unknown")
                    self.assertIs(result["entries"][0]["refusal"], bool(refusal))
                self.assertNotIn("private refusal", json.dumps(result))
                self.assertNotIn("untrusted remote", json.dumps(result))
                for directory in (self.cache, self.cache / "objects", self.cache / "responses"):
                    self.assertEqual(list(directory.glob("*.json")), [])

    def test_stale_pilot_and_oversize_fail_before_any_request(self):
        raw = raw_guide()
        entry = self.store(raw)
        raw["summary"] = "Changed"
        self.store(raw)
        result = self.batch([entry], api_key="test-key", transport=lambda *_: self.fail("Stale source called API"))
        self.assertEqual(result["counts"], {"failed": 1})
        self.assertEqual(result["apiCalls"], 0)
        oversize = self.batch([self.store(raw)], max_chars=10, api_key="test-key",
                              transport=lambda *_: self.fail("Oversize called API"))
        self.assertEqual(oversize["apiCalls"], 0)

    def test_concurrency_is_bounded(self):
        entries = [self.store(raw_guide(i)) for i in range(1, 7)]
        mutex = threading.Lock()
        current, maximum = 0, 0
        def transport(*_):
            nonlocal current, maximum
            with mutex:
                current += 1
                maximum = max(maximum, current)
            time.sleep(0.01)
            with mutex:
                current -= 1
            return generated(), {}
        result = self.batch(entries, api_key="test-key", transport=transport, concurrency=2)
        self.assertEqual(result["counts"], {"processed": 6})
        self.assertLessEqual(maximum, 2)
        self.assertEqual(current, 0)

    def test_default_transport_can_run_forty_simultaneous_requests(self):
        entries = [self.store(raw_guide(i)) for i in range(1, 41)]
        barrier = threading.Barrier(40, timeout=10)
        mutex = threading.Lock()
        active, maximum = 0, 0
        def transport(*_):
            nonlocal active, maximum
            with mutex:
                active += 1
                maximum = max(maximum, active)
            barrier.wait()
            with mutex:
                active -= 1
            return generated(), {}
        result = self.batch(entries, api_key="test-key", transport=transport)
        self.assertEqual(result["counts"], {"processed": 40})
        self.assertEqual(result["apiCalls"], 40)
        self.assertEqual(maximum, 40)
        self.assertEqual(active, 0)

    def test_invalid_concurrency_is_rejected_before_resources_or_requests(self):
        for value in (0, -1, 129, True, 4.0, "40"):
            with self.subTest(concurrency=value):
                with self.assertRaisesRegex(ValueError, "Invalid concurrency"):
                    self.batch([], concurrency=value,
                               transport=lambda *_: self.fail("Invalid concurrency reached API"))

    def test_http_completion_validates_model_finish_content_and_keeps_numeric_usage(self):
        payload = {"model": processor.DEFAULT_MODEL}
        response = {"model": processor.DEFAULT_MODEL, "choices": [{"finish_reason": "stop",
                    "message": {"content": json.dumps(generated())}}],
                    "usage": {"prompt_tokens": 50, "cost": 0.1, "private": "never recorded"}}
        class Reply(io.BytesIO):
            pass
        with patch.object(processor, "urlopen", return_value=Reply(json.dumps(response).encode())):
            content, usage = processor.http_completion(payload, "test-key")
        self.assertEqual(content, generated())
        self.assertEqual(usage, {"prompt_tokens": 50, "cost": 0.1})
        for field, value in (("model", "wrong/model"), ("choices", [{"finish_reason": "length", "message": {"content": "{}"}}])):
            invalid = copy.deepcopy(response)
            invalid[field] = value
            with patch.object(processor, "urlopen", return_value=Reply(json.dumps(invalid).encode())):
                with self.assertRaises(processor.APIError):
                    processor.http_completion(payload, "test-key")

    def test_http_errors_redact_response_body_and_authentication_does_not_retry(self):
        for code, retryable in ((401, False), (400, False), (429, True), (503, True)):
            stream = io.BytesIO(b"secret-token")
            error = HTTPError(processor.API_URL, code, "private remote body", {"Retry-After": "4"}, stream)
            with patch.object(processor, "urlopen", side_effect=error):
                with self.assertRaises(processor.APIError) as caught:
                    processor.http_completion({"model": processor.DEFAULT_MODEL}, "secret-token")
            self.assertNotIn("secret", str(caught.exception))
            self.assertNotIn("private", str(caught.exception))
            self.assertEqual(caught.exception.retryable, retryable)
            self.assertTrue(stream.closed)

    def test_malformed_environment_credentials_fail_without_network_or_secret_receipts(self):
        secret = "fake-credential-sensitive"
        entry = self.store(raw_guide())
        for suffix in ("\nInjected: value", "\r", "\x00", " space", "\u0100"):
            malformed = secret + suffix
            with patch.object(processor.os, "environ", {"OPENROUTER_API_KEY": malformed}):
                with self.assertRaises(ValueError) as caught:
                    processor.load_api_key(env_file=self.root / "missing.env")
            self.assertNotIn(secret, str(caught.exception))
            with patch.object(processor, "urlopen", side_effect=AssertionError("Invalid credential reached network")):
                result = self.batch([entry], api_key=malformed)
            self.assertEqual(result["counts"], {"failed": 1})
            self.assertEqual(result["apiCalls"], 0)
            self.assertNotIn(secret, json.dumps(result))
            self.assertEqual(list(self.cache.glob("*.json")), [])

    def test_unexpected_urllib_header_errors_are_redacted(self):
        secret = "fake-credential-sensitive"
        error = ValueError("Invalid header value b'Bearer " + secret + "'")
        with patch.object(processor, "urlopen", side_effect=error):
            with self.assertRaises(processor.APIError) as caught:
                processor.http_completion({"model": processor.DEFAULT_MODEL}, secret)
        self.assertNotIn(secret, str(caught.exception))
        self.assertFalse(caught.exception.retryable)

    def test_literal_env_lookup_never_expands_or_executes_other_assignments(self):
        env = self.root / ".env"
        env.write_text('UNRELATED=$(touch never)\nexport OPENROUTER_API_KEY="literal-$value-$(no)" # comment\n')
        env.chmod(0o600)
        with patch.dict(processor.os.environ, {}, clear=True):
            self.assertEqual(processor.load_api_key(env_file=env), "literal-$value-$(no)")
        with patch.dict(processor.os.environ, {"OPENROUTER_API_KEY": "from-env"}, clear=True):
            self.assertEqual(processor.load_api_key(env_file=env), "from-env")
        env.chmod(0o644)
        with patch.dict(processor.os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "private"):
                processor.load_api_key(env_file=env)

    def test_overlapping_cli_cache_runs_fail_before_paid_work(self):
        with processor.batch_lock(self.cache):
            with self.assertRaisesRegex(ValueError, "Another restructuring"):
                with processor.batch_lock(self.cache):
                    self.fail("Overlapping run acquired lock")

    def test_pilot_selection_is_pinned_unique_deterministic_and_reports_gaps(self):
        rows = []
        for i in range(1, 5):
            raw = raw_guide(i)
            raw["title"] = "Guide " + str(i)
            self.store(raw)
            rows.append([raw[key] for key in ("id", "title", "category", "kind", "device")])
        index = {"fields": ["id", "title", "category", "kind", "device"], "revision": "fixture", "items": rows}
        (self.runtime / "index.json").write_bytes(canonical_bytes(index))
        topics = {"buckets": [{"id": str(i), "quota": 1, "categories": ["technology"], "include": "Guide",
                               "topics": [{"name": "Wanted " + str(i), "patterns": ["^Guide " + str(i) + "$"]}]} for i in range(1, 5)],
                  "requestedExamples": [{"bucket": "1", "name": "Missing example", "pattern": "Absent"}]}
        topic_path = self.root / "topics.json"
        topic_path.write_bytes(canonical_bytes(topics))
        first = processor.select_pilot(self.runtime, topic_path)
        self.assertEqual(first, processor.select_pilot(self.runtime, topic_path))
        self.assertEqual(first["count"], 4)
        self.assertEqual(len(set(entry["id"] for entry in first["entries"])), 4)
        self.assertEqual(first["indexSha256"], hashlib.sha256((self.runtime / "index.json").read_bytes()).hexdigest())
        self.assertEqual(first["gaps"][0]["topic"], "Missing example")
        manifest = self.root / "pilot.json"
        processor.atomic_json(manifest, first)
        self.assertEqual(processor.pilot_entries(manifest), first["entries"])

    def test_atomic_cache_write_never_mutates_its_source(self):
        value = {"a": ["source"]}
        before = copy.deepcopy(value)
        destination = self.root / "cache.json"
        processor.atomic_json(destination, value)
        self.assertEqual(value, before)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o644)
        self.assertEqual(json.loads(destination.read_bytes()), value)


if __name__ == "__main__":
    unittest.main()
