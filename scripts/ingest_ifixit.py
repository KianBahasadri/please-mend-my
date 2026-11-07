#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["libzim==4.1.0", "lxml==6.1.3"]
# ///
"""Download, verify and normalize the complete current English Kiwix iFixit archive.

Content is CC BY-NC-SA 3.0; this is retrieval/formatting, never model training.
Everything it writes stays in the gitignored catalog-data directory: the ZIM,
SQLite inventory, and runtime snapshot are local only and must not be committed.
Run: uv run scripts/ingest_ifixit.py [--download-only | --build-only]
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
import html as html_escape
import json
import mimetypes
from pathlib import Path
import posixpath
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
from urllib.request import urlopen
import xml.etree.ElementTree as ET

from libzim.reader import Archive
from lxml import etree, html

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "catalog-data"
RAW = DATA / "raw"
RUNTIME = DATA / "runtime"
REVISION = "ifixit_en_all_2025-12"
ARCHIVE = RAW / f"{REVISION}.zim"
BASE = "https://download.kiwix.org/zim/ifixit/"
SOURCE_URL = "https://browse.library.kiwix.org/#lang=eng&category=iFixit"
CATALOG_URL = "https://library.kiwix.org/catalog/v2/entries?lang=eng&category=ifixit&count=100"
LICENSE = {"name": "CC BY-NC-SA 3.0", "url": "https://creativecommons.org/licenses/by-nc-sa/3.0/"}
CATEGORIES = [
    {"id": "technology", "name": "Technology"},
    {"id": "household", "name": "Household & appliances"},
    {"id": "handyman", "name": "Everyday objects & DIY"},
    {"id": "vehicles", "name": "Vehicles & transport"},
    {"id": "medical", "name": "Medical equipment"},
    {"id": "other", "name": "Other repairs & reference"},
]
CATEGORY_ROOTS = {
    "medical": {"medical device", "medical equipment", "medical", "hospital", "health"},
    "vehicles": {"vehicle", "vehicles", "car and truck", "car", "truck", "bicycle", "motorcycle", "automotive", "bike"},
    "household": {"appliance", "appliances", "home appliance", "household", "home", "kitchen", "small appliance", "major appliance"},
    "handyman": {"clothing", "apparel", "tool", "tools", "household object", "household objects", "furniture", "skills", "bag", "shoe", "shoes", "outdoor", "sporting goods"},
    "technology": {"electronics", "electronic", "computer hardware", "computer", "phone", "tablet", "pc", "mac", "laptop", "game console", "camera", "audio", "electronics skills"},
}
# Titles are a secondary fallback only when the archive contains no source category relationship.
FALLBACKS = {
    "medical": r"\b(ventilator|defibrillator|infusion pump|syringe pump|patient monitor|pulse oximeter|medical|hospital|surgical|stethoscope|nebulizer|cpap)\b",
    "vehicles": r"\b(bicycle|motorcycle|scooter|car|truck|automotive|honda|toyota|ford|chevrolet|volkswagen|bmw|mercedes|nissan|subaru|mazda|jeep|engine|brake|tire|wheel|spark plug|lawn mower|chainsaw)\b",
    "household": r"\b(refrigerator|dishwasher|washing machine|washer|dryer|oven|microwave|toaster|blender|vacuum|coffee|kettle|appliance|ceiling fan|air conditioner|faucet|toilet|sink|garbage disposal)\b",
    "handyman": r"\b(clothing|jacket|zipper|shoe|boot|bag|backpack|chair|table|furniture|door|window|drill|saw|screwdriver|hammer|tent|umbrella|buckle|sewing|stitch|jeans|pants|strap|luggage)\b",
    "technology": r"\b(iphone|ipad|ipod|imac|macbook|mac|samsung|galaxy|phone|tablet|laptop|computer|playstation|xbox|nintendo|camera|headphone|speaker|printer|monitor|motherboard|keyboard|smartwatch|watch|pixel|lenovo|dell|acer|asus|hp|sony|lg|huawei|motorola|nokia|battery|screen|usb|software|firmware)\b",
}
ALLOWED_TAGS = {"a", "abbr", "b", "blockquote", "br", "caption", "code", "dd", "del", "details", "div", "dl", "dt", "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "kbd", "li", "ol", "p", "pre", "s", "section", "small", "span", "strong", "sub", "summary", "sup", "table", "tbody", "td", "th", "thead", "tr", "u", "ul"}
DROP_TAGS = {"script", "style", "iframe", "object", "embed", "svg", "math", "form", "input", "button", "textarea", "select", "option", "nav", "noscript", "template"}
IMAGE_EXT = {"image/webp": ".webp", "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/svg+xml": ".svg", "image/avif": ".avif", "image/bmp": ".bmp", "image/vnd.microsoft.icon": ".ico", "image/x-icon": ".ico"}


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def atomic_write(path: Path, payload: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(payload)
    temp.replace(path)


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def digest_file(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def source_metadata():
    RAW.mkdir(parents=True, exist_ok=True)
    metalink = RAW / f"{REVISION}.zim.meta4"
    if not metalink.exists():
        with urlopen(BASE + metalink.name, timeout=30) as response:
            atomic_write(metalink, response.read())
    root = ET.fromstring(metalink.read_bytes())
    ns = {"m": "urn:ietf:params:xml:ns:metalink"}
    file = root.find("m:file", ns)
    return {
        "size": int(file.find("m:size", ns).text),
        "sha256": file.find("m:hash[@type='sha-256']", ns).text,
        "mirrors": [item.text for item in file.findall("m:url", ns)],
    }


def download(metadata):
    RAW.mkdir(parents=True, exist_ok=True)
    if ARCHIVE.exists() and ARCHIVE.stat().st_size == metadata["size"] and not Path(str(ARCHIVE) + ".aria2").exists():
        return
    url = metadata["mirrors"][0]
    log(f"Downloading entire {REVISION}: {metadata['size']:,} bytes")
    if shutil.which("aria2c"):
        subprocess.run(["aria2c", "--continue=true", "--split=4", "--max-connection-per-server=4", "--min-split-size=32M", "--file-allocation=none", "--summary-interval=30", "--console-log-level=warn", f"--dir={RAW}", f"--out={ARCHIVE.name}", url], check=True)
    else:
        subprocess.run(["curl", "-L", "--fail", "--retry", "4", "--continue-at", "-", url, "-o", str(ARCHIVE)], check=True)


def verify(metadata):
    if not ARCHIVE.exists() or ARCHIVE.stat().st_size != metadata["size"]:
        raise RuntimeError("Archive is absent/incomplete; run without --build-only to download it.")
    log("Verifying complete archive SHA-256 against official Metalink")
    actual = digest_file(ARCHIVE)
    if actual != metadata["sha256"]:
        raise RuntimeError(f"Archive checksum mismatch: {actual}")
    return actual


def normalized_path(path):
    for _ in range(2):
        path = unquote(path)
    return posixpath.normpath(path.split("?", 1)[0].split("#", 1)[0]).lstrip("/")


def resolve_reference(base, reference):
    if not reference or reference.startswith(("data:", "javascript:", "mailto:", "tel:")):
        return None
    parsed = urlsplit(urljoin("https://offline.invalid/" + base, reference))
    if parsed.hostname not in {"offline.invalid", "www.ifixit.com", "ifixit.com", "en.ifixit.com"}:
        return None
    return normalized_path(parsed.path)


def short_id(path):
    return hashlib.sha256(path.encode()).hexdigest()[:20]


def tokens(value):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def kind_for(path):
    first = path.split("/", 1)[0].lower()
    if first == "guide":
        return "guide"
    if first == "device":
        return "topic"
    if first in {"info", "wiki", "answers", "teardown", "news"}:
        return "article"
    return None


def text(element, limit=None):
    if element is None:
        return ""
    result = " ".join(element.text_content().split())
    return result[:limit] if limit else result


def first(tree, expression):
    found = tree.xpath(expression)
    return found[0] if found else None


def parse_page(payload):
    return html.fromstring(payload, parser=html.HTMLParser(encoding="utf-8", recover=True))


def build(metadata, sha256):
    log("Opening verified archive and inventorying canonical entries")
    zim = Archive(ARCHIVE)
    if not zim.check():
        raise RuntimeError("libzim internal archive checksum failed")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    (RUNTIME / "guides").mkdir(exist_ok=True)
    (RUNTIME / "media").mkdir(exist_ok=True)
    work_path = DATA / "work.sqlite"
    work = sqlite3.connect(work_path)
    work.execute("PRAGMA journal_mode=OFF")
    work.execute("PRAGMA synchronous=OFF")
    work.executescript("CREATE TABLE IF NOT EXISTS pages(path TEXT PRIMARY KEY,title TEXT NOT NULL,kind TEXT NOT NULL,html BLOB NOT NULL); CREATE TABLE IF NOT EXISTS inventory(path TEXT PRIMARY KEY,title TEXT,mime TEXT,redirect TEXT,bytes INTEGER); CREATE TABLE IF NOT EXISTS media_map(path TEXT PRIMARY KEY,filename TEXT NOT NULL,bytes INTEGER NOT NULL); CREATE TABLE IF NOT EXISTS build_metadata(key TEXT PRIMARY KEY,value TEXT);")
    previous_archive = work.execute("SELECT value FROM build_metadata WHERE key='archive_sha256'").fetchone()
    if previous_archive and previous_archive[0] != sha256:
        raise RuntimeError("Scratch inventory belongs to a different archive; move work.sqlite before rebuilding.")
    work.execute("INSERT OR REPLACE INTO build_metadata VALUES('archive_sha256',?)", (sha256,))
    work.commit()
    aliases, article_ids, title_map, mime_counts = {}, {}, {}, Counter()
    exclusions = Counter()
    html_count = redirects = total = 0
    inventory_batch = []
    for entry in zim.iter_by_path():
        total += 1
        path = normalized_path(entry.path)
        item = entry.get_item()
        mime = item.mimetype
        mime_counts[mime] += 1
        redirect = normalized_path(item.path) if entry.is_redirect else ""
        inventory_batch.append((path, entry.title, mime, redirect, item.size))
        if entry.is_redirect:
            redirects += 1
            aliases[path] = redirect
        elif mime.startswith("text/html"):
            html_count += 1
            kind = kind_for(path)
            if kind:
                article_ids[path] = short_id(path)
                title_map[path] = entry.title
            else:
                exclusions[path.split("/", 1)[0] or "root"] += 1
        if len(inventory_batch) >= 1000:
            work.executemany("INSERT OR REPLACE INTO inventory VALUES(?,?,?,?,?)", inventory_batch)
            inventory_batch.clear()
    if inventory_batch:
        work.executemany("INSERT OR REPLACE INTO inventory VALUES(?,?,?,?,?)", inventory_batch)
    work.commit()
    log(f"Archive inventory: {total:,} entries, {html_count:,} canonical HTML pages, {len(article_ids):,} useful guide/topic/article pages, {redirects:,} redirects")

    media, parents, memberships = {}, defaultdict(set), defaultdict(set)
    page_count = image_count = media_bytes = 0
    media_records = dict((path, (filename, size)) for path, filename, size in work.execute("SELECT path,filename,bytes FROM media_map"))
    unique_media = {}
    media_batch = []
    log("Extracting all archive images and useful HTML in efficient archive order")
    batch = []
    for entry in zim.iter_efficient():
        if entry.is_redirect:
            continue
        path = normalized_path(entry.path)
        item = entry.get_item()
        if item.mimetype.startswith("image/"):
            cached = media_records.get(path)
            if cached and (RUNTIME / "media" / cached[0]).is_file():
                filename, size = cached
            else:
                body = bytes(item.content)
                extension = IMAGE_EXT.get(item.mimetype, mimetypes.guess_extension(item.mimetype) or ".bin")
                filename = hashlib.sha256(body).hexdigest() + extension
                destination = RUNTIME / "media" / filename
                size = len(body)
                if not destination.exists():
                    atomic_write(destination, body)
                media_batch.append((path, filename, size))
                if len(media_batch) >= 1000:
                    work.executemany("INSERT OR REPLACE INTO media_map VALUES(?,?,?)", media_batch)
                    work.commit()
                    media_batch.clear()
            unique_media[filename] = size
            image_count = len(unique_media)
            media[path] = "/media/" + filename
        elif path in article_ids:
            body = bytes(item.content)
            kind = kind_for(path)
            batch.append((path, entry.title, kind, body))
            page_count += 1
            if kind == "topic":
                tree = parse_page(body)
                for anchor in tree.xpath("//*[contains(concat(' ',normalize-space(@class),' '),' categoryAnchor ')]"):
                    target = resolve_reference(path, anchor.get("href", ""))
                    target = aliases.get(target, target)
                    if target and target in article_ids and kind_for(target) == "topic" and target != path:
                        parents[target].add(path)
                for anchor in tree.xpath("//a[@href]"):
                    target = resolve_reference(path, anchor.get("href", ""))
                    target = aliases.get(target, target)
                    if target and target in article_ids and kind_for(target) == "guide":
                        memberships[target].add(path)
            if len(batch) >= 250:
                work.executemany("INSERT OR REPLACE INTO pages VALUES(?,?,?,?)", batch)
                work.commit()
                batch.clear()
        if (page_count + image_count) % 10000 == 0 and page_count + image_count:
            log(f"Extracted {page_count:,} pages + {image_count:,} unique media files")
    if batch:
        work.executemany("INSERT OR REPLACE INTO pages VALUES(?,?,?,?)", batch)
        work.commit()
    if media_batch:
        work.executemany("INSERT OR REPLACE INTO media_map VALUES(?,?,?)", media_batch)
        work.commit()
    media_bytes = sum(unique_media.values())
    # Alias lookup uses canonical identities; no duplicate documents or media copies.
    for path, target in list(aliases.items()):
        seen = {path}
        while target in aliases and target not in seen:
            seen.add(target)
            target = aliases[target]
        if target in article_ids:
            article_ids[path] = article_ids[target]
        if target in media:
            media[path] = media[target]

    inventory_path = DATA / "catalog.sqlite"
    if inventory_path.exists():
        inventory_path.unlink()
    inventory = sqlite3.connect(inventory_path)
    inventory.executescript("CREATE TABLE items(id TEXT PRIMARY KEY,source_path TEXT UNIQUE,title TEXT,category TEXT,kind TEXT,device TEXT,summary TEXT,source_url TEXT); CREATE TABLE source_categories(path TEXT PRIMARY KEY,title TEXT,category TEXT); CREATE TABLE category_edges(parent TEXT,child TEXT,PRIMARY KEY(parent,child)); CREATE TABLE guide_categories(guide TEXT,category TEXT,PRIMARY KEY(guide,category)); CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);")

    category_cache, classification_basis = {}, Counter()
    def category_for(path, title=""):
        if path in category_cache:
            return category_cache[path]
        start = [path] if kind_for(path) == "topic" else sorted(memberships.get(path, []))
        queue, seen = deque((node, 0) for node in start), set()
        candidates = []
        while queue:
            node, depth = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            category_title = tokens(title_map.get(node, node.rsplit("/", 1)[-1]))
            for category, labels in CATEGORY_ROOTS.items():
                if category_title in labels:
                    candidates.append((depth, list(CATEGORY_ROOTS).index(category), category))
            queue.extend((parent, depth + 1) for parent in sorted(parents.get(node, [])))
        if candidates:
            result, basis = min(candidates)[2], "source_category_ancestry"
        else:
            haystack = tokens(title + " " + " ".join(title_map.get(node, node.rsplit("/", 1)[-1]) for node in seen))
            result = next((key for key, expression in FALLBACKS.items() if re.search(expression, haystack)), "other")
            basis = "source_title_fallback" if result != "other" else "unclassified_reference"
        category_cache[path] = result
        classification_basis[basis] += 1
        return result

    def media_url(base, reference):
        target = resolve_reference(base, reference)
        return media.get(target, "")

    def clean_fragment(element, base):
        if element is None:
            return "", []
        element = html.fromstring(etree.tostring(element), parser=html.HTMLParser(encoding="utf-8"))
        image_urls = []
        for node in list(element.iter()):
            if not isinstance(node.tag, str):
                if node.getparent() is not None:
                    node.drop_tree()
                continue
            tag = node.tag.lower()
            source_classes = set(node.get("class", "").split())
            if source_classes & {"ico-step-icon-caution", "ico-step-icon-warning", "ico-step-icon-note"}:
                node.tag = tag = "strong"
                node.text = "Caution: " if source_classes & {"ico-step-icon-caution", "ico-step-icon-warning"} else "Note: "
            if tag in DROP_TAGS:
                if node.getparent() is not None:
                    node.drop_tree()
                continue
            if tag not in ALLOWED_TAGS:
                if node.getparent() is not None:
                    node.drop_tag()
                continue
            attrs = {}
            if tag == "a":
                href = node.get("href", "")
                target = resolve_reference(base, href)
                if target in article_ids:
                    attrs["href"] = "/?guide=" + article_ids[target]
                elif href.startswith("#"):
                    pass
                else:
                    absolute = urljoin("https://www.ifixit.com/" + base, href)
                    parsed = urlsplit(absolute)
                    if parsed.path in {"/home/external_content", "/home/unavailable_offline"}:
                        original = parse_qs(parsed.query).get("url", [""])[0]
                        absolute = urljoin("https://www.ifixit.com/", original) if original else ""
                    if urlsplit(absolute).scheme in {"http", "https"}:
                        attrs = {"href": absolute.replace("http://", "https://", 1), "rel": "noopener noreferrer"}
            elif tag == "img":
                url = media_url(base, node.get("src") or node.get("data-src") or "")
                if not url:
                    if node.getparent() is not None:
                        node.drop_tree()
                    continue
                attrs = {"src": url, "alt": node.get("alt", "")[:300], "loading": "lazy", "decoding": "async"}
                for dimension in ("width", "height"):
                    value = node.get(dimension, "")
                    if value.isdigit() and 0 < int(value) <= 10000:
                        attrs[dimension] = value
                image_urls.append(url)
            elif tag in {"td", "th"}:
                for attribute in ("colspan", "rowspan"):
                    value = node.get(attribute, "")
                    if value.isdigit() and 0 < int(value) <= 20:
                        attrs[attribute] = value
            node.attrib.clear()
            node.attrib.update(attrs)
        return etree.tostring(element, encoding="unicode", method="html"), image_urls

    def normalize(row):
        path, archive_title, kind, payload = row
        tree = parse_page(payload)
        if kind == "guide":
            title_element = first(tree, "//h1[contains(concat(' ',normalize-space(@class),' '),' guide-title ')]")
        else:
            title_element = first(tree, "//h1")
        title = text(title_element) or archive_title or path.rsplit("/", 1)[-1].replace("_", " ")
        related_paths = sorted(memberships.get(path, []))
        if kind == "topic":
            device = title
        elif related_paths:
            # Specific source membership is preferable to guessing device words.
            device = max((title_map.get(p, p.rsplit("/", 1)[-1].replace("_", " ")) for p in related_paths), key=len)
        else:
            device = ""
        category = category_for(path, title)
        author = first(tree, "//*[contains(concat(' ',normalize-space(@class),' '),' guide-author ')]/a")
        author_name = text(author) or "iFixit contributors"
        attribution = f"{author_name} via iFixit / Kiwix; reformatted and sanitized for this offline catalog. CC BY-NC-SA 3.0."
        selected, images = [], []
        summary_element = first(tree, "//*[contains(concat(' ',normalize-space(@class),' '),' introduction-container ')]//*[@itemprop='description']")
        if summary_element is None:
            summary_element = first(tree, "//*[@itemprop='description']")
        if summary_element is None:
            summary_element = first(tree, "//*[contains(concat(' ',normalize-space(@class),' '),' banner-blurb ')]")
        summary = text(summary_element, 500)
        if kind == "guide":
            # Include every instruction, warning, tool/part and conclusion; omit only comments/navigation.
            selectors = [
                "//*[contains(concat(' ',normalize-space(@class),' '),' guide-details-container ')]",
                "//*[contains(concat(' ',normalize-space(@class),' '),' introduction-container ')]",
                "//*[@id='tools-and-parts']",
                "//*[contains(concat(' ',normalize-space(@class),' '),' steps-container ')]",
                "//*[@id='conclusion' or contains(concat(' ',normalize-space(@class),' '),' conclusion ')]",
            ]
            for expression in selectors:
                for element in tree.xpath(expression):
                    if element not in selected:
                        selected.append(element)
            for node in tree.xpath("//*[contains(concat(' ',normalize-space(@class),' '),' js-comment-container ') or contains(concat(' ',normalize-space(@class),' '),' comments-container ')]"):
                if node.getparent() is not None:
                    node.drop_tree()
        else:
            node = first(tree, "//*[@id='mainBody']")
            if node is None:
                node = first(tree, "//main")
            if node is None:
                node = first(tree, "//body")
            selected = [node if node is not None else tree]
        if not selected:
            fallback = first(tree, "//*[@id='content']")
            selected = [fallback if fallback is not None else tree]
        fragments = []
        for node in selected:
            fragment, found_images = clean_fragment(node, path)
            fragments.append(fragment)
            images.extend(found_images)
        # Include the cover image in cached content as well as card-prefetch media.
        cover = first(tree, "//*[contains(concat(' ',normalize-space(@class),' '),' guide-main-image ')]//img")
        if cover is None:
            cover = first(tree, "//*[contains(concat(' ',normalize-space(@class),' '),' banner-small-photo ')]//img")
        if cover is not None:
            url = media_url(path, cover.get("src", ""))
            if url:
                images.insert(0, url)
        images = list(dict.fromkeys(images))
        related = []
        seen_ids = set()
        for anchor in tree.xpath("//a[@href]"):
            target = resolve_reference(path, anchor.get("href", ""))
            target_id = article_ids.get(target)
            if target_id and target_id != article_ids[path] and target_id not in seen_ids:
                related.append({"id": target_id, "title": title_map.get(target) or text(anchor) or target})
                seen_ids.add(target_id)
        record = {"id": article_ids[path], "title": title, "category": category, "kind": kind, "device": device,
                  "sourceUrl": "https://www.ifixit.com/" + path, "license": LICENSE, "attribution": attribution,
                  "summary": summary, "html": "".join(fragments), "images": images, "related": related[:40]}
        body = json_bytes(record)
        atomic_write(RUNTIME / "guides" / f"{record['id']}.json", body)
        atomic_write(RUNTIME / "guides" / f"{record['id']}.json.gz", gzip.compress(body, compresslevel=6, mtime=0))
        return record, path, len(body)

    log(f"Normalizing full content for {page_count:,} pages; each finished file is atomic for staging sync")
    rows, item_rows, counts, total_json = [], [], Counter(), 0
    # Bound queued documents and memory. lxml and gzip do their expensive work in C.
    cursor = work.execute("SELECT path,title,kind,html FROM pages ORDER BY path")
    completed = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        while block := cursor.fetchmany(128):
            for record, path, body_size in pool.map(normalize, block):
                rows.append([record[key] for key in ("id", "title", "category", "kind", "device")])
                item_rows.append((record["id"], path, record["title"], record["category"], record["kind"], record["device"], record["summary"], record["sourceUrl"]))
                counts[record["category"]] += 1
                total_json += body_size
                completed += 1
            inventory.executemany("INSERT INTO items VALUES(?,?,?,?,?,?,?,?)", item_rows)
            item_rows.clear()
            inventory.commit()
            if completed % 1024 == 0:
                log(f"Normalized {completed:,}/{page_count:,} documents")
    for path in sorted(parents):
        inventory.execute("INSERT OR IGNORE INTO source_categories VALUES(?,?,?)", (path, title_map.get(path, path), category_for(path)))
        inventory.executemany("INSERT OR IGNORE INTO category_edges VALUES(?,?)", [(parent, path) for parent in parents[path]])
    for path, categories in memberships.items():
        inventory.executemany("INSERT OR IGNORE INTO guide_categories VALUES(?,?)", [(path, category) for category in categories])
    inventory.execute("INSERT INTO metadata VALUES(?,?)", ("revision", REVISION))
    inventory.execute("INSERT INTO metadata VALUES(?,?)", ("archive_sha256", sha256))
    inventory.commit()
    inventory.close()
    work.close()
    index = {"version": 1, "revision": REVISION,
             "source": {"name": "iFixit via Kiwix", "url": SOURCE_URL, "archive": ARCHIVE.name, "license": LICENSE,
                        "attribution": "iFixit and its contributors, distributed by Kiwix/openZIM. Offline formatting adapted; CC BY-NC-SA 3.0."},
             "categories": CATEGORIES, "fields": ["id", "title", "category", "kind", "device"], "items": sorted(rows, key=lambda row: (row[1].casefold(), row[0]))}
    index_payload = json_bytes(index)
    index_gzip = gzip.compress(index_payload, compresslevel=9, mtime=0)
    atomic_write(RUNTIME / "index.json", index_payload)
    atomic_write(RUNTIME / "index.json.gz", index_gzip)
    metadata_keys = {key: zim.get_metadata(key).decode("utf-8", errors="replace") for key in zim.metadata_keys if key not in {"Illustration_48x48@1"}}
    manifest = {"version": 1, "revision": REVISION, "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source": index["source"], "catalogUrl": CATALOG_URL, "archiveUrl": BASE + ARCHIVE.name,
                "archiveBytes": ARCHIVE.stat().st_size, "archiveSha256": sha256, "expectedSha256": metadata["sha256"],
                "archiveChecksumVerified": True, "zimChecksumVerified": True, "archiveMetadata": metadata_keys,
                "archiveEntries": total, "archiveArticleCount": zim.article_count, "archiveMediaCount": zim.media_count,
                "canonicalHtmlPages": html_count, "redirectEntries": redirects, "excludedCanonicalHtmlByPathPrefix": dict(exclusions),
                "indexedItems": len(rows), "categoryCounts": dict(counts), "classificationBasisCounts": dict(classification_basis),
                "mimeCounts": dict(mime_counts), "uniqueMediaFiles": image_count, "mediaBytes": media_bytes,
                "guideJsonBytes": total_json, "indexBytes": len(index_payload), "indexGzipBytes": len(index_gzip),
                "classification": "Primary: source Device category relationships and ancestor roots. Fallback: source titles and available category names; unresolved reference pages remain Other.",
                "transformations": "Useful full guide/topic/article content retained. Navigation, comments, user profiles, executable markup and offline error pages excluded; local links/images rewritten; images content-addressed.",
                "licenseNotice": "Source content and adapted guide HTML are CC BY-NC-SA 3.0: attribution, noncommercial and share-alike. Not for AI/model training.",
                "runtimeComplete": True}
    atomic_write(DATA / "manifest.json", json_bytes(manifest))
    atomic_write(RUNTIME / "manifest.json", json_bytes(manifest))
    log(f"COMPLETE: {len(rows):,} indexed documents, {image_count:,} media files; index gzip {len(index_gzip):,} bytes")
    print(json.dumps({key: manifest[key] for key in ("revision", "indexedItems", "categoryCounts", "uniqueMediaFiles", "mediaBytes", "indexBytes", "indexGzipBytes")}, indent=2))


def assert_output_untracked():
    """Stop before download or build if Git could pick up the catalog database."""
    data = DATA.resolve()
    if ROOT.resolve() not in data.parents:
        raise SystemExit(f"Refusing to write catalog data outside the repo: {data}")
    probes = [
        "catalog-data/catalog.sqlite",
        "catalog-data/work.sqlite",
        "catalog-data/raw/ifixit_en_all_2025-12.zim",
        "catalog-data/runtime/index.json",
    ]
    result = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--", *probes], capture_output=True, text=True)
    ignored = {line.strip() for line in result.stdout.splitlines()}
    missing = [probe for probe in probes if probe not in ignored]
    if result.returncode not in {0, 1} or missing:
        raise SystemExit("Refusing to ingest: the catalog database, archive, or runtime files are not gitignored.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args()
    if args.download_only and args.build_only:
        parser.error("Choose one operation or omit both to download and build.")
    assert_output_untracked()
    metadata = source_metadata()
    if not args.build_only:
        download(metadata)
    sha256 = verify(metadata)
    if not args.download_only:
        build(metadata, sha256)


if __name__ == "__main__":
    main()
