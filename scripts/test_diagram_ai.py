"""Immutable remote cache, strict schemas, and actual executor concurrency."""
import asyncio
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import diagram_ai as ai
import diagram_batch as diagrams
from structured_guides import _object, _text

PROMPT = "Fixture diagram prompt"


class DiagramAITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_cache_key_covers_source_schema_prompt_and_config(self):
        def key_for(*args):
            return ai.cache_key(*args, system_prompt=PROMPT)
        schema = _object({"answer": _text(100, 1)})
        key = key_for("mapping", {"sourceSha256": "a"}, schema, "model", {"maxTokens": 10})
        self.assertNotEqual(key, ai.cache_key("mapping", {"sourceSha256": "a"}, schema, "model",
                                        {"maxTokens": 10}, system_prompt=PROMPT + " changed"))
        other_schema = _object({"answer": _text(90, 1)})
        self.assertNotEqual(key, key_for("mapping", {"sourceSha256": "a"}, other_schema, "model", {"maxTokens": 10}))
        with patch.object(ai, 'COMPILER_VERSION', 'changed'):
            self.assertNotEqual(key, key_for("mapping", {"sourceSha256": "a"}, schema, "model", {"maxTokens": 10}))
        for change in ({"sourceSha256": "b"}, {"sourceSha256": "a", "mapping": "changed"}):
            self.assertNotEqual(key, key_for("mapping", change, schema, "model", {"maxTokens": 10}))
        self.assertNotEqual(key, key_for("mapping", {"sourceSha256": "a"}, schema, "other", {"maxTokens": 10}))
        self.assertNotEqual(key, key_for("mapping", {"sourceSha256": "a"}, schema, "model", {"maxTokens": 11}))
        first = {"sourceSha256": "a", "visualEvidence": [{"id": "one", "sha256": "image-one"}]}
        second = {"sourceSha256": "a", "visualEvidence": [{"id": "one", "sha256": "image-two"}]}
        self.assertNotEqual(key_for("mapping", first, schema, "model", {}),
                        key_for("mapping", second, schema, "model", {}))

    def test_batch_reuses_valid_cache_offline_with_no_transport_calls(self):
        schema = _object({"answer": _text(100, 1)})
        job = {"id": "phone", "input": {"source": "pinned"}, "schema": schema, "systemPrompt": PROMPT}
        calls = []
        def transport(payload, key, contract):
            calls.append(payload)
            return {"answer": "valid"}, {"total_tokens": 12, "cost": .001}
        first = asyncio.run(ai.batch("mapping", [job], self.root, api_key="test", transport=transport))
        second = asyncio.run(ai.batch("mapping", [job], self.root, api_key=None, transport=transport))
        self.assertEqual(first["apiCalls"], 1)
        self.assertEqual(second["apiCalls"], 0)
        self.assertEqual(second["cacheHits"], 1)
        self.assertEqual(second["entries"][0]["content"], {"answer": "valid"})
        self.assertEqual(len(calls), 1)

    def test_accounting_recovers_interrupted_successes_and_does_not_double_count_receipts(self):
        paid = {"version": 1, "stage": "mapping", "apiCalls": 2, "entries": [
            {"cacheKey": "one", "apiCalls": 1, "content": {}},
            {"cacheKey": "failed", "apiCalls": 1, "error": "Incomplete"}]}
        ai.save(self.root / "mapping-receipt.json", paid)
        ai.save(self.root / "receipts" / "same-receipt.json", paid)
        ai.save(self.root / "cache/mapping/one.json", {"cacheKey": "one", "usage": {"cost": .01}})
        ai.save(self.root / "cache/mapping/interrupted.json", {"cacheKey": "interrupted", "usage": {"cost": .02}})
        result = diagrams.lifetime_accounting(self.root)
        self.assertEqual(result["knownApiCalls"], 3)
        self.assertEqual(result["acceptedResponses"], 2)
        self.assertEqual(result["callsRecoveredFromInterruptedCaches"], 1)
        self.assertEqual(result["failedAttemptsWithUnreportedUsage"], 1)
        self.assertAlmostEqual(result["acceptedUsage"]["cost"], .03)

    def test_invalid_complete_response_is_never_cached(self):
        schema = _object({"answer": _text(100, 1)})
        job = {"id": "phone", "input": {}, "schema": schema, "systemPrompt": PROMPT}
        def transport(*args):
            return {"wrong": "field"}, {}
        receipt = asyncio.run(ai.batch("mapping", [job], self.root, api_key="test", transport=transport))
        self.assertIn("error", receipt["entries"][0])
        self.assertEqual(list((self.root / "cache").rglob("*.json")) if (self.root / "cache").exists() else [], [])

    def test_transport_pool_really_supports_forty_requests(self):
        schema = _object({"answer": _text(100, 1)})
        jobs = [{"id": str(i), "input": {"i": i}, "schema": schema, "systemPrompt": PROMPT} for i in range(42)]
        gate = threading.Event(); guard = threading.Lock()
        active = peak = 0
        def transport(*args):
            nonlocal active, peak
            with guard:
                active += 1; peak = max(peak, active)
                if active == 40:
                    gate.set()
            if not gate.wait(3):
                raise AssertionError("Default executor silently reduced requested concurrency")
            time.sleep(.01)
            with guard:
                active -= 1
            return {"answer": "valid"}, {}
        receipt = asyncio.run(ai.batch("mapping", jobs, self.root, api_key="test", transport=transport, concurrency=40))
        self.assertEqual(receipt["apiCalls"], 42)
        self.assertEqual(peak, 40)

if __name__ == "__main__":
    unittest.main()
