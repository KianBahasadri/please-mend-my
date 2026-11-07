#!/usr/bin/env python3
"""Prepare shared search aliases from pinned Wikidata entities and iFixit ancestry.

No article/media rewriting and no runtime network dependency. Source responses
stay in the ignored raw cache; their revisions and checksums accompany the index.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
VOCABULARY = ROOT / "scripts/search-vocabulary.json"
USER_AGENT = "PleaseMendMyIngest/1.0 (+https://github.com/KianBahasadri/please-mend-my)"


def clean(value):
    return "".join(char for char in unicodedata.normalize("NFKD", value)
                   if not unicodedata.category(char).startswith("M")).lower()


def atomic_write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(body)
    temporary.replace(path)


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def wikidata_entity(concept, cache):
    qid, revision = concept["id"], concept["revision"]
    if not re.fullmatch(r"Q[1-9][0-9]*", qid) or not isinstance(revision, int) or revision <= 0:
        raise ValueError("Wikidata inputs must pin an item ID and revision.")
    url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json?revision={revision}"
    path = Path(cache) / f"{qid}-{revision}.json"
    if path.exists():
        body = path.read_bytes()
    else:
        for attempt in range(3):
            try:
                request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                                "Accept-Encoding": "gzip"})
                with urlopen(request, timeout=30) as response:
                    body = response.read(8 * 1024 * 1024 + 1)
                    if len(body) > 8 * 1024 * 1024:
                        raise ValueError("Wikidata item exceeded the bounded response size.")
                    if response.headers.get("Content-Encoding") == "gzip":
                        body = gzip.decompress(body)
                time.sleep(0.55)  # Keep uncached public-source requests below two per second.
                break
            except HTTPError as error:
                if error.code not in {429, 500, 502, 503, 504} or attempt == 2:
                    raise
                retry = error.headers.get("Retry-After", "")
                delay = int(retry) if retry.isdigit() else 2 ** (attempt + 1)
                if delay > 60:
                    raise RuntimeError("Wikidata requested a longer pause; retry ingestion later.") from error
                time.sleep(max(1, delay))
            except (URLError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(2 ** (attempt + 1))
    entity = json.loads(body)["entities"][qid]
    label = entity.get("labels", {}).get("en", {})
    if (entity.get("id") != qid or entity.get("lastrevid") != revision or
            label.get("language") != "en" or clean(label.get("value", "")) != clean(concept["label"])):
        raise ValueError(f"Wikidata {qid} does not match its pinned English identity/revision.")
    if not path.exists():
        atomic_write(path, body)
    aliases = [item["value"] for item in entity.get("aliases", {}).get("en", [])
               if item.get("language") == "en"]
    excluded = {clean(item) for item in concept.get("excludeAliases", [])}
    terms = sorted({clean(value).strip() for value in [label["value"], *aliases, *concept.get("curated", [])]
                    if clean(value).strip() not in excluded and any(char.isalpha() for char in value)})
    return terms, {"id": qid, "revision": revision, "url": url, "label": label["value"],
                   "aliases": aliases, "curated": concept.get("curated", []),
                   "ifixitPaths": concept["ifixitPaths"], "sha256": hashlib.sha256(body).hexdigest(),
                   "license": "CC0-1.0"}


def prepare_search(index, inventory, vocabulary, cache, fetch_entity=wikidata_entity):
    if vocabulary.get("version") != 1 or vocabulary.get("language") != "en":
        raise ValueError("Search vocabulary must be version 1 English metadata.")
    connection = sqlite3.connect(f"file:{Path(inventory).resolve()}?mode=ro", uri=True)
    try:
        items = {identifier: (path, title, kind) for identifier, path, title, kind in
                 connection.execute("SELECT id,source_path,title,kind FROM items")}
        titles = {path: title for path, title, kind in items.values() if kind == "topic"}
        parents, memberships = defaultdict(set), defaultdict(set)
        for parent, child in connection.execute("SELECT parent,child FROM category_edges"):
            parents[child].add(parent)
        for guide, category in connection.execute("SELECT guide,category FROM guide_categories"):
            memberships[guide].add(category)
    finally:
        connection.close()
    anchor_terms, sources = defaultdict(set), []
    for concept in vocabulary["concepts"]:
        terms, source = fetch_entity(concept, cache)
        sources.append(source)
        for path in concept["ifixitPaths"]:
            anchor_terms[path].update(terms)
    ancestors = {}
    def ancestry(start):
        if start not in ancestors:
            seen, queue = set(), deque([start])
            while queue:
                path = queue.popleft()
                if path in seen:
                    continue
                seen.add(path)
                queue.extend(sorted(parents.get(path, ())))
            ancestors[start] = frozenset(seen)
        return ancestors[start]
    groups, group_ids, references, enriched = [""], {"": 0}, [], 0
    for row in index["items"]:
        if row[0] not in items:
            raise ValueError("Search item has no source inventory record: " + row[0])
        path, title, kind = items[row[0]]
        starts = [path] if kind == "topic" else sorted(memberships.get(path, ()))
        nodes = set().union(*(ancestry(start) for start in starts)) if starts else set()
        # Own titles/devices are already indexed. Share only additional names.
        own = {clean(row[1]), clean(row[4])}
        terms = {clean(titles[node]) for node in nodes if node in titles and clean(titles[node]) not in own}
        for node in nodes:
            terms.update(anchor_terms.get(node, ()))
        terms.difference_update(own)
        text = "\n".join(sorted(term for term in terms if term))
        if text not in group_ids:
            group_ids[text] = len(groups)
            groups.append(text)
        references.append(group_ids[text])
        enriched += bool(text)
    return {"version": 1, "normalization": "NFKD-no-marks-lower", "groups": groups,
            "itemGroups": references, "enrichedItems": enriched,
            "sources": sources, "ancestrySource": index["source"],
            "note": "English Wikidata aliases and curated synonyms follow explicit iFixit anchors and source ancestry. No guessed name matches."}


def enrich(data_dir, runtime=None, vocabulary=VOCABULARY):
    data_dir = Path(data_dir).resolve()
    runtime = Path(runtime).resolve() if runtime else data_dir / "runtime"
    index_path, manifest_path = runtime / "index.json", runtime / "manifest.json"
    index = json.loads(index_path.read_bytes())
    manifest = json.loads(manifest_path.read_bytes())
    if not manifest.get("runtimeComplete") or manifest["revision"] != index["revision"]:
        raise ValueError("Alias enrichment requires a completed unpublished catalog.")
    config = json.loads(Path(vocabulary).read_bytes())
    search = prepare_search(index, data_dir / "catalog.sqlite", config, data_dir / "raw/wikidata")
    index["search"] = search
    body = json_bytes(index)
    packed = gzip.compress(body, compresslevel=9, mtime=0)
    # Completion stays false until both representations and metadata are ready.
    manifest["runtimeComplete"] = False
    atomic_write(manifest_path, json_bytes(manifest))
    atomic_write(index_path, body)
    atomic_write(runtime / "index.json.gz", packed)
    manifest.update({"runtimeComplete": True, "indexBytes": len(body), "indexGzipBytes": len(packed),
                     "searchEnrichment": {"version": 1, "enrichedItems": search["enrichedItems"],
                                          "sharedGroups": len(search["groups"]), "wikidataItems": len(search["sources"]),
                                          "vocabularySha256": hashlib.sha256(Path(vocabulary).read_bytes()).hexdigest(),
                                          "sources": search["sources"]}})
    atomic_write(manifest_path, json_bytes(manifest))
    atomic_write(data_dir / "manifest.json", json_bytes(manifest))
    print(json.dumps({"enrichedItems": search["enrichedItems"], "sharedGroups": len(search["groups"]),
                      "wikidataItems": len(search["sources"]), "indexBytes": len(body), "indexGzipBytes": len(packed)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "catalog-data")
    parser.add_argument("--runtime-dir", type=Path, help="Unpublished runtime directory; never enrich a live snapshot in place.")
    args = parser.parse_args()
    enrich(args.data_dir, args.runtime_dir)
