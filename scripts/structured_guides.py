"""Shared, offline-only contract for extracted guide content and v2 cache files."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlsplit

SCHEMA_VERSION = 2
PROMPT_VERSION = "stage3-v1"
GENERATED_FIELDS = ("summary", "tools", "materials", "safety", "symptoms", "steps", "references")


def _text(limit=6000, minimum=0):
    return {"type": "string", "minLength": minimum, "maxLength": limit}


def _array(items, maximum=100):
    return {"type": "array", "items": items, "maxItems": maximum}


def _object(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


CONTENT_SCHEMA = _object({
    "summary": _text(6000),
    "tools": _array(_text(500, 1)),
    "materials": _array(_text(500, 1)),
    "safety": _array(_object({"level": {"type": "string", "enum": ["note", "warning", "danger"]},
                              "text": _text(6000, 1)})),
    "symptoms": _array(_text(500, 1)),
    "steps": _array(_object({"title": _text(500, 1), "body": _text(12000, 1),
                             "warnings": _array(_text(6000, 1)),
                             "images": _array(_text(1000, 1), 20), "code": _text(24000)}), 150),
    "references": _array(_object({"title": _text(500, 1), "url": _text(4000, 1)}), 30),
})


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def raw_digest(raw):
    return hashlib.sha256(canonical_bytes(raw)).hexdigest()


def cache_key(raw, model, prompt_version=PROMPT_VERSION, *, config=None):
    return hashlib.sha256(canonical_bytes({"raw": raw, "model": model,
        "promptVersion": prompt_version, "schema": CONTENT_SCHEMA,
        "schemaVersion": SCHEMA_VERSION, "configuration": config or {}})).hexdigest()


def _validate(value, schema, path):
    kind = schema["type"]
    expected = {"object": dict, "array": list, "string": str,
                "integer": int, "boolean": bool}[kind]
    if type(value) is not expected:
        raise ValueError(path + " must be " + kind)
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(path + " has an unsupported value")
    if kind == "object":
        if set(value) != set(schema["properties"]):
            raise ValueError(path + " has missing or unexpected fields")
        for name, subschema in schema["properties"].items():
            _validate(value[name], subschema, path + "." + name)
    elif kind == "array":
        if len(value) > schema["maxItems"]:
            raise ValueError(path + " has too many items")
        for number, item in enumerate(value):
            _validate(item, schema["items"], path + "[" + str(number) + "]")
    elif kind == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", len(value)):
            raise ValueError(path + " has an invalid length")
        if schema.get("minLength", 0) and not value.strip():
            raise ValueError(path + " cannot be blank")
    elif kind == "integer":
        if ("minimum" in schema and value < schema["minimum"] or
                "maximum" in schema and value > schema["maximum"]):
            raise ValueError(path + " is outside the allowed range")


def validate_content(content):
    """Validate exactly the schema sent to the model, without third-party packages."""
    _validate(content, CONTENT_SCHEMA, "content")


def safe_reference_url(value):
    if not isinstance(value, str) or any(ord(c) < 33 for c in value):
        return False
    try:
        url = urlsplit(value)
        return (url.scheme in {"http", "https"} and bool(url.hostname)
                and url.username is None and url.password is None)
    except ValueError:
        return False


def safe_media_url(value):
    return isinstance(value, str) and bool(re.fullmatch(
        r"/media/[0-9a-f]{64}(?:\.[A-Za-z0-9]{1,12})?", value))


class _SourceAssets(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.references, self.images = set(), set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and safe_reference_url(attrs.get("href")):
            self.references.add(attrs["href"])
        elif tag == "img" and safe_media_url(attrs.get("src")):
            self.images.add(attrs["src"])


def source_assets(raw):
    """Only source-provided links/images may be reused by generated content."""
    parser = _SourceAssets()
    parser.feed(raw.get("html", ""))
    parser.close()
    parser.references.update(value for value in [raw.get("sourceUrl")] if safe_reference_url(value))
    parser.references.update(item["url"] for item in raw.get("references", [])
                             if isinstance(item, dict) and safe_reference_url(item.get("url")))
    parser.images.update(value for value in raw.get("images", []) if safe_media_url(value))
    return {"references": sorted(parser.references), "images": sorted(parser.images)}


def _validate_assets(detail, raw=None):
    allowed = source_assets(raw) if raw is not None else None
    for reference in detail["references"]:
        url = reference["url"]
        if not safe_reference_url(url) or (allowed is not None and url not in allowed["references"]):
            raise ValueError("Generated reference must be a source-provided HTTP(S) link")
    for step in detail["steps"]:
        for image in step["images"]:
            if not safe_media_url(image) or (allowed is not None and image not in allowed["images"]):
                raise ValueError("Generated image must be a source-provided local media URL")


def make_v2(raw, content, model, *, key=None, prompt_version=PROMPT_VERSION, config=None):
    validate_content(content)
    expected = cache_key(raw, model, prompt_version, config=config)
    if key is not None and key != expected:
        raise ValueError("Processing cache key does not match its inputs")
    result = deepcopy(raw)
    result.pop("html", None)
    result.update(deepcopy(content))
    result.update(version=SCHEMA_VERSION, processing={"model": model, "cacheKey": expected,
        "rawSha256": raw_digest(raw), "promptVersion": prompt_version,
        "schemaVersion": SCHEMA_VERSION, "configuration": deepcopy(config or {})})
    validate_v2(result, raw)
    return result


def validate_v2(detail, raw=None):
    """Validate cache shape, provenance, assets and authoritative source metadata.

    Passing raw is mandatory at the publication seam: it also detects stale
    input, changed configuration, and edits to source attribution or identity.
    Schema validation cannot establish the factual accuracy of generated prose.
    """
    if not isinstance(detail, dict) or type(detail.get("version")) is not int or detail["version"] != SCHEMA_VERSION:
        raise ValueError("Guide must have version 2")
    if not isinstance(detail.get("id"), str) or not re.fullmatch("[0-9a-f]{20}", detail["id"]):
        raise ValueError("Guide ID must be 20 lowercase hex characters")
    for name in ("title", "category", "kind", "sourceUrl", "attribution"):
        if not isinstance(detail.get(name), str) or not detail[name].strip():
            raise ValueError("Missing source metadata: " + name)
    if not isinstance(detail.get("device"), str) or not isinstance(detail.get("license"), dict):
        raise ValueError("Guide must retain device and license metadata")
    if "html" in detail:
        raise ValueError("Structured guides cannot contain legacy HTML")
    validate_content({name: detail.get(name) for name in GENERATED_FIELDS})
    processing = detail.get("processing")
    fields = {"model", "cacheKey", "rawSha256", "promptVersion", "schemaVersion", "configuration"}
    if not isinstance(processing, dict) or set(processing) != fields:
        raise ValueError("Guide must include complete processing provenance")
    if (processing["schemaVersion"] != SCHEMA_VERSION or processing["promptVersion"] != PROMPT_VERSION
            or not isinstance(processing["model"], str) or not processing["model"].strip()
            or not isinstance(processing["configuration"], dict)):
        raise ValueError("Unsupported processing version or configuration")
    for name in ("cacheKey", "rawSha256"):
        if not isinstance(processing[name], str) or not re.fullmatch("[0-9a-f]{64}", processing[name]):
            raise ValueError("Invalid processing digest: " + name)
    canonical_bytes(processing["configuration"])
    _validate_assets(detail, raw)
    if raw is not None:
        if processing["rawSha256"] != raw_digest(raw):
            raise ValueError("Processed guide was built from different raw input")
        expected = cache_key(raw, processing["model"], processing["promptVersion"], config=processing["configuration"])
        if processing["cacheKey"] != expected:
            raise ValueError("Processing cache key does not match its inputs")
        fixed = set(raw) - set(GENERATED_FIELDS) - {"version", "html", "processing"}
        if any(name not in detail or detail[name] != raw[name] for name in fixed):
            raise ValueError("Processed guide altered authoritative source metadata")
        if set(detail) != fixed | set(GENERATED_FIELDS) | {"version", "processing"}:
            raise ValueError("Processed guide contains unexpected metadata")
