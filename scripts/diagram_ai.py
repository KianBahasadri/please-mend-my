"""Strict-schema remote diagram requests and immutable, resumable caches.

This module has no subject registry, drawing compiler, or publication path.
Callers supply their complete prompt and schema. The cache protocol identifier
is intentionally stable so existing paid responses continue replaying offline.
"""
from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from diagram_svg import VERSION as COMPILER_VERSION, canonical, digest_bytes
from restructure_catalog import (APIError, DEFAULT_MODEL, load_api_key,
                                 numerical_usage, retry_delay, validate_api_key)
from structured_guides import _validate

# Immutable cache-format identity, retained independently of the retired runner.
PROMPT_VERSION = "diagram-pilot-v1"

def read_json(path):
    return json.loads(Path(path).read_bytes())

def atomic(path, body):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
        Path(temporary).chmod(0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)

def save(path, value):
    atomic(path, canonical(value) + b"\n")

@contextmanager
def lock(path):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another diagram batch is running") from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)

def validate_schema(value, schema):
    _validate(value, schema, "response")

def cache_key(stage, prepared, schema, model, config, *, system_prompt):
    return digest_bytes(canonical({"stage": stage, "input": prepared, "schema": schema,
        "model": model, "promptVersion": PROMPT_VERSION,
        "systemPromptSha256": digest_bytes(system_prompt.encode()),
        "compilerVersion": COMPILER_VERSION, "configuration": config}))

def completion(payload, key, schema, timeout=120):
    """Use the existing credential/error conventions with a separate schema."""
    try:
        key = validate_api_key(key)
        if not key:
            raise ValueError("Missing credential")
        request = Request("https://openrouter.ai/api/v1/chat/completions", data=canonical(payload), method="POST", headers={
            "Authorization": "Bearer " + key, "Content-Type": "application/json",
            "HTTP-Referer": "https://pleasemendmy.com", "X-OpenRouter-Title": "Please mend my offline diagrams"})
        with urlopen(request, timeout=timeout) as response:
            body = response.read(4_000_001)
    except HTTPError as error:
        code, delay = error.code, retry_delay((error.headers or {}).get("Retry-After"))
        error.close()
        raise APIError("OpenRouter HTTP " + str(code), retryable=code in {429, 500, 502, 503, 504}, retry_after=delay) from None
    except (ValueError, UnicodeError):
        raise APIError("OpenRouter request has invalid credentials or headers") from None
    except (TimeoutError, URLError, OSError) as error:
        raise APIError("OpenRouter connection failed (" + type(error).__name__ + ")", retryable=True) from None
    if len(body) > 4_000_000:
        raise APIError("OpenRouter response exceeds size bound")
    try:
        envelope = json.loads(body)
        choices = envelope.get("choices")
        if envelope.get("error") or envelope.get("model") != payload["model"] or not isinstance(choices, list) or len(choices) != 1:
            raise ValueError()
        choice = choices[0]; message = choice["message"]
        if choice.get("finish_reason") != "stop" or message.get("refusal"):
            raise ValueError()
        result = json.loads(message["content"])
        validate_schema(result, schema)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise APIError("OpenRouter returned incomplete, refused, or schema-invalid output") from None
    return result, numerical_usage(envelope.get("usage"))

async def batch(stage, jobs, work, *, model=DEFAULT_MODEL, api_key=None, concurrency=40, transport=completion):
    if type(concurrency) is not int or not 1 <= concurrency <= 128:
        raise ValueError("Concurrency must be 1..128")
    config = {"maxTokens": 8192 if stage == "mapping" else 4096, "temperature": 0,
              "reasoningEnabled": False, "strict": True, "requireParameters": True}
    pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="diagram-ai")
    semaphore = asyncio.Semaphore(concurrency)
    started = time.perf_counter()
    active = peak = calls = 0

    async def run(job):
        nonlocal active, peak, calls
        prepared, schema = job["input"], job["schema"]
        system_prompt = job["systemPrompt"]
        key = cache_key(stage, prepared, schema, model, config, system_prompt=system_prompt)
        path = Path(work) / "cache" / stage / (key + ".json")
        record = None
        try:
            cached = read_json(path)
            if cached.get("cacheKey") == key and cached.get("model") == model:
                validate_schema(cached["content"], schema)
                record = cached
        except (OSError, ValueError, TypeError, KeyError):
            pass
        if record is not None:
            return {"id": job["id"], "cacheKey": key, "apiCalls": 0, "cached": True, "content": record["content"], "usage": record.get("usage", {})}
        if not api_key:
            return {"id": job["id"], "cacheKey": key, "apiCalls": 0, "cached": False,
                    "error": "No matching validated response cached (offline or missing API key)"}
        user_content = [{"type": "text", "text": canonical(prepared).decode()}]
        for image in job.get("images", []):
            body = Path(image["path"]).read_bytes()
            if digest_bytes(body) != image["sha256"] or len(body) > 1_000_000:
                raise ValueError("Mapping preview differs from its SHA pin or size bound")
            user_content.extend([{"type": "text", "text": "Candidate image: " + image["id"]},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(body).decode(), "detail": "low"}}])
        payload = {"model": model, "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content if job.get("images") else canonical(prepared).decode()}],
            "temperature": config["temperature"], "max_tokens": config["maxTokens"],
            "reasoning": {"enabled": False}, "provider": {"require_parameters": True},
            "response_format": {"type": "json_schema", "json_schema": {"name": "diagram_" + stage, "strict": True, "schema": schema}}}
        attempted = 0
        async with semaphore:
            for attempt in range(3):
                try:
                    attempted += 1; calls += 1; active += 1; peak = max(peak, active)
                    try:
                        content, usage = await asyncio.get_running_loop().run_in_executor(pool, transport, payload, api_key, schema)
                    finally:
                        active -= 1
                    try:
                        validate_schema(content, schema)
                    except (ValueError, TypeError):
                        raise APIError("Completion failed independent local schema validation") from None
                    record = {"version": 1, "cacheKey": key, "model": model, "configuration": config,
                              "inputSha256": digest_bytes(canonical(prepared)), "content": content, "usage": usage}
                    save(path, record)
                    return {"id": job["id"], "cacheKey": key, "apiCalls": attempted, "cached": False, "content": content, "usage": usage}
                except APIError as error:
                    if not error.retryable or attempt == 2:
                        return {"id": job["id"], "cacheKey": key, "apiCalls": attempted, "cached": False, "error": str(error)}
                    await asyncio.sleep(max(error.retry_after, 2 ** attempt))
    try:
        results = await asyncio.gather(*(run(job) for job in jobs))
    finally:
        pool.shutdown(wait=True)
    usage = {name: sum(result.get("usage", {}).get(name, 0) for result in results if result["apiCalls"])
             for name in ("prompt_tokens", "completion_tokens", "total_tokens", "cost")}
    receipt = {"version": 1, "stage": stage, "model": model, "concurrency": concurrency,
        "peakRequests": peak, "durationSeconds": round(time.perf_counter() - started, 4), "apiCalls": calls,
        "cacheHits": sum(result["cached"] for result in results), "usage": usage, "entries": results}
    receipt_path = Path(work) / (stage + "-receipt.json")
    if receipt_path.exists():
        previous = read_json(receipt_path)
        save(Path(work) / "receipts" / (stage + "-" + digest_bytes(canonical(previous)) + ".json"), previous)
    save(Path(work) / "receipts" / (stage + "-" + digest_bytes(canonical(receipt)) + ".json"), receipt)
    save(receipt_path, receipt)
    return receipt
