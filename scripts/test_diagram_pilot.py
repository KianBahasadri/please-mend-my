import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import diagram_pilot as pilot


class DiagramPilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_catalog_slices_use_domain_and_preserve_exact_models(self):
        runtime = self.root / "runtime"; runtime.mkdir()
        index = {"items": [["1", "Car T cell therapy", "health", "article", ""],
                           ["2", "Car battery repair", "vehicles", "guide", "Honda Civic 2018"],
                           ["3", "Car topic", "vehicles", "topic", "Car"]]}
        (runtime / "index.json").write_bytes(pilot.canonical(index))
        seed = {"id": "car", "label": "Car", "aliases": ["car"], "parts": [], "categories": ["vehicles"]}
        prepared = pilot.prepare_catalog(runtime, self.root / "work", [seed])
        entry = prepared["archetypes"][0]
        self.assertEqual(entry["catalogMatches"], 1)
        self.assertEqual(entry["examples"][0]["device"], "Honda Civic 2018")
        self.assertEqual(entry["examples"][0]["id"], "2")

    def test_proposed_aliases_stay_held_and_parts_need_real_query_evidence(self):
        seed = {"id": "phone", "label": "Phone", "aliases": ["phone"],
                "parts": [{"id": "screen", "label": "Screen", "query": "screen", "aliases": ["display"]}]}
        generated = {"aliases": ["phone", "handset"], "svgQueries": ["phone diagram"], "parts": [
            {"id": "screen", "label": "Wrong label", "query": "wrong", "aliases": ["cracked"]},
            {"id": "camera", "label": "Camera", "query": "camera", "aliases": []},
            {"id": "teardown", "label": "Teardown", "query": "teardown", "aliases": []},
            {"id": "imaginary", "label": "Imaginary", "query": "flux capacitor", "aliases": []}]}
        documents = [{"title": "Phone camera teardown", "device": "Phone 12"}]
        result, rejected = pilot.taxonomy_subject(seed, generated, documents)
        self.assertEqual(result["aliases"], ["phone"])
        self.assertEqual([part["id"] for part in result["parts"]], ["screen", "camera"])
        self.assertEqual(result["parts"][0]["query"], "screen")
        self.assertEqual(result["parts"][0]["label"], "Screen")
        self.assertIn("cracked", result["parts"][0]["aliases"])
        self.assertEqual(len(rejected), 3)

    def test_cache_key_covers_source_schema_prompt_and_config(self):
        schema = pilot._object({"answer": pilot._text(100, 1)})
        key = pilot.cache_key("mapping", {"sourceSha256": "a"}, schema, "model", {"maxTokens": 10})
        for change in ({"sourceSha256": "b"}, {"sourceSha256": "a", "mapping": "changed"}):
            self.assertNotEqual(key, pilot.cache_key("mapping", change, schema, "model", {"maxTokens": 10}))
        self.assertNotEqual(key, pilot.cache_key("mapping", {"sourceSha256": "a"}, schema, "other", {"maxTokens": 10}))
        self.assertNotEqual(key, pilot.cache_key("mapping", {"sourceSha256": "a"}, schema, "model", {"maxTokens": 11}))
        first = {"sourceSha256": "a", "visualEvidence": [{"id": "one", "sha256": "image-one"}]}
        second = {"sourceSha256": "a", "visualEvidence": [{"id": "one", "sha256": "image-two"}]}
        self.assertNotEqual(pilot.cache_key("mapping", first, schema, "model", {}),
                            pilot.cache_key("mapping", second, schema, "model", {}))

    def test_public_art_requires_exact_visual_approval_of_source_map_compiler_and_bytes(self):
        subject = {"id": "phone", "label": "Phone", "aliases": ["phone"],
                   "parts": [{"id": "screen", "label": "Screen", "query": "screen", "aliases": []}],
                   "catalogMatches": 1, "coverage": {"screen": 1}}
        source = {"id": "source", "sha256": "source-sha"}
        leaves = self.root / "leaves.json"; pilot.save(leaves, [])
        digests = {"phone": {"candidates": [{"source": source, "digest": {}, "leavesPath": str(leaves)}], "rejected": []}}
        mapping = {"selectedSource": "source", "groupMap": [], "reason": "Reviewed fixture"}
        receipt = {"entries": [{"id": "phone", "content": mapping}]}
        art = {"version": 1, "id": "phone", "viewBox": "0 0 100 100", "context": [],
               "parts": {"screen": [["rect", {"x": 1, "width": 10}]]}, "attribution": {"author": "Artist"}}
        approvals = self.root / "approvals.json"
        output = self.root / "assets/art"
        with patch.object(pilot, "compile_art", return_value=art), patch.object(pilot, "render_preview"):
            baseline = pilot.publish([subject], digests, receipt, self.root, output, approvals)
            entry = baseline["archetypes"][0]
            self.assertEqual(entry["status"], "needs-visual-review")
            self.assertIsNone(entry["artUrl"])
            self.assertEqual(list(output.glob("phone-*.json")), [])
            approved = {"approved": True, **{name: entry[name] for name in
                ("sourceSha256", "mappingSha256", "compilerVersion", "artSha256")}}
            for field in ("sourceSha256", "mappingSha256", "compilerVersion", "artSha256"):
                wrong = dict(approved); wrong[field] = "stale"
                pilot.save(approvals, {"version": 1, "approvals": {"phone": wrong}})
                result = pilot.publish([subject], digests, receipt, self.root, output, approvals)
                self.assertIsNone(result["archetypes"][0]["artUrl"], field)
            pilot.save(approvals, {"version": 1, "approvals": {"phone": approved}})
            selected = pilot.publish([subject], digests, receipt, self.root, output, approvals)["archetypes"][0]
            self.assertEqual(selected["status"], "approved")
            body = (output / Path(selected["artUrl"]).name).read_bytes()
            self.assertEqual(pilot.digest_bytes(body), approved["artSha256"])
            mutated = deepcopy(art); mutated["parts"]["screen"][0][1]["x"] = 2
            with patch.object(pilot, "compile_art", return_value=mutated):
                result = pilot.publish([subject], digests, receipt, self.root, output, approvals)
                self.assertIsNone(result["archetypes"][0]["artUrl"], "changed compiler output must be reviewed again")

    def test_reviewed_query_rejections_filter_public_options_without_mutating_taxonomy(self):
        subject = {"id": "stove", "label": "Stove", "aliases": ["stove"],
                   "parts": [{"id": "timer", "label": "Timer", "query": "timer", "aliases": []},
                             {"id": "burner", "label": "Burner", "query": "burner", "aliases": []}],
                   "catalogMatches": 2, "coverage": {"timer": 1, "burner": 1}}
        approvals = self.root / "approvals.json"
        pilot.save(approvals, {"version": 1, "approvals": {}, "rejectedQueries": {"stove": ["timer"]}})
        output = self.root / "assets/art"
        result = pilot.publish([subject], {"stove": {"candidates": [], "rejected": []}},
                               {"entries": []}, self.root, output, approvals)
        registry = (output.parent / "specifier-pilot.js").read_text()
        self.assertNotIn('"query":"timer"', registry)
        self.assertIn('"query":"burner"', registry)
        self.assertEqual([part["id"] for part in subject["parts"]], ["timer", "burner"])
        self.assertEqual(result["archetypes"][0]["rejectedQueries"][0]["query"], "timer")

    def test_batch_reuses_valid_cache_offline_with_no_transport_calls(self):
        schema = pilot._object({"answer": pilot._text(100, 1)})
        job = {"id": "phone", "input": {"source": "pinned"}, "schema": schema}
        calls = []
        def transport(payload, key, contract):
            calls.append(payload)
            return {"answer": "valid"}, {"total_tokens": 12, "cost": .001}
        first = asyncio.run(pilot.batch("mapping", [job], self.root, api_key="test", transport=transport))
        second = asyncio.run(pilot.batch("mapping", [job], self.root, api_key=None, transport=transport))
        self.assertEqual(first["apiCalls"], 1)
        self.assertEqual(second["apiCalls"], 0)
        self.assertEqual(second["cacheHits"], 1)
        self.assertEqual(second["entries"][0]["content"], {"answer": "valid"})
        self.assertEqual(len(calls), 1)

    def test_accounting_recovers_interrupted_successes_and_does_not_double_count_receipts(self):
        paid = {"version": 1, "stage": "mapping", "apiCalls": 2, "entries": [
            {"cacheKey": "one", "apiCalls": 1, "content": {}},
            {"cacheKey": "failed", "apiCalls": 1, "error": "Incomplete"}]}
        pilot.save(self.root / "mapping-receipt.json", paid)
        pilot.save(self.root / "receipts" / "same-receipt.json", paid)
        pilot.save(self.root / "cache/mapping/one.json", {"cacheKey": "one", "usage": {"cost": .01}})
        pilot.save(self.root / "cache/mapping/interrupted.json", {"cacheKey": "interrupted", "usage": {"cost": .02}})
        result = pilot.accounting(self.root)
        self.assertEqual(result["knownApiCalls"], 3)
        self.assertEqual(result["acceptedResponses"], 2)
        self.assertEqual(result["callsRecoveredFromInterruptedBatchCaches"], 1)
        self.assertEqual(result["failedAttemptsWithUnreportedUsage"], 1)
        self.assertAlmostEqual(result["acceptedResponseCost"], .03)

    def test_invalid_complete_response_is_never_cached(self):
        schema = pilot._object({"answer": pilot._text(100, 1)})
        job = {"id": "phone", "input": {}, "schema": schema}
        def transport(*args):
            return {"wrong": "field"}, {}
        receipt = asyncio.run(pilot.batch("mapping", [job], self.root, api_key="test", transport=transport))
        self.assertIn("error", receipt["entries"][0])
        self.assertEqual(list((self.root / "cache").rglob("*.json")) if (self.root / "cache").exists() else [], [])

    def test_transport_pool_really_supports_forty_requests(self):
        schema = pilot._object({"answer": pilot._text(100, 1)})
        jobs = [{"id": str(i), "input": {"i": i}, "schema": schema} for i in range(42)]
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
        receipt = asyncio.run(pilot.batch("mapping", jobs, self.root, api_key="test", transport=transport, concurrency=40))
        self.assertEqual(receipt["apiCalls"], 42)
        self.assertEqual(peak, 40)


if __name__ == "__main__":
    unittest.main()
