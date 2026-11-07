#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["libzim==4.1.0", "lxml==6.1.3"]
# ///
"""Download and normalize the pinned full English WikiMed archive.

Run the same command locally or on EC2. All generated data remains in --data-dir
(Git-ignored by default); existing served catalogs are never touched. A completed
source is reused by script/archive fingerprint without scanning its corpus.
Text is CC BY-SA 4.0; images retain their individual source licenses.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import nullcontext
import fcntl
from functools import lru_cache
import gzip
import hashlib
import json
import mimetypes
import multiprocessing
from pathlib import Path
import posixpath
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata
from urllib.parse import quote, unquote, urljoin, urlsplit
from urllib.request import urlopen
import xml.etree.ElementTree as ET

from libzim.reader import Archive
from lxml import etree, html

ROOT = Path(__file__).resolve().parent.parent
REVISION = "wikipedia_en_medicine_maxi_2026-10"
ARCHIVE_BYTES = 1_864_210_320
ARCHIVE_SHA256 = "9a1570f2f346aa18694ee520e0483f19b7447f69e0970e2f727c53ccf0dcc7d6"
ARCHIVE_NAME = REVISION + ".zim"
BASE = "https://download.kiwix.org/zim/wikipedia/"
CATALOG_URL = "https://library.kiwix.org/catalog/v2/entries?lang=eng&q=wikimed&count=10"
LICENSE = {"name": "CC BY-SA 4.0", "url": "https://creativecommons.org/licenses/by-sa/4.0/"}
SOURCE = {"id": "wikimed", "name": "WikiMed Medical Encyclopedia via Kiwix", "url": CATALOG_URL,
          "archive": ARCHIVE_NAME, "license": LICENSE,
          "attribution": "Wikipedia contributors via WikiMed / Kiwix."}
CATEGORIES = [{"id": "health", "name": "Health & medicine"}]
MEDIA_NOTICE = "Images retain individual source licenses, not the text license. Archive image paths and encountered file-description URLs are retained in catalog.sqlite and article mediaSources; the original ZIM is preserved."
ADMIN_NAMESPACES = {"category", "file", "image", "template", "special", "portal", "help", "mediawiki", "user", "talk", "wikipedia", "module", "draft", "timedtext", "book", "media", "gadget", "gadget definition"}
ALLOWED_TAGS = {"a", "abbr", "b", "blockquote", "br", "caption", "code", "dd", "del", "details", "div", "dl", "dt", "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "kbd", "li", "ol", "p", "pre", "s", "section", "small", "span", "strong", "sub", "summary", "sup", "table", "tbody", "td", "th", "thead", "tr", "u", "ul", "var"}
DROP_TAGS = {"script", "style", "iframe", "object", "embed", "svg", "form", "input", "button", "textarea", "select", "option", "nav", "noscript", "template", "link", "meta", "source", "audio", "video", "canvas"}
NAV_CLASSES = {"mw-editsection", "mw-jump-link", "mw-indicators", "vector-header", "vector-page-toolbar", "vector-column-start", "vector-column-end", "catlinks", "printfooter", "toc", "mw-navigation", "mw-footer"}
IMAGE_EXT = {"image/webp": ".webp", "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/svg+xml": ".svg", "image/avif": ".avif", "image/bmp": ".bmp", "image/vnd.microsoft.icon": ".ico", "image/x-icon": ".ico"}


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def atomic_write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(body)
    temporary.replace(path)


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ingestion_key():
    return hashlib.sha256(Path(__file__).read_bytes() + b"\0" + ARCHIVE_SHA256.encode()).hexdigest()


def clean(value):
    return "".join(char for char in unicodedata.normalize("NFKD", value)
                   if not unicodedata.category(char).startswith("M")).lower()


def normalized_path(value):
    """Decode a URL path once; never use this for identities returned by libzim."""
    value = value.split("#", 1)[0].split("?", 1)[0]
    return posixpath.normpath(unquote(value)).lstrip("/")


def article_name(path, legacy=False):
    if legacy and path.startswith("A/"):
        path = path[2:]
    elif path.startswith(("-/", "_/")) or legacy and path.startswith(("I/", "M/", "X/")):
        return None
    if not path or path in {".", "index", "index.html", "mainPage", "Main_Page", "home/external_content", "home/unavailable_offline"}:
        return None
    namespace = path.split(":", 1)[0].replace("_", " ").casefold()
    if ":" in path and (namespace in ADMIN_NAMESPACES or namespace.endswith(" talk")):
        return None
    return path


def short_id(path):
    return hashlib.sha256(("wikimed:" + path).encode()).hexdigest()[:20]


def source_url(path, legacy=False):
    name = path
    if legacy and name.startswith("A/"):
        name = name[2:]
    return "https://en.wikipedia.org/wiki/" + quote(name.replace(" ", "_"), safe="/():,~!$&'*=;@+-_")


@lru_cache(maxsize=64)
def base_url(base):
    return "https://offline.invalid/" + quote(base, safe="/")


def resolve_reference(base, reference, legacy=False):
    """Return possible archive keys for a local/Wikipedia link, never other hosts."""
    if not reference or reference.startswith("#"):
        return []
    try:
        parsed = urlsplit(urljoin(base_url(base), reference))
    except ValueError:
        return []
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"offline.invalid", "en.wikipedia.org", "en.m.wikipedia.org"}:
        return []
    path = normalized_path(parsed.path)
    if path.startswith("wiki/") and parsed.hostname != "offline.invalid":
        path = path[5:]
        return ["A/" + path, path] if legacy else [path]
    candidates = [path]
    if legacy and path.startswith("A/"):
        candidates.append(path[2:])
    elif legacy and not path.startswith(("I/", "-/")):
        candidates.append("A/" + path)
    return candidates


def external_url(base, reference, legacy=False):
    if not reference or reference.startswith("#"):
        return ""
    try:
        parsed = urlsplit(reference)
    except ValueError:
        return ""
    if parsed.scheme.casefold() in ADMIN_NAMESPACES:
        absolute = source_url(unquote(reference), legacy)
    elif parsed.scheme or parsed.netloc:
        absolute = urljoin("https://en.wikipedia.org/", reference)
    elif reference.startswith("/wiki/"):
        absolute = "https://en.wikipedia.org" + reference
    else:
        absolute = source_url(normalized_path(urlsplit(urljoin(base_url(base), reference)).path), legacy)
    try:
        parsed = urlsplit(absolute)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return absolute.replace("http://", "https://", 1)


def first(tree, expression):
    nodes = tree.xpath(expression)
    return nodes[0] if nodes else None


def text(element, limit=None):
    result = " ".join(element.text_content().split()) if element is not None else ""
    return result[:limit] if limit else result


def fragment_id(identifier, original):
    return "wm-" + identifier + "-" + hashlib.sha256(original.encode()).hexdigest()[:16]


def page_config(tree):
    """Read explicit Wikipedia export metadata as JSON, never execute source JS."""
    for script in tree.xpath("//script[@id='mwoffliner-jsConfigVars']"):
        match = re.search(r"\bRLCONF\s*=\s*", script.text or "")
        if match:
            try:
                value, _ = json.JSONDecoder().raw_decode((script.text or "")[match.end():])
                return value if isinstance(value, dict) else {}
            except ValueError:
                pass
    return {}


def sanitize(element, base, identifier, article_ids, media, legacy=False):
    """Preserve article content, clinical notices and references; allow local images only."""
    element = html.fromstring(etree.tostring(element), parser=html.HTMLParser(encoding="utf-8", recover=True))
    wrapper = html.Element("div")
    wrapper.append(element)
    element = wrapper
    images, media_sources, related = [], [], {}
    for node in list(element.iter()):
        if not isinstance(node.tag, str):
            if node.getparent() is not None:
                node.drop_tree()
            continue
        tag = node.tag.lower()
        if tag in DROP_TAGS or set(node.get("class", "").split()) & NAV_CLASSES:
            if node.getparent() is not None:
                node.drop_tree()
            continue
        if tag == "math":
            # MathML cannot execute; retain its textual formula when source has no
            # local raster fallback, rather than discarding a clinical formula.
            formula = node.get("alttext") or node.get("aria-label") or text(node)
            node.clear()
            node.tag, node.text = tag, formula
            tag = node.tag = "span"
        if tag not in ALLOWED_TAGS:
            if node.getparent() is not None:
                node.drop_tag()
            continue
        attrs = {}
        original_id = node.get("id") or (node.get("name") if tag == "a" else None)
        if original_id:
            attrs["id"] = fragment_id(identifier, original_id)
        if tag == "a":
            href = node.get("href", "")
            if href.startswith("#"):
                attrs["href"] = "#" + fragment_id(identifier, unquote(href[1:]))
            else:
                candidates = resolve_reference(base, href, legacy)
                target = next((article_ids[path] for path in candidates if path in article_ids), None)
                if target:
                    attrs["href"] = "/?guide=" + target
                    if urlsplit(href).fragment:
                        attrs["href"] += "#" + fragment_id(target, unquote(urlsplit(href).fragment))
                    if target != identifier:
                        related.setdefault(target, text(node, 180))
                else:
                    absolute = external_url(base, href, legacy)
                    if absolute:
                        attrs.update(href=absolute, rel="noopener noreferrer")
        elif tag == "img":
            reference = node.get("src") or node.get("data-src") or node.get("data-lazy-src") or ""
            candidates = resolve_reference(base, reference, legacy)
            media_path = next((path for path in candidates if path in media), None)
            if media_path is None:
                if node.getparent() is not None:
                    node.drop_tree()
                continue
            local = media[media_path]
            attrs.update(src=local, alt=node.get("alt", "")[:500], loading="lazy", decoding="async")
            for dimension in ("width", "height"):
                value = node.get(dimension, "")
                if value.isdigit() and 0 < int(value) <= 10000:
                    attrs[dimension] = value
            images.append(local)
            file_page = ""
            parent = node.getparent()
            if parent is not None and parent.tag == "a":
                file_page = parent.get("href", "")
                if not file_page.startswith("https://"):
                    file_page = external_url(base, file_page, legacy)
            media_sources.append({"url": local, "sourcePath": media_path, "filePageUrl": file_page})
        elif tag in {"td", "th"}:
            for name in ("colspan", "rowspan"):
                value = node.get(name, "")
                if value.isdigit() and 0 < int(value) <= 100:
                    attrs[name] = value
        node.attrib.clear()
        node.attrib.update(attrs)
    return etree.tostring(element, encoding="unicode", method="html"), list(dict.fromkeys(images)), media_sources, related


def source_metadata(raw):
    path = raw / (ARCHIVE_NAME + ".meta4")
    if not path.exists():
        with urlopen(BASE + path.name, timeout=45) as response:
            atomic_write(path, response.read())
    namespace = {"m": "urn:ietf:params:xml:ns:metalink"}
    root = ET.fromstring(path.read_bytes())
    file = root.find("m:file", namespace)
    metadata = {"size": int(file.find("m:size", namespace).text),
                "sha256": file.find("m:hash[@type='sha-256']", namespace).text,
                "mirrors": [item.text for item in file.findall("m:url", namespace)
                            if item.text and item.text.startswith("https://")]}
    if metadata["size"] != ARCHIVE_BYTES or metadata["sha256"] != ARCHIVE_SHA256:
        raise RuntimeError("Official WikiMed Metalink differs from pinned size/SHA-256; review source revision before updating.")
    if not metadata["mirrors"]:
        raise RuntimeError("Official WikiMed Metalink has no HTTPS download mirrors.")
    return metadata


def download(raw, metadata):
    raw.mkdir(parents=True, exist_ok=True)
    archive = raw / ARCHIVE_NAME
    if archive.exists() and archive.stat().st_size == metadata["size"] and not Path(str(archive) + ".aria2").exists():
        return archive
    log(f"Downloading complete {REVISION}: {metadata['size']:,} bytes")
    if shutil.which("aria2c"):
        subprocess.run(["aria2c", "--continue=true", "--auto-file-renaming=false", "--split=4", "--max-connection-per-server=4",
                        "--min-split-size=32M", "--file-allocation=none", "--summary-interval=30", "--console-log-level=warn",
                        "--max-tries=2", "--connect-timeout=15", "--timeout=45", "--show-console-readout=false",
                        "--dir=" + str(raw), "--out=" + archive.name, *metadata["mirrors"]], check=True)
    else:
        for url in metadata["mirrors"]:
            result = subprocess.run(["curl", "-L", "--fail", "--retry", "2", "--connect-timeout", "15", "--continue-at", "-", url, "-o", str(archive)])
            if result.returncode == 0:
                break
        else:
            raise RuntimeError("All official WikiMed mirrors failed; the partial archive is retained for resuming.")
    return archive


def verify(archive, metadata):
    if not archive.exists() or archive.stat().st_size != metadata["size"] or Path(str(archive) + ".aria2").exists():
        raise RuntimeError("WikiMed archive is absent/incomplete; run without --build-only to download it.")
    log("Verifying complete WikiMed SHA-256 against pinned official Metalink")
    actual = digest_file(archive)
    if actual != metadata["sha256"]:
        raise RuntimeError("WikiMed archive SHA-256 mismatch: " + actual)
    return actual


def completed(data_dir):
    """Completion metadata only: never enumerate or rehash the finished corpus."""
    runtime = data_dir / "runtime"
    try:
        manifest = json.loads((runtime / "manifest.json").read_bytes())
        if (manifest.get("runtimeComplete") and manifest.get("ingestionKey") == ingestion_key() and
                manifest.get("archiveSha256") == ARCHIVE_SHA256 == manifest.get("expectedSha256") and
                manifest.get("archiveChecksumVerified") and manifest.get("zimChecksumVerified") and
                manifest.get("indexedItems", 0) > 0 and manifest.get("searchEnrichment", {}).get("version") == 1 and
                (runtime / "index.json").stat().st_size == manifest["indexBytes"] and
                (runtime / "index.json.gz").stat().st_size == manifest["indexGzipBytes"] and
                (runtime / "guides").is_dir() and (runtime / "media").is_dir() and (data_dir / "catalog.sqlite").is_file()):
            return manifest
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


_NORMALIZER = None


def normalize_row(row):
    # The callable and read-only identity/media maps are inherited once via fork,
    # rather than serialized with every document. No worker accesses SQLite/ZIM.
    return _NORMALIZER(row)


def build(data_dir, archive, metadata, sha256, worker_count=None):
    started = time.monotonic()
    key = ingestion_key()
    zim = Archive(archive)
    legacy = not zim.has_new_namespace_scheme
    if not zim.check():
        raise RuntimeError("WikiMed libzim internal archive checksum failed")
    runtime = data_dir / "runtime-building"
    runtime.mkdir(parents=True, exist_ok=True)
    for name in ("guides", "media"):
        (runtime / name).mkdir(exist_ok=True)
    atomic_write(runtime / "manifest.json", json_bytes({"version": 1, "revision": REVISION, "runtimeComplete": False}))
    work = sqlite3.connect(data_dir / "work.sqlite")
    work.execute("PRAGMA journal_mode=OFF")
    work.execute("PRAGMA synchronous=OFF")
    work.executescript("DROP TABLE IF EXISTS inventory; DROP TABLE IF EXISTS pages; CREATE TABLE inventory(path TEXT PRIMARY KEY,title TEXT,mime TEXT,redirect TEXT,bytes INTEGER); CREATE TABLE pages(path TEXT PRIMARY KEY,title TEXT,html BLOB);")
    article_ids, canonical, titles, aliases, redirects_to = {}, {}, {}, defaultdict(set), {}
    total = html_count = redirects = 0
    exclusions, mime_counts = Counter(), Counter()
    batch = []
    log("Inventorying new WikiMed archive entries and redirect aliases")
    for entry in zim.iter_by_path():
        total += 1
        path, item = entry.path, entry.get_item()
        mime = item.mimetype
        mime_counts[mime] += 1
        target = item.path if entry.is_redirect else ""
        batch.append((path, entry.title, mime, target, item.size))
        if entry.is_redirect:
            redirects += 1
            redirects_to[path] = target
            if article_name(path, legacy):
                aliases[target].add((entry.title or article_name(path, legacy)).replace("_", " "))
        elif mime.startswith("text/html"):
            html_count += 1
            if article_name(path, legacy):
                article_ids[path], canonical[path], titles[path] = short_id(path), path, entry.title
            else:
                exclusions[path.split(":", 1)[0].split("/", 1)[0]] += 1
        if len(batch) >= 1000:
            work.executemany("INSERT INTO inventory VALUES(?,?,?,?,?)", batch)
            batch.clear()
    if batch:
        work.executemany("INSERT INTO inventory VALUES(?,?,?,?,?)", batch)
    work.commit()
    # get_item() usually resolves chains; handle archives that expose an
    # intermediate redirect target without creating duplicate articles.
    for path, target in redirects_to.items():
        seen = {path}
        while target in redirects_to and target not in seen:
            seen.add(target)
            target = redirects_to[target]
        if target in canonical:
            article_ids[path] = article_ids[target]
            if article_name(path, legacy):
                aliases[target].update(aliases.get(redirects_to[path], ()))
    log(f"WikiMed: {len(canonical):,} canonical main articles, {redirects:,} redirects; extracting source HTML/media")
    media, media_rows, unique_media = {}, [], {}
    pages = 0
    batch = []
    for entry in zim.iter_efficient():
        if entry.is_redirect:
            continue
        path, item = entry.path, entry.get_item()
        if item.mimetype.startswith("image/"):
            body = bytes(item.content)
            extension = IMAGE_EXT.get(item.mimetype, mimetypes.guess_extension(item.mimetype) or ".bin")
            filename = hashlib.sha256(body).hexdigest() + extension
            if not (runtime / "media" / filename).exists():
                atomic_write(runtime / "media" / filename, body)
            media[path] = "/media/" + filename
            unique_media[filename] = len(body)
            media_rows.append((path, filename, item.mimetype, len(body)))
        elif path in canonical:
            batch.append((path, titles[path], bytes(item.content)))
            pages += 1
            if len(batch) >= 128:
                work.executemany("INSERT INTO pages VALUES(?,?,?)", batch)
                work.commit()
                batch.clear()
        if (pages + len(unique_media)) % 20000 == 0 and pages + len(unique_media):
            log(f"Extracted {pages:,} WikiMed articles + {len(unique_media):,} unique images")
    if batch:
        work.executemany("INSERT INTO pages VALUES(?,?,?)", batch)
        work.commit()
    for path, target in redirects_to.items():
        if target in media:
            media[path] = media[target]
    database_path = data_dir / "catalog-building.sqlite"
    database_path.unlink(missing_ok=True)
    inventory = sqlite3.connect(database_path)
    # This disposable inventory is unpublished and rebuilt after interruption.
    # Avoid a durable fsync for every small batch; completion is published last.
    inventory.execute("PRAGMA journal_mode=OFF")
    inventory.execute("PRAGMA synchronous=OFF")
    inventory.executescript("CREATE TABLE items(id TEXT PRIMARY KEY,source_path TEXT UNIQUE,title TEXT,category TEXT,kind TEXT,device TEXT,summary TEXT,source_url TEXT); CREATE TABLE source_categories(path TEXT PRIMARY KEY,title TEXT,category TEXT); CREATE TABLE category_edges(parent TEXT,child TEXT,PRIMARY KEY(parent,child)); CREATE TABLE guide_categories(guide TEXT,category TEXT,PRIMARY KEY(guide,category)); CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE redirects(path TEXT PRIMARY KEY,title TEXT,target TEXT); CREATE TABLE media(path TEXT PRIMARY KEY,filename TEXT,mime TEXT,bytes INTEGER); CREATE TABLE media_attribution(article_id TEXT,url TEXT,source_path TEXT,file_page_url TEXT,PRIMARY KEY(article_id,url,source_path,file_page_url));")
    inventory.executemany("INSERT INTO media VALUES(?,?,?,?)", media_rows)
    inventory.executemany("INSERT INTO redirects SELECT ?,?,?", [(path, title, target) for path, title, target in work.execute("SELECT path,title,redirect FROM inventory WHERE redirect!=''")])
    inventory.commit()

    def normalize(row):
        path, archive_title, payload = row
        tree = html.fromstring(payload, parser=html.HTMLParser(encoding="utf-8", recover=True))
        config = page_config(tree)
        title = text(first(tree, "//h1")) or archive_title or article_name(path, legacy).replace("_", " ")
        content = first(tree, "//*[@id='mw-content-text']/*[contains(concat(' ',normalize-space(@class),' '),' mw-parser-output ')]")
        if content is None:
            content = first(tree, "//*[@id='mw-content-text']")
        if content is None:
            content = first(tree, "//body")
        if content is None:
            content = tree
        configured_categories = config.get("wgCategories")
        if isinstance(configured_categories, list):
            source_categories = ["Category:" + name.replace(" ", "_") for name in configured_categories if isinstance(name, str)]
        else:
            source_categories = []
            for anchor in tree.xpath("//a[contains(@href,'Category:') or contains(@href,'Category%3A')]"):
                candidates = resolve_reference(path, anchor.get("href", ""), legacy)
                target = next((name.removeprefix("A/") if legacy else name for name in candidates
                               if (name.removeprefix("A/") if legacy else name).startswith("Category:")), "")
                if target:
                    source_categories.append(target)
        identifier = article_ids[path]
        body, images, media_sources, related = sanitize(content, path, identifier, article_ids, media, legacy)
        summary_tree = html.fromstring(body)
        summary = next((text(node, 500) for node in summary_tree.xpath(".//p") if text(node)), "")
        source = source_url(path, legacy)
        record = {"id": identifier, "title": title, "category": "health", "kind": "article", "device": "",
                  "sourceId": "wikimed", "sourceName": "Wikipedia", "sourceUrl": source,
                  "sourceHistoryUrl": source + "?action=history", "sourceRevision": REVISION,
                  "sourceRevisionId": config.get("wgCurRevisionId"),
                  "sourceRevisionUrl": ("https://en.wikipedia.org/w/index.php?title=" + quote(article_name(path, legacy)) + "&oldid=" + str(config["wgCurRevisionId"])) if isinstance(config.get("wgCurRevisionId"), int) else "",
                  "license": LICENSE, "attribution": "Wikipedia contributors via WikiMed / Kiwix.",
                  "mediaLicenseNotice": MEDIA_NOTICE, "sourceCategories": sorted(set(source_categories)),
                  "summary": summary, "html": body, "images": images, "mediaSources": media_sources,
                  "related": [{"id": target, "title": label} for target, label in list(related.items())[:40]]}
        payload = json_bytes(record)
        atomic_write(runtime / "guides" / (identifier + ".json"), payload)
        atomic_write(runtime / "guides" / (identifier + ".json.gz"), gzip.compress(payload, compresslevel=6, mtime=0))
        # The parent needs only inventory metadata, not another copy of rich HTML.
        record.pop("html")
        record.pop("images")
        record.pop("related")
        return path, record, len(payload)

    rows, group_texts, group_numbers, references = [], [""], {"": 0}, {}
    json_size = enriched = done = 0
    workers = worker_count if worker_count is not None else (4 if pages >= 512 else 1)
    log(f"Normalizing complete WikiMed article content ({pages:,} documents; {workers} workers)")
    cursor = work.execute("SELECT path,title,html FROM pages ORDER BY path")
    global _NORMALIZER
    _NORMALIZER = normalize
    process_pool = multiprocessing.get_context("fork").Pool(workers) if workers > 1 else None
    with process_pool if process_pool is not None else nullcontext() as pool:
        while block := cursor.fetchmany(128):
            records = pool.imap(normalize_row, block, chunksize=4) if pool is not None else map(normalize, block)
            for path, record, size in records:
                identifier = record["id"]
                rows.append([record[name] for name in ("id", "title", "category", "kind", "device")])
                own = clean(record["title"])
                alias_text = "\n".join(sorted({clean(name) for name in aliases.get(path, ()) if clean(name) and clean(name) != own}))
                if alias_text not in group_numbers:
                    group_numbers[alias_text] = len(group_texts)
                    group_texts.append(alias_text)
                references[identifier] = group_numbers[alias_text]
                enriched += bool(alias_text)
                inventory.execute("INSERT INTO items VALUES(?,?,?,?,?,?,?,?)", (identifier, path, record["title"], "health", "article", "", record["summary"], record["sourceUrl"]))
                for category in record["sourceCategories"]:
                    inventory.execute("INSERT OR IGNORE INTO source_categories VALUES(?,?,?)", (category, category.removeprefix("Category:").replace("_", " "), "health"))
                    inventory.execute("INSERT OR IGNORE INTO guide_categories VALUES(?,?)", (path, category))
                inventory.executemany("INSERT OR IGNORE INTO media_attribution VALUES(?,?,?,?)", [(identifier, item["url"], item["sourcePath"], item["filePageUrl"]) for item in record["mediaSources"]])
                json_size += size
                done += 1
            inventory.commit()
            if done % 2048 == 0:
                log(f"Normalized {done:,}/{pages:,} WikiMed articles")
    if not rows:
        raise RuntimeError("WikiMed contains no canonical main article pages; refusing to publish")
    rows.sort(key=lambda row: (row[1].casefold(), row[0]))
    index = {"version": 1, "revision": REVISION, "source": SOURCE, "categories": CATEGORIES,
             "fields": ["id", "title", "category", "kind", "device"], "items": rows,
             "search": {"version": 1, "normalization": "NFKD-no-marks-lower", "groups": group_texts,
                        "itemGroups": [references[row[0]] for row in rows], "enrichedItems": enriched,
                        "sources": [{"id": "wikimed", "url": CATALOG_URL, "revision": REVISION, "archiveSha256": sha256, "license": LICENSE}],
                        "note": "Only explicit source redirect titles expand WikiMed names; no inferred diagnosis or disease equivalence."}}
    payload = json_bytes(index)
    packed = gzip.compress(payload, compresslevel=9, mtime=0)
    atomic_write(runtime / "index.json", payload)
    atomic_write(runtime / "index.json.gz", packed)
    archive_metadata = {name: zim.get_metadata(name).decode("utf-8", errors="replace") for name in zim.metadata_keys if not name.startswith("Illustration_")}
    manifest = {"version": 1, "revision": REVISION, "ingestionKey": key,
                "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "source": SOURCE,
                "catalogUrl": CATALOG_URL, "archiveUrl": BASE + ARCHIVE_NAME,
                "archiveBytes": archive.stat().st_size, "archiveSha256": sha256, "expectedSha256": metadata["sha256"],
                "archiveChecksumVerified": True, "zimChecksumVerified": True, "archiveMetadata": archive_metadata,
                "archiveEntries": total, "archiveArticleCount": zim.article_count, "archiveMediaCount": zim.media_count,
                "canonicalHtmlPages": html_count, "redirectEntries": redirects, "excludedCanonicalHtmlByNamespace": dict(exclusions),
                "indexedItems": len(rows), "categoryCounts": {"health": len(rows)},
                "classification": "All main articles from the English WikiMed medical selection map to Health & medicine; explicit source categories are retained separately.",
                "mimeCounts": dict(mime_counts), "uniqueMediaFiles": len(unique_media), "mediaBytes": sum(unique_media.values()),
                "guideJsonBytes": json_size, "indexBytes": len(payload), "indexGzipBytes": len(packed),
                "searchEnrichment": {"version": 1, "enrichedItems": enriched, "sharedGroups": len(group_texts), "redirectAliases": sum(len(names) for names in aliases.values()), "sources": index["search"]["sources"]},
                "transformations": "Canonical main articles retained in full, including clinical warnings and references; source navigation and executable markup removed; images and article links rewritten locally; redirect titles become searchable aliases.",
                "licenseNotice": "Adapted text CC BY-SA 4.0, attributed to Wikipedia contributors with original/history links. " + MEDIA_NOTICE,
                "elapsedSeconds": round(time.monotonic() - started, 3), "runtimeComplete": True}
    for name, value in (("revision", REVISION), ("archive_sha256", sha256), ("ingestion_key", key)):
        inventory.execute("INSERT INTO metadata VALUES(?,?)", (name, value))
    inventory.commit()
    inventory.close()
    work.close()
    atomic_write(runtime / "manifest.json", json_bytes(manifest))
    previous = data_dir / "runtime.previous"
    if previous.exists():
        shutil.rmtree(previous)
    destination = data_dir / "runtime"
    if destination.exists():
        destination.rename(previous)
    runtime.rename(destination)
    database_path.replace(data_dir / "catalog.sqlite")
    atomic_write(data_dir / "manifest.json", json_bytes(manifest))
    if previous.exists():
        shutil.rmtree(previous)
    log(f"COMPLETE: {len(rows):,} WikiMed articles, {len(unique_media):,} images; compressed index {len(packed):,} bytes")
    return manifest


def assert_output_untracked(data_dir):
    try:
        relative = data_dir.relative_to(ROOT.resolve())
    except ValueError:
        return
    result = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--quiet", "--", str(relative / "catalog-output-probe")], capture_output=True)
    if result.returncode != 0:
        raise RuntimeError("WikiMed data inside the source checkout must be Git-ignored")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "catalog-data/wikimed")
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument("--download-only", action="store_true")
    operations.add_argument("--build-only", action="store_true")
    parser.add_argument("--print-key", action="store_true")
    args = parser.parse_args()
    if args.print_key:
        print(ingestion_key())
        return
    data_dir = args.data_dir.resolve()
    assert_output_untracked(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / ".ingest.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another WikiMed ingestion is using this data directory") from error
        if not args.download_only and (manifest := completed(data_dir)):
            log("Reusing completed WikiMed source " + manifest["ingestionKey"])
            return
        raw = data_dir / "raw"
        raw.mkdir(exist_ok=True)
        metadata = source_metadata(raw)
        archive = raw / ARCHIVE_NAME if args.build_only else download(raw, metadata)
        checksum = verify(archive, metadata)
        if not args.download_only:
            manifest = build(data_dir, archive, metadata, checksum)
            print(json.dumps({name: manifest[name] for name in ("revision", "indexedItems", "uniqueMediaFiles", "mediaBytes", "indexBytes", "indexGzipBytes", "elapsedSeconds")}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print("WikiMed ingestion failed: " + str(error), file=sys.stderr)
        sys.exit(1)
