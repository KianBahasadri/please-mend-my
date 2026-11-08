#!/usr/bin/env python3
"""Cached, bounded Commons/Openclipart discovery for arbitrary catalog nouns.

This is a build-only helper. Callers may discover many subjects concurrently;
provider semaphores separately cap upstream requests. Successful responses and
content-addressed original SVGs are reusable offline. Transient failures never
become successful empty discovery records.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from find_diagram_sources import inspect_svg, license_from_metadata, plain

VERSION = "diagram-discovery-v1"
ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "PleaseMendMyDiagrams/1.0 (https://github.com/KianBahasadri/please-mend-my)"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
MAXIMUM = 500_000
SEMAPHORES = {"commons": threading.BoundedSemaphore(4),
              "openclipart": threading.BoundedSemaphore(4),
              "download": threading.BoundedSemaphore(8)}
_CACHE_LOCKS = defaultdict(threading.Lock)
_CACHE_LOCK = threading.Lock()


class DiscoveryError(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def atomic(path, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".discovery-", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(body)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def cache_lock(path):
    with _CACHE_LOCK:
        return _CACHE_LOCKS[str(path)]


def request_bytes(url, *, provider="download", maximum=MAXIMUM, attempts=3):
    parsed = urlsplit(url)
    allowed = {"commons.wikimedia.org", "upload.wikimedia.org", "openclipart.org"}
    if parsed.scheme != "https" or parsed.hostname not in allowed or parsed.username or parsed.password:
        raise ValueError("Unexpected upstream source host")
    for attempt in range(attempts):
        delay = 0
        try:
            with SEMAPHORES[provider]:
                with urlopen(Request(url, headers={"User-Agent": USER_AGENT,
                        "Accept": "application/json,image/svg+xml,text/html;q=0.8,*/*;q=0.1"}), timeout=25) as response:
                    if urlsplit(response.geturl()).hostname not in allowed:
                        raise DiscoveryError("Upstream redirected to an unsupported host")
                    body = response.read(maximum + 1)
                    if len(body) > maximum:
                        raise ValueError("Source exceeds configured byte limit")
                    return body
        except HTTPError as error:
            code = error.code
            try:
                delay = min(30, max(0, float(error.headers.get("Retry-After", "0"))))
            except (TypeError, ValueError):
                pass
            error.close()
            if code not in {429, 500, 502, 503, 504} or attempt + 1 == attempts:
                raise DiscoveryError("Upstream HTTP " + str(code)) from None
        except (OSError, URLError):
            if attempt + 1 == attempts:
                raise DiscoveryError("Upstream connection failed") from None
        time.sleep(max(delay, 2 ** attempt))
    raise DiscoveryError("Upstream retry budget exhausted")


def commons_query(params, work, *, offline=False):
    key = hashlib.sha256(canonical(params)).hexdigest()
    path = Path(work) / "discovery-cache/commons" / (key + ".json")
    with cache_lock(path):
        if path.is_file():
            data = json.loads(path.read_bytes())
            if not data.get("error"):
                return data
        if offline:
            raise DiscoveryError("Commons response is not cached offline")
        for attempt in range(3):
            data = json.loads(request_bytes(COMMONS_API + "?" + urlencode(params),
                                           provider="commons", maximum=4_000_000))
            error = data.get("error")
            if not error:
                atomic(path, canonical(data))
                return data
            code = error.get("code", "unknown") if isinstance(error, dict) else "unknown"
            if code not in {"maxlag", "ratelimited"} or attempt == 2:
                raise DiscoveryError("Commons API error: " + str(code))
            time.sleep(3 * (attempt + 1))
    raise DiscoveryError("Commons API retry budget exhausted")


def commons_search(query, work, *, limit=8, offline=False):
    return commons_query({"action": "query", "format": "json", "formatversion": 2,
        "generator": "search", "gsrsearch": "filetype:svg " + query,
        "gsrnamespace": 6, "gsrlimit": limit, "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata|timestamp|sha1", "maxlag": 5}, work, offline=offline)


def cached_html(url, work, *, offline=False):
    path = Path(work) / "discovery-cache/openclipart" / (hashlib.sha256(url.encode()).hexdigest() + ".html")
    with cache_lock(path):
        if path.is_file():
            return path.read_text()
        if offline:
            raise DiscoveryError("Openclipart response is not cached offline")
        body = request_bytes(url, provider="openclipart", maximum=2_000_000)
        text = body.decode("utf-8", errors="replace")
        if re.search(r"just a moment|checking your browser|cf-challenge|captcha", text, re.I):
            raise DiscoveryError("Openclipart returned a challenge page")
        atomic(path, body)
        return text


def openclipart_search(query, work, *, limit=8, offline=False):
    page = cached_html("https://openclipart.org/search/?" + urlencode({"query": query}), work, offline=offline)
    links = re.findall(r'href=["\'](/detail/\d+(?:/[^"\'#?]+)?)["\']', page)
    return ["https://openclipart.org" + item for item in dict.fromkeys(links)][:limit]


def source_body(download, work, *, offline=False):
    key = hashlib.sha256(download.encode()).hexdigest()
    path = Path(work) / "discovery-cache/downloads" / (key + ".svg")
    with cache_lock(path):
        if path.is_file():
            body = path.read_bytes()
        elif offline:
            raise DiscoveryError("Original SVG is not cached offline")
        else:
            body = request_bytes(download)
            # Never cache a success HTML/error response under an SVG filename.
            inspect_svg(body)
            atomic(path, body)
        if len(body) > MAXIMUM:
            raise ValueError("Source exceeds configured byte limit")
        return body


def pin(record, body, subject, work):
    structure = inspect_svg(body)
    if structure["shapes"] > 500:
        raise ValueError("Source exceeds 500-shape geometry budget")
    if len(body) > MAXIMUM:
        raise ValueError("Source exceeds configured byte limit")
    digest = hashlib.sha256(body).hexdigest()
    target = Path(work).resolve() / "sources" / (digest + ".original.svg")
    if target.is_file():
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("Pinned original changed SHA-256")
    else:
        atomic(target, body)
    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", str(subject["id"]))[:80]
    result = dict(record, id=slug + "-" + digest[:12], subject=subject["id"],
                  file=str(target), sha256=digest, bytes=len(body), structure=structure,
                  shapeCount=structure["shapes"], sourceUrl=record["source"],
                  downloadUrl=record.get("download", ""),
                  adaptations="None in pinned original; runtime adaptations recorded by compiler.")
    atomic(Path(work) / "source-metadata" / (digest + ".json"), canonical(result))
    return result


def commons_candidate(page, subject, work, *, offline=False):
    info = page.get("imageinfo", [{}])[0]
    if info.get("mime") != "image/svg+xml":
        raise ValueError("Candidate is not SVG MIME")
    if type(info.get("size")) is not int or not 0 < info["size"] <= MAXIMUM:
        raise ValueError("Candidate exceeds source size budget")
    metadata = info.get("extmetadata", {})
    license_name, license_url = license_from_metadata(metadata)
    author = plain(metadata.get("Artist", {}).get("value", ""))
    if not author:
        raise ValueError("Source author is unspecified")
    parsed = urlsplit(info["url"])
    if parsed.scheme != "https" or parsed.hostname != "upload.wikimedia.org":
        raise ValueError("Unexpected Commons source host")
    download = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    body = source_body(download, work, offline=offline)
    if len(body) != info["size"]:
        raise ValueError("Source size changed between metadata and download")
    if info.get("sha1") and hashlib.sha1(body).hexdigest() != info["sha1"]:
        raise ValueError("Source changed between metadata and download")
    source = info.get("descriptionurl") or "https://commons.wikimedia.org/wiki/" + page["title"].replace(" ", "_")
    if urlsplit(source).scheme != "https" or urlsplit(source).hostname != "commons.wikimedia.org":
        raise ValueError("Unexpected Commons description host")
    record = {"title": page["title"].removeprefix("File:"), "author": author,
        "source": source, "download": download, "license": license_name,
        "licenseUrl": license_url, "revision": info.get("timestamp", ""),
        "upstreamSha1": info.get("sha1", ""), "extmetadata": metadata,
        "description": plain(metadata.get("ImageDescription", {}).get("value", ""))[:16000],
        "discovery": "MediaWiki generator=search + imageinfo/extmetadata"}
    return pin(record, body, subject, work)


def openclipart_candidate(source, subject, work, *, offline=False):
    parsed = urlsplit(source)
    match = re.fullmatch(r"/detail/(\d+)(?:/[^/?#]+)?", parsed.path)
    if parsed.scheme != "https" or parsed.hostname != "openclipart.org" or not match:
        raise ValueError("Unexpected Openclipart source URL")
    page = cached_html(source, work, offline=offline)
    records = []
    for block in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            value = json.loads(block)
            records.extend(value if isinstance(value, list) else [value])
        except ValueError:
            continue
    images = [value for value in records if isinstance(value, dict) and value.get("@type") in {"ImageObject", "VisualArtwork"}]
    if not any(value.get("license") == "https://creativecommons.org/publicdomain/zero/1.0/" for value in images):
        raise ValueError("Openclipart page lacks explicit CC0 license metadata")
    author = re.search(r'<a[^>]+href=["\']/artist/[^"\']+["\'][^>]*>(.*?)</a>', page, re.S)
    title = re.search(r"<h2[^>]*>(.*?)</h2>", page, re.S)
    if not author or not title:
        raise ValueError("Openclipart author/title is unspecified")
    download = "https://openclipart.org/download/" + match.group(1)
    body = source_body(download, work, offline=offline)
    description = plain(" ".join(str(value.get("description", "")) for value in images))[:16000]
    return pin({"title": plain(title.group(1)), "author": plain(author.group(1)),
        "source": source, "download": download, "license": "CC0 1.0",
        "licenseUrl": "https://creativecommons.org/publicdomain/zero/1.0/",
        "licenseConfirmation": "https://openclipart.org/share", "description": description,
        "discovery": "Openclipart detail page with explicit ImageObject CC0 license"}, body, subject, work)


def relevant(subject, title, description=""):
    """Generic subject evidence guard; visual mapping remains the final judge."""
    title = plain(title).casefold().replace("_", " ").replace("-", " ")
    if re.search(r"\b(?:logo|wordmark|coat of arms|flag|ribbon|insignia|statistical|statistics|exports|prices|flowchart|prohibition)\b", title):
        return False
    text = title + " " + plain(description).casefold().replace("_", " ").replace("-", " ")
    aliases = [subject.get("label", ""), *subject.get("aliases", [])]
    for alias in aliases:
        words = re.findall(r"[^\W_]+", str(alias).casefold())
        if words and all(re.search(r"\b" + re.escape(word) + r"(?:s|es)?\b", text) for word in words):
            return True
    return False


def local_candidates(subject, work):
    """Reuse previously pinned originals without giving them new approval."""
    path = ROOT / "docs/references/specifiers/sources.json"
    if not path.is_file():
        return []
    aliases = {re.sub(r"[-_]", " ", str(value).casefold()) for value in
               [subject.get("label", ""), *subject.get("aliases", [])]}
    result = []
    for item in json.loads(path.read_bytes()).get("archetypes", []):
        if item["id"].replace("-", " ") not in aliases:
            continue
        for entry in item.get("candidates", []):
            candidate_path = path.parent / entry["file"]
            try:
                body = candidate_path.read_bytes()
                if hashlib.sha256(body).hexdigest() != entry["sha256"]:
                    raise ValueError("Previously pinned source SHA changed")
                result.append(pin(entry, body, subject, work))
            except (OSError, ValueError, ET.ParseError):
                continue
    return result


def discover_candidates(subject, work, *, limit=3, queries=None, offline=False):
    """Return candidates plus separate rejections and transient provider errors.

    ``file`` is an absolute pinned original path. The standard source/author/
    license fields can pass directly to ``diagram_svg.compile_art``. Override
    ``svgQueries`` and ``viewId`` when discovering another spatial view.
    """
    if type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError("Candidate limit must be 1..5")
    terms = queries if queries is not None else subject.get("svgQueries", [])
    terms = [str(term).strip() for term in terms if isinstance(term, str) and 2 <= len(term.strip()) <= 180][:5]
    label = str(subject.get("label", "")).strip()
    if not terms and len(label) >= 2:
        terms = [label + " diagram", label]
    key = hashlib.sha256(canonical({"version": VERSION, "id": subject["id"],
        "viewId": subject.get("viewId", ""), "label": label,
        "aliases": subject.get("aliases", []), "queries": terms, "limit": limit})).hexdigest()
    destination = Path(work) / "discovery" / (key + ".json")
    with cache_lock(destination):
        if destination.is_file():
            previous = json.loads(destination.read_bytes())
            valid = all(Path(row["file"]).is_file() and
                hashlib.sha256(Path(row["file"]).read_bytes()).hexdigest() == row["sha256"]
                for row in previous.get("candidates", []))
            if valid and not previous.get("errors"):
                return previous
        report = {"version": 1, "id": subject["id"], "viewId": subject.get("viewId", ""),
                  "cacheKey": key, "candidates": [], "rejected": [], "errors": [], "queries": []}
        seen = set()
        if not subject.get("viewId") or subject.get("viewId") in {"exterior", "default", "primary"}:
            for row in local_candidates(subject, work):
                if row["sha256"] not in seen:
                    report["candidates"].append(row); seen.add(row["sha256"])
                if len(report["candidates"]) >= limit:
                    break
        for term in terms:
            if len(report["candidates"]) >= limit:
                break
            try:
                pages = commons_search(term, work, offline=offline).get("query", {}).get("pages", [])
                report["queries"].append({"provider": "commons", "query": term, "results": len(pages)})
                for page in pages:
                    if len(report["candidates"]) >= limit:
                        break
                    title = page.get("title", "")
                    metadata = page.get("imageinfo", [{}])[0].get("extmetadata", {})
                    description = metadata.get("ImageDescription", {}).get("value", "")
                    if not relevant(subject, title, description):
                        report["rejected"].append({"title": title, "reason": "No subject evidence or decorative search noise"})
                        continue
                    try:
                        row = commons_candidate(page, subject, work, offline=offline)
                        if row["sha256"] not in seen:
                            row["query"] = term
                            report["candidates"].append(row); seen.add(row["sha256"])
                    except DiscoveryError as error:
                        report["errors"].append({"provider": "commons", "title": title, "reason": str(error)})
                    except (ValueError, KeyError, ET.ParseError) as error:
                        report["rejected"].append({"title": title, "reason": str(error)})
            except (DiscoveryError, ValueError, KeyError) as error:
                report["errors"].append({"provider": "commons", "query": term, "reason": str(error)})
        if len(report["candidates"]) < limit:
            # One broad object query avoids multiple low-value text searches.
            try:
                links = openclipart_search(label, work, offline=offline)
                report["queries"].append({"provider": "openclipart", "query": label, "results": len(links)})
                for source in links:
                    if len(report["candidates"]) >= limit:
                        break
                    try:
                        row = openclipart_candidate(source, subject, work, offline=offline)
                        if not relevant(subject, row["title"], row.get("description", "")):
                            raise ValueError("No subject evidence or decorative search noise")
                        if row["sha256"] not in seen:
                            report["candidates"].append(row); seen.add(row["sha256"])
                    except DiscoveryError as error:
                        report["errors"].append({"provider": "openclipart", "source": source, "reason": str(error)})
                    except (ValueError, KeyError, ET.ParseError) as error:
                        report["rejected"].append({"source": source, "reason": str(error)})
            except (DiscoveryError, ValueError) as error:
                report["errors"].append({"provider": "openclipart", "query": label, "reason": str(error)})
        atomic(destination, canonical(report))
        return report
