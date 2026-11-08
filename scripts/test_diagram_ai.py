"""Immutable remote cache, strict schemas, and actual executor concurrency."""
import asyncio
from contextlib import contextmanager
import json
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

    def test_vision_settings_and_actual_image_pins_invalidate_mapping_cache(self):
        schema = _object({'answer': _text(100, 1)})
        image = self.root / 'numbered.png'
        image.write_bytes(b'first numbered contact sheet')
        job = {'id': 'object', 'input': {}, 'schema': schema, 'systemPrompt': PROMPT,
               'images': [{'id': 'candidates', 'path': str(image),
                           'sha256': ai.digest_bytes(image.read_bytes())}]}
        calls = []
        def transport(payload, *_):
            calls.append(payload)
            return {'answer': 'valid'}, {'cost': .001}
        first = asyncio.run(ai.batch('mapping', [job], self.root, api_key='test', transport=transport))
        payload = calls[0]
        self.assertTrue(payload['reasoning']['enabled'])
        self.assertEqual(payload['messages'][1]['content'][-1]['image_url']['detail'], 'high')
        self.assertEqual(payload['max_tokens'], 8192)
        old_key = first['entries'][0]['cacheKey']
        offline = asyncio.run(ai.batch('mapping', [job], self.root, api_key=None, transport=transport))
        self.assertEqual(offline['cacheHits'], 1)
        image.write_bytes(b'different lit region')
        with self.assertRaisesRegex(ValueError, 'SHA pin'):
            asyncio.run(ai.batch('mapping', [job], self.root, api_key=None, transport=transport))
        job['images'][0]['sha256'] = ai.digest_bytes(image.read_bytes())
        changed = asyncio.run(ai.batch('mapping', [job], self.root, api_key=None, transport=transport))
        self.assertNotEqual(changed['entries'][0]['cacheKey'], old_key)
        self.assertEqual(changed['cacheHits'], 0)
        self.assertEqual(len(calls), 1)

    def test_taxonomy_keeps_legacy_identity_and_no_reasoning(self):
        schema = _object({'answer': _text(100, 1)})
        job = {'id': 'object', 'input': {}, 'schema': schema, 'systemPrompt': PROMPT}
        calls = []
        def transport(payload, *_):
            calls.append(payload)
            return {'answer': 'valid'}, {}
        receipt = asyncio.run(ai.batch('taxonomy', [job], self.root, api_key='test', transport=transport))
        config = {'maxTokens': 4096, 'temperature': 0, 'reasoningEnabled': False,
                  'strict': True, 'requireParameters': True}
        expected = ai.cache_key('taxonomy', {}, schema, ai.DEFAULT_MODEL, config, system_prompt=PROMPT)
        self.assertEqual(receipt['entries'][0]['cacheKey'], expected)
        self.assertEqual(calls[0]['reasoning'], {'enabled': False})

    def test_vision_transport_timeout_and_rejected_response_usage_are_preserved(self):
        schema = _object({'answer': _text(100, 1)})
        payload = {'model': ai.DEFAULT_MODEL, 'reasoning': {'enabled': True}}
        envelope = {'model': ai.DEFAULT_MODEL, 'choices': [{'finish_reason': 'length',
            'message': {'content': ''}}], 'usage': {'cost': .02, 'completion_tokens': 8192}}
        timeouts = []
        @contextmanager
        def response(request, *, timeout):
            timeouts.append(timeout)
            class Body:
                def read(self, _):
                    return json.dumps(envelope).encode()
            yield Body()
        with patch.object(ai, 'urlopen', response):
            with self.assertRaises(ai.APIError) as result:
                ai.completion(payload, 'test-key', schema)
            self.assertEqual(result.exception.finish_reason, 'length')
            self.assertEqual(result.exception.usage['cost'], .02)
            payload['reasoning']['enabled'] = False
            envelope['choices'][0].update(finish_reason='stop', message={'content': '{"answer":"valid"}'})
            self.assertEqual(ai.completion(payload, 'test-key', schema)[0], {'answer': 'valid'})
        self.assertEqual(timeouts, [300, 120])

    def test_success_after_retries_retains_unreported_attempts_and_known_failure_usage(self):
        schema = _object({'answer': _text(100, 1)})
        job = {'id': 'object', 'input': {}, 'schema': schema, 'systemPrompt': PROMPT}
        async def no_wait(*_):
            return None
        for report_first in (False, True):
            with self.subTest(report_first=report_first):
                calls = []
                def transport(*_):
                    calls.append(1)
                    if len(calls) < 3:
                        error = ai.APIError('Transient provider error', retryable=True)
                        if report_first and len(calls) == 1:
                            error.usage = {'cost': .002, 'completion_tokens': 50}
                        raise error
                    return {'answer': 'valid'}, {'cost': .003, 'total_tokens': 100}
                work = self.root / str(report_first)
                with patch.object(ai.asyncio, 'sleep', no_wait):
                    result = asyncio.run(ai.batch('mapping', [job], work, api_key='test', transport=transport))
                self.assertEqual(result['apiCalls'], 3)
                accounting = diagrams.lifetime_accounting(work)
                self.assertEqual(accounting['knownApiCalls'], 3)
                self.assertEqual(accounting['failedAttemptsWithUnreportedUsage'], 1 if report_first else 2)
                self.assertEqual(accounting['failedAttemptsWithReportedUsage'], int(report_first))
                self.assertAlmostEqual(accounting['acceptedUsage']['cost'], .003)
                self.assertAlmostEqual(accounting['reportedUsage']['cost'], .005 if report_first else .003)

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
