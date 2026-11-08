#!/usr/bin/env -S uv run --script
# /// script
# requires-python = "==3.12.*"
# dependencies = ["libzim==4.1.0", "lxml==6.1.3"]
# ///
"""Stream a checksum-pinned English Appropedia ZIM without duplicating raw HTML."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import fcntl
import hashlib
import json
from pathlib import Path
import posixpath
import re
import sys
from urllib.parse import quote, unquote, urljoin, urlsplit

from libzim.reader import Archive
from lxml import etree, html

from ingest_common import (CatalogWriter, classify, completed, download, ingestion_key,
                           short_id, verify)
from ingest_wikimed import (ALLOWED_TAGS, DROP_TAGS, IMAGE_EXT, NAV_CLASSES,
                            article_name, first, page_config, text)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(__file__).with_name("appropedia-source.json")
ORIGIN = "https://www.appropedia.org/"
HOSTS = {"www.appropedia.org", "appropedia.org", "offline.invalid"}


def configuration():
    return json.loads(CONFIG.read_bytes())


def key_for(config):
    # Shared MediaWiki sanitizing constants are build inputs too.
    from ingest_common import digest_file
    config = dict(config, mediaWikiHelpers=digest_file(Path(__file__).with_name("ingest_wikimed.py")))
    return ingestion_key(__file__, config)


def source_url(path, legacy=False):
    if legacy and path.startswith("A/"):
        path = path[2:]
    return ORIGIN + quote(path.replace(" ", "_"), safe="/():,~!$&'*=;@+-_")


def references(base, reference, legacy=False):
    if not reference or reference.startswith("#"):
        return []
    if re.match(r"^(?:File|Image|Category|Appropedia):", reference, re.I):
        reference = "./" + reference
    try:
        parsed = urlsplit(urljoin("https://offline.invalid/" + quote(base, safe="/"), reference))
    except ValueError:
        return []
    if parsed.scheme not in {"https", "http"} or parsed.hostname not in HOSTS:
        return []
    path = posixpath.normpath(unquote(parsed.path)).lstrip("/")
    if parsed.hostname != "offline.invalid" and path.startswith("wiki/"):
        path = path[5:]
    if parsed.hostname != "offline.invalid" and path.startswith("w/"):
        return []
    result = [path]
    if legacy:
        result.append(path[2:] if path.startswith("A/") else "A/" + path)
    return result


def external_url(base, reference, legacy=False):
    try:
        parsed = urlsplit(reference)
        if parsed.scheme and parsed.scheme.lower() not in {"http", "https"}:
            # A MediaWiki namespace is a title, never an executable URL scheme.
            if not re.match(r"^(?:File|Image|Category|Appropedia):", reference, re.I):
                return ""
            return source_url(unquote(reference), legacy)
        if parsed.scheme or parsed.netloc:
            absolute = urljoin(ORIGIN, reference)
            value = urlsplit(absolute)
            return absolute if value.scheme in {"http", "https"} and value.hostname and not value.username and not value.password else ""
        candidates = references(base, reference, legacy)
        return source_url(candidates[0], legacy) if candidates else ""
    except ValueError:
        return ""


def sanitize(content, base, identifier, articles, media, legacy=False):
    wrapper = html.Element("div")
    wrapper.append(html.fromstring(etree.tostring(content), parser=html.HTMLParser(encoding="utf-8", recover=True)))
    images, media_sources, related = [], [], {}
    fragment = lambda value: "ap-" + identifier + "-" + hashlib.sha256(value.encode()).hexdigest()[:16]
    for node in list(wrapper.iter()):
        if not isinstance(node.tag, str):
            if node.getparent() is not None:
                node.drop_tree()
            continue
        tag = node.tag.lower()
        if tag in DROP_TAGS or set(node.get("class", "").split()) & NAV_CLASSES:
            if node.getparent() is not None:
                node.drop_tree()
            continue
        if tag not in ALLOWED_TAGS:
            if node.getparent() is not None:
                node.drop_tag()
            continue
        attrs = {}
        if value := node.get("id") or (node.get("name") if tag == "a" else None):
            attrs["id"] = fragment(value)
        if tag == "a":
            reference = node.get("href", "")
            if reference.startswith("#"):
                attrs["href"] = "#" + fragment(unquote(reference[1:]))
            else:
                target = next((articles[path] for path in references(base, reference, legacy) if path in articles), None)
                if target:
                    attrs["href"] = "/?guide=" + target
                    if tail := urlsplit(reference).fragment:
                        attrs["href"] += "#ap-" + target + "-" + hashlib.sha256(unquote(tail).encode()).hexdigest()[:16]
                    if target != identifier:
                        related.setdefault(target, text(node, 180))
                elif absolute := external_url(base, reference, legacy):
                    attrs.update(href=absolute, rel="noopener noreferrer")
        elif tag == "img":
            reference = node.get("src") or node.get("data-src") or ""
            path = next((path for path in references(base, reference, legacy) if path in media), None)
            if path is None:
                if node.getparent() is not None:
                    node.drop_tree()
                continue
            local = media[path]
            attrs.update(src=local, alt=node.get("alt", "")[:500], loading="lazy", decoding="async")
            images.append(local)
            parent = node.getparent()
            file_page = parent.get("href", "") if parent is not None and parent.tag == "a" else ""
            media_sources.append({"url": local, "sourcePath": path,
                                  "filePageUrl": external_url(base, file_page, legacy) if file_page else ""})
            for dimension in ("width", "height"):
                value = node.get(dimension, "")
                if value.isdigit() and 0 < int(value) <= 10000:
                    attrs[dimension] = value
        elif tag in {"td", "th"}:
            for name in ("colspan", "rowspan"):
                value = node.get(name, "")
                if value.isdigit() and 0 < int(value) <= 100:
                    attrs[name] = value
        node.attrib.clear()
        node.attrib.update(attrs)
    return etree.tostring(wrapper, encoding="unicode", method="html"), list(dict.fromkeys(images)), media_sources, related


def page_license(tree, config):
    # Appropedia still contains explicitly CC-BY-SA-3.0 and other licensed pages.
    for anchor in tree.xpath("//a[@href]"):
        href = anchor.get("href", "")
        if "creativecommons.org/licenses/" in href:
            match = re.search(r"creativecommons\.org/licenses/(by(?:-nc)?(?:-sa)?)/(\d\.\d)/?", href)
            if match:
                return {"name": "CC " + match[1].upper() + " " + match[2],
                        "url": "https://creativecommons.org/licenses/" + match[1] + "/" + match[2] + "/"}
    return config["license"]


def build(data_dir, archive, config=None):
    config = configuration() if config is None else config
    verify(archive, config)
    zim = Archive(archive)
    if not zim.check():
        raise RuntimeError("Appropedia ZIM internal checksum failed")
    legacy = not zim.has_new_namespace_scheme
    articles, aliases, redirects, titles = {}, defaultdict(set), {}, {}
    exclusions = Counter()
    with CatalogWriter(data_dir, config, key_for(config)) as writer:
        media = {}
        for entry in zim.iter_by_path():
            path, item = entry.path, entry.get_item()
            if entry.is_redirect:
                redirects[path] = item.path
                aliases[item.path].add(entry.title or path)
                writer.db.execute("INSERT INTO redirects VALUES(?,?,?)", (path, entry.title, item.path))
                continue
            mime = item.mimetype
            if mime.startswith("text/html"):
                name = article_name(path, legacy)
                if name and name.split(":", 1)[0].lower() not in {"appropedia", "translations"}:
                    prefix = bytes(item.content)[:8192]
                    lang = re.search(br'<html\b[^>]*\blang=[\"\x27]([^\"\x27]+)', prefix, re.I)
                    if lang and not lang[1].lower().startswith(b'en'):
                        exclusions["explicit-non-English"] += 1
                        continue
                    articles[path] = short_id(config["id"], path)
                    titles[path] = entry.title
                else:
                    exclusions["administrative/navigation"] += 1
            elif mime in IMAGE_EXT and mime != "image/svg+xml":
                # SVG can contain scripts/external URLs. Preserve in raw ZIM;
                # serve only safe raster images, as in the existing PDF pipeline.
                media[path] = writer.add_media(path, bytes(item.content), mime, IMAGE_EXT[mime])
        for path, target in redirects.items():
            if target in articles:
                articles[path] = articles[target]
            if target in media:
                media[path] = media[target]
        writer.db.commit()
        for number, (path, archive_title) in enumerate(titles.items(), 1):
            payload = bytes(zim.get_entry_by_path(path).get_item().content)
            tree = html.fromstring(payload, parser=html.HTMLParser(encoding="utf-8", recover=True))
            config_vars = page_config(tree)
            title = text(first(tree, "//h1")) or archive_title or article_name(path, legacy).replace("_", " ")
            content = first(tree, "//*[@id='mw-content-text']/*[contains(concat(' ',normalize-space(@class),' '),' mw-parser-output ')]")
            if content is None:
                content = first(tree, "//*[@id='mw-content-text']")
            if content is None:
                content = first(tree, "//body")
            if content is None:
                content = tree
            categories = config_vars.get("wgCategories", [])
            categories = ["Category:" + name.replace(" ", "_") for name in categories if isinstance(name, str)] if isinstance(categories, list) else []
            if not categories:
                categories = sorted({target for anchor in tree.xpath("//a[@href]")
                                     for target in references(path, anchor.get("href"), legacy)
                                     if target.removeprefix("A/").startswith("Category:")})
            identifier = articles[path]
            body, images, image_sources, related = sanitize(content, path, identifier, articles, media, legacy)
            summary = next((text(node, 500) for node in html.fromstring(body).xpath(".//p") if text(node)), "")
            source = source_url(path, legacy)
            record = {"id": identifier, "title": title, "category": classify(title, categories), "kind": "article", "device": "",
                      "sourceId": config["id"], "sourceName": "Appropedia", "sourceUrl": source,
                      "sourceHistoryUrl": source + "?action=history", "sourceRevision": config["revision"],
                      "sourceRevisionId": config_vars.get("wgCurRevisionId"), "sourceCategories": sorted(set(categories)),
                      "license": page_license(tree, config), "licenseNotice": config["licenseNotice"],
                      "attribution": config["attribution"], "summary": summary, "html": body,
                      "images": images, "mediaSources": image_sources,
                      "related": [{"id": target, "title": label} for target, label in list(related.items())[:40]]}
            writer.add(record, path, aliases.get(path, ()))
            if number % 1000 == 0:
                print(f"Appropedia: {number:,} source articles streamed", file=sys.stderr, flush=True)
        metadata = {key: bytes(zim.get_metadata(key)).decode(errors="replace") for key in zim.metadata_keys if not key.startswith("Illustration_")}
        return writer.finish({"excludedPages": dict(exclusions), "archiveMetadata": metadata,
                              "archiveArticleCount": zim.article_count, "archiveMediaCount": zim.media_count,
                              "licenseNotice": config["licenseNotice"]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "catalog-data/build/appropedia")
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument("--download-only", action="store_true")
    operations.add_argument("--build-only", action="store_true")
    args = parser.parse_args()
    config = configuration()
    data = args.data_dir.resolve()
    data.mkdir(parents=True, exist_ok=True)
    with (data / ".ingest.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not args.download_only and completed(data, key_for(config), config):
            print("Reusing completed Appropedia source", file=sys.stderr)
            return
        archive = download(data, config, args.build_only)
        if not args.download_only:
            result = build(data, archive, config)
            print(json.dumps({key: result[key] for key in ("indexedItems", "uniqueMediaFiles")}))


if __name__ == "__main__":
    main()
