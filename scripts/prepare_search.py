#!/usr/bin/env python3
"""Prepare compact browser-resident token postings and Porter stem expansions.

Run after alias enrichment on an unpublished runtime. Only index/manifest files
change; repair articles, media, provenance, and source licenses remain untouched.
"""
from __future__ import annotations

import argparse
import base64
from collections.abc import Mapping
from collections import defaultdict, deque
import gzip
import hashlib
import heapq
import json
from pathlib import Path
import re
import sqlite3
import stat
import unicodedata

from ingest_common import clean, json_bytes, normalize_words, porter, tokens

ROOT = Path(__file__).resolve().parent.parent
NOUN_SOURCE = Path(__file__).with_name("nouns-source.json")


def lexicographic(value):
    # Browser strings compare UTF-16 code units rather than Unicode code points.
    return value.encode("utf-16-be", errors="surrogatepass")


def pack_postings(lists):
    """Each sorted distinct list resets delta to zero; offsets address bytes."""
    packed, offsets = bytearray(), [0]
    for values in lists:
        previous = 0
        for value in values:
            delta = value - previous
            if delta < 0 or value >= 2**32:
                raise ValueError("Posting lists must contain increasing uint32 identifiers.")
            while delta >= 128:
                packed.append((delta & 127) | 128)
                delta >>= 7
            packed.append(delta)
            previous = value
        offsets.append(len(packed))
    return offsets, base64.b64encode(packed).decode("ascii")


def guide_terms(guide_records):
    """Validate optional v2 inputs and keep their terms document-specific.

    This is build-time data, not extra browser rows or shared alias groups.
    Absent fields retain the same empty-list meaning as legacy records.
    """
    if guide_records is None:
        return {}, {}
    if isinstance(guide_records, Mapping):
        entries = guide_records.items()
    else:
        try:
            entries = ((record.get("id") if isinstance(record, dict) else None, record)
                       for record in iter(guide_records))
        except TypeError as error:
            raise ValueError("Structured guide records must be a mapping or iterable.") from error
    result, literals = {}, {}
    for identifier, record in entries:
        if (not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identifier)
                or not isinstance(record, dict) or record.get("id") != identifier
                or type(record.get("version")) is not int or record["version"] != 2
                or identifier in result):
            raise ValueError("Structured guide records need unique matching v2 identities.")
        own, literal_values = set(), []
        for field in ("symptoms", "materials"):
            values = record.get(field, [])
            if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                raise ValueError("Structured guide " + field + " must be an array of strings.")
            for value in values:
                own.update(tokens(value))
                normalized = clean(value)
                if any(unicodedata.category(char).startswith("N") for char in normalized):
                    literal_values.append(normalized)
        result[identifier] = own
        if literal_values:
            literals[identifier] = " ".join(literal_values)
    return result, literals


def noun_terms(index, database):
    """Expand aliases only on guides explicitly linked to their catalog noun.

    A part alias requires direct title/device evidence for that part. Model and
    numeric aliases never enter semantic expansion; their literal gate stays
    separate from the word index.
    """
    if database is None:
        return {}
    registry = index.get("archetypes")
    if not isinstance(registry, dict) or registry.get("version") != 1:
        raise ValueError("Noun search enrichment requires the prepared archetype registry")
    definitions = {}
    for row in registry["archetypes"]:
        alias_terms = set()
        for alias in row["aliases"]:
            if not any(char.isdigit() for char in alias):
                alias_terms.update(tokens(alias))
        parts = []
        for part in row["parts"]:
            query = tokens(part["query"])
            aliases = set()
            for alias in part["aliases"]:
                if not any(char.isdigit() for char in alias):
                    aliases.update(tokens(alias))
            if query:
                parts.append(({porter(term) for term in query}, aliases))
        definitions[row["id"]] = alias_terms, parts
    known = {row[0] for row in index["items"]}
    expanded = defaultdict(set)
    connection = sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        # Keep only one document's direct-token roots at a time. Creating token
        # and stem sets for all 207k rows duplicates hundreds of MiB before the
        # inverted postings have even been built.
        current, roots = None, set()
        rows = connection.execute(
            "SELECT g.guide_id,g.noun_id,i.title,i.device FROM guide_nouns AS g "
            "JOIN items AS i ON i.id=g.guide_id WHERE i.kind IN ('guide','article') "
            "ORDER BY g.guide_id,g.noun_id")
        for guide_id, noun_id, title, device in rows:
            if guide_id not in known:
                raise ValueError("Noun link refers to a guide absent from the catalog")
            definition = definitions.get(noun_id)
            if definition is None:
                continue
            if guide_id != current:
                roots = {porter(term) for term in tokens(title) + tokens(device)}
                current = guide_id
            alias_terms, parts = definition
            expanded[guide_id].update(alias_terms)
            for query, aliases in parts:
                if query <= roots:
                    expanded[guide_id].update(aliases)
    finally:
        connection.close()
    return expanded


def prepare_hierarchy(index, database, config_path=NOUN_SOURCE):
    """Compile lexical ancestry and primary guide membership, never article bodies.

    Structural nodes retain unfilled descendants such as breeds and kinship roles
    so they can inherit a guide-backed ancestor without masquerading as filled UI
    archetypes. Primary links exclude incidental title-component associations.
    Numeric model names stay in the existing registry, outside lexical aliases.
    """
    if database is None:
        return None
    registry = index.get("archetypes")
    if not isinstance(registry, dict) or registry.get("version") != 1:
        raise ValueError("Hierarchy preparation requires the prepared archetype registry")
    categories = {row["id"] for row in index.get("categories", [])}
    configuration = json.loads(Path(config_path).read_bytes())
    category_domains = configuration["categoryDomains"]
    # The catch-all category has no useful semantic fence. Never export it as a
    # universal domain fallback (human choking must not inherit pet recipes).
    catch_all = set(category_domains.get("other", []))
    domain_categories = defaultdict(list)
    for category, domains in sorted(category_domains.items()):
        if category not in categories or category == "other" or catch_all and set(domains) >= catch_all:
            continue
        for domain in sorted(set(domains)):
            domain_categories[domain].append(category)
    docs = {row[0]: position for position, row in enumerate(index["items"])}
    if len(docs) != len(index["items"]):
        raise ValueError("Hierarchy catalog IDs must be unique")
    connection = sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        nodes = [[identifier, label, domain.removeprefix("noun."), frequency, count]
                 for identifier, label, domain, frequency, count in connection.execute(
                     "SELECT id,label,domain,frequency,guide_count FROM nouns ORDER BY id")]
        positions = {row[0]: position for position, row in enumerate(nodes)}
        parents, children = [set() for _ in nodes], [[] for _ in nodes]
        for parent, child in connection.execute(
                "SELECT parent_id,child_id FROM noun_edges WHERE relation='is-a' ORDER BY child_id,parent_id"):
            if parent not in positions or child not in positions:
                raise ValueError("Hierarchy relationship refers to an unknown noun")
            parent, child = positions[parent], positions[child]
            if parent == child:
                raise ValueError("Hierarchy cannot contain a self parent")
            parents[child].add(parent)
        parents = [sorted(values) for values in parents]
        for child, values in enumerate(parents):
            for parent in values:
                children[parent].append(child)
        # Reject a corrupted cycle before publishing, rather than asking every
        # browser traversal to discover a malformed ontology repeatedly.
        remaining = [len(values) for values in parents]
        pending = deque(position for position, degree in enumerate(remaining) if degree == 0)
        visited = 0
        while pending:
            visited += 1
            for child in children[pending.popleft()]:
                remaining[child] -= 1
                if remaining[child] == 0:
                    pending.append(child)
        if visited != len(nodes):
            raise ValueError("Hierarchy is-a relationships contain a cycle")
        # Filled aliases are already present in the resident archetype payload;
        # only structural/unfilled aliases need an additional lexical table.
        filled = {row["id"] for row in registry["archetypes"]}
        extra_aliases = {}
        for identifier, raw_alias, rank in connection.execute(
                "SELECT noun_id,alias,rank FROM noun_aliases ORDER BY noun_id,alias"):
            if identifier not in positions:
                raise ValueError("Hierarchy alias refers to an unknown noun")
            if identifier in filled:
                continue
            alias = normalize_words(raw_alias)
            if not 2 <= len(alias) <= 160 or any(char.isdigit() for char in alias):
                continue
            key = alias, positions[identifier]
            extra_aliases[key] = min(rank, extra_aliases.get(key, rank))
        aliases = [[alias, node, rank] for (alias, node), rank in sorted(
            extra_aliases.items(), key=lambda item: (lexicographic(item[0][0]), item[0][1]))]
        documents, observed_categories = [set() for _ in nodes], [set() for _ in nodes]
        for guide, noun in connection.execute(
                "SELECT g.guide_id,g.noun_id FROM guide_nouns g JOIN items i ON i.id=g.guide_id "
                "WHERE i.kind IN ('guide','article') AND g.reason!='title-component' "
                "ORDER BY g.noun_id,g.guide_id"):
            if guide not in docs or noun not in positions:
                raise ValueError("Hierarchy guide link refers to an unknown catalog ID or noun")
            doc, node = docs[guide], positions[noun]
            row = index["items"][doc]
            if row[3] not in {"guide", "article"}:
                raise ValueError("Hierarchy actionable link disagrees with catalog kind")
            documents[node].add(doc)
            observed_categories[node].add(row[2])
    finally:
        connection.close()
    offsets, data = pack_postings(sorted(values) for values in documents)
    return {"version": 1, "fields": ["id", "label", "domain", "frequency", "guideCount"],
            "nodes": nodes, "parents": parents, "aliases": aliases,
            "categories": [sorted(values) for values in observed_categories],
            "domainCategories": dict(sorted(domain_categories.items())),
            "documents": {"encoding": "delta-varint-base64", "offsets": offsets, "data": data}}


def prepare_short_prefixes(items, groups, references, postings):
    """Exact top-100 single-letter rankings, computed offline from real postings.

    Letters retain the worker's original direct/alias quality and tie ordering;
    numeric queries keep the runtime model-literal gate. No candidate truncation.
    """
    count = len(items)
    group_docs = [[] for _ in groups]
    for doc, group in enumerate(references):
        group_docs[group].append(doc)
    by_letter = defaultdict(list)
    for term in postings:
        if term[0].isalpha() and ord(term[0]) <= 0xffff:
            by_letter[term[0]].append(term)
    titles = [clean(row[1]) for row in items]
    queries = []
    for letter in sorted(by_letter, key=lexicographic):
        quality, group_quality = {}, {}
        for term in by_letter[letter]:
            cost = 1 if term == letter else 2
            for value in postings[term]:
                if value < count:
                    quality[value] = min(quality.get(value, cost), cost)
                else:
                    group = value - count
                    group_quality[group] = min(group_quality.get(group, cost + 16), cost + 16)
        for group, cost in group_quality.items():
            for doc in group_docs[group]:
                quality[doc] = min(quality.get(doc, cost), cost)
        best = []
        for doc, cost in quality.items():
            row, title = items[doc], titles[doc]
            if row[3] not in {'guide', 'article'}:
                continue
            score = 15 if row[3] == 'guide' else 0
            if cost <= 2:
                position = title.find(letter)
                if position == 0:
                    score += (1000 if len(title) == 1 else 500) + 35
                elif position > 0:
                    score += 250 + (35 if ' ' + letter in title else 10)
            rank = (score - (cost - 1) * 100, -doc)
            if len(best) < 100:
                heapq.heappush(best, rank)
            elif rank > best[0]:
                heapq.heapreplace(best, rank)
        queries.append([letter, [-doc for _, doc in sorted(best, reverse=True)]])
    return {'version': 1, 'limit': 100, 'queries': queries}


def prepare_index(index, guide_records=None, noun_records=None):
    """Return deterministic postings, optionally including v2 symptoms/materials."""
    items, categories, search = index.get("items"), index.get("categories"), index.get("search")
    if not isinstance(items, list) or not isinstance(categories, list) or not isinstance(search, dict):
        raise ValueError("Prepared search requires a catalog and completed alias enrichment.")
    groups, references = search.get("groups"), search.get("itemGroups")
    if (search.get("version") != 1 or not isinstance(groups, list) or not groups or groups[0] != "" or
            not all(isinstance(group, str) for group in groups) or not isinstance(references, list) or
            len(references) != len(items) or not all(type(group) is int and 0 <= group < len(groups)
                                                   for group in references)):
        raise ValueError("The prepared search alias format is invalid.")
    names = {category["id"]: category["name"] for category in categories}
    postings = defaultdict(set)
    count = len(items)
    if count + len(groups) >= 2**32:
        raise ValueError("Catalog exceeds uint32 posting capacity.")
    extra, literals = guide_terms(guide_records)
    unseen = set(extra)
    guide_literals, structured_terms = [], []
    for identifier, row in enumerate(items):
        if not isinstance(row, list) or len(row) < 5:
            raise ValueError("Search rows must retain the five-field catalog shape.")
        own = set(tokens(row[1])) | set(tokens(row[4])) | set(tokens(names.get(row[2], "")))
        if noun_records is not None:
            own.update(noun_records.get(row[0], ()))
        if extra and row[0] in extra:
            if row[0] not in unseen:
                raise ValueError("Structured guide ID occurs more than once in the catalog.")
            unseen.remove(row[0])
            own.update(extra[row[0]])
            if extra[row[0]]:
                # Preserve the provenance of real structured symptoms/materials.
                # Inheritance must not mistake noun aliases added to the same
                # postings for an actually requested symptom or component.
                structured_terms.append([identifier, sorted(extra[row[0]], key=lexicographic)])
            if row[0] in literals:
                guide_literals.append([identifier, literals[row[0]]])
        for term in own:
            postings[term].add(identifier)
    if unseen:
        raise ValueError("Structured guides must refer to catalog item IDs.")
    for group, text in enumerate(groups[1:], 1):
        for term in set(tokens(text)):
            postings[term].add(count + group)
    terms = sorted(postings, key=lexicographic)
    offsets, body = pack_postings(sorted(postings[term]) for term in terms)
    roots = defaultdict(list)
    for identifier, term in enumerate(terms):
        root = porter(term)
        # The worker also checks the root directly in the surface lexicon.
        # Identity entries add no information and otherwise nearly duplicate it.
        if root != term:
            roots[root].append(identifier)
    stem_terms = sorted(roots, key=lexicographic)
    stem_offsets, stem_body = pack_postings(roots[root] for root in stem_terms)
    result = {"version": 1, "normalization": "NFKD-no-marks-lower", "tokenizer": "unicode-LN",
              "stemmer": "porter1980", "documents": count, "groups": len(groups), "terms": terms,
              "offsets": offsets, "postings": body, "stemTerms": stem_terms,
              "stemOffsets": stem_offsets, "stemPostings": stem_body}
    result['shortPrefixes'] = prepare_short_prefixes(items, groups, references, postings)
    if guide_literals:
        # Numeric/model queries retain the worker's strict literal gate. Only
        # these few source fields need text beside the compact token postings.
        result["guideLiterals"] = guide_literals
    if structured_terms:
        result["structuredTerms"] = structured_terms
    return result


def atomic_write(path, body):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(body)
    temporary.replace(path)


def load_guide_records(runtime, index, manifest):
    """Read only the v2 IDs recorded by validation, never enumerate raw guides."""
    structured = manifest.get("structuredGuides", {})
    if not isinstance(structured, dict):
        raise ValueError("Structured guide completion metadata is invalid.")
    count = structured.get("processedItems", 0)
    identifiers = structured.get("itemIds", [])
    if (type(count) is not int or count < 0 or not isinstance(identifiers, list)
            or not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{20}", value)
                       for value in identifiers) or len(set(identifiers)) != len(identifiers)):
        raise ValueError("Structured guide IDs or completed count are invalid.")
    if len(identifiers) != count:
        raise ValueError("Structured guides need their complete itemIds; rebuild the validated overlay.")
    if not count:
        return {}
    known = {row[0] for row in index.get("items", []) if isinstance(row, list) and row}
    if not set(identifiers) <= known:
        raise ValueError("Structured guide IDs must exist in the catalog.")
    guides = runtime / "guides"
    if not stat.S_ISDIR(guides.lstat().st_mode):
        raise ValueError("Structured guide directory must not be a symlink.")
    records = {}
    for identifier in sorted(identifiers):
        path = guides / (identifier + ".json")
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("Structured guide input must be a regular file, not a symlink.")
        records[identifier] = json.loads(path.read_bytes())
    guide_terms(records)  # Validate before a caller can write completion metadata.
    return records


def prepare(data_dir, runtime=None, noun_database=None):
    """Prepare an unpublished completed runtime; never mutate a served release."""
    data_dir = Path(data_dir).resolve()
    runtime = Path(runtime).resolve() if runtime else data_dir / "runtime"
    index_path, manifest_path = runtime / "index.json", runtime / "manifest.json"
    if any(not stat.S_ISREG(path.lstat().st_mode) for path in (index_path, manifest_path)):
        raise ValueError("Search index and completion metadata must be regular files, not symlinks.")
    index, manifest = json.loads(index_path.read_bytes()), json.loads(manifest_path.read_bytes())
    if (not manifest.get("runtimeComplete") or manifest.get("revision") != index.get("revision") or
            manifest.get("searchEnrichment", {}).get("version") != 1):
        raise ValueError("Inverted search preparation requires a completed unpublished enriched catalog.")
    records = load_guide_records(runtime, index, manifest)
    semantic = noun_terms(index, noun_database)
    hierarchy = prepare_hierarchy(index, noun_database)
    prepared = prepare_index(index, records, semantic)
    index["search"]["inverted"] = prepared
    if hierarchy is not None:
        index["search"]["hierarchy"] = hierarchy
    else:
        index["search"].pop("hierarchy", None)
    body = json_bytes(index)
    packed = gzip.compress(body, compresslevel=9, mtime=0)
    manifest["runtimeComplete"] = False
    atomic_write(manifest_path, json_bytes(manifest))
    atomic_write(index_path, body)
    atomic_write(runtime / "index.json.gz", packed)
    metadata = {"version": 1, "tokenizer": prepared["tokenizer"], "stemmer": prepared["stemmer"],
                "terms": len(prepared["terms"]), "stems": len(prepared["stemTerms"]),
                "documents": prepared["documents"], "groups": prepared["groups"],
                "structuredDocuments": len(records), "guideFields": ["symptoms", "materials"],
                "nounDocuments": len(semantic),
                "hierarchyVersion": hierarchy["version"] if hierarchy else None,
                "hierarchyNodes": len(hierarchy["nodes"]) if hierarchy else 0,
                "hierarchyEdges": sum(map(len, hierarchy["parents"])) if hierarchy else 0,
                "hierarchyJsonBytes": len(json_bytes(hierarchy)) if hierarchy else 0,
                "indexSha256": hashlib.sha256(body).hexdigest()}
    manifest.update({"runtimeComplete": True, "indexBytes": len(body), "indexGzipBytes": len(packed),
                     "searchIndex": metadata})
    atomic_write(manifest_path, json_bytes(manifest))
    atomic_write(data_dir / "manifest.json", json_bytes(manifest))
    print(json.dumps({**metadata, "indexBytes": len(body), "indexGzipBytes": len(packed)}), flush=True)
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "catalog-data")
    parser.add_argument("--runtime-dir", type=Path, help="Unpublished runtime; never modify a live release.")
    parser.add_argument("--noun-database", type=Path, help="Build-only noun/model links from all merged sources.")
    args = parser.parse_args()
    prepare(args.data_dir, args.runtime_dir, args.noun_database)
