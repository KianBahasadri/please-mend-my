#!/usr/bin/env python3
"""Compile filled noun/model/part metadata and reviewed vector art into a release.

Only compact SQLite rows and index metadata are read. No guide JSON traversal or
network/AI calls occur; an archetype without reviewed geometry stays list-only.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from diagram_credits import render as render_credits
from ingest_common import atomic_write, json_bytes, porter, normalize_words as normalized
from diagram_svg import load_approved_art

ROOT = Path(__file__).resolve().parents[1]
APPROVED_ART = ROOT / "scripts/data/archetype-art.json"
NOUN_SOURCE = ROOT / "scripts/nouns-source.json"
PART_LIMIT = 12
# These are document actions/metadata rather than repairable assemblies. This
# filter is shared by every noun; it does not enumerate supported objects.
PART_STOPWORDS = set("a an the new old front rear left right upper lower part section item unit thing change repair replacement replacing disassembly reassembly guide tutorial fix fixing introduction troubleshooting maintenance removal removing procedure step tool instruction instructions information problem issue damage solution method service access opening assembly".split())
PART_ACTIONS = set("replacement repair removal installation adjustment cleaning disassembly reassembly change replace remove install adjust clean fix replacing removing installing repairing fixing".split())
PART_DEFINITION = re.compile(r"\b(?:part|component|assembly|section|piece|mechanism)\b.*\b(?:of|in|on|within)\b", re.I)


def database_connection(database):
    connection = sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def sorted_aliases(values, maximum=160):
    return sorted({normalized(value) for value in values if isinstance(value, str)
                   and 0 < len(normalized(value)) <= maximum})


def part_id(label):
    return normalized(label).replace(" ", "-")


def lexical_sources():
    config = json.loads(NOUN_SOURCE.read_bytes())
    wordnet, frequency = config["wordnet"], config["wordfreq"]
    return [{"id": "oewn", "name": wordnet["name"], "source": wordnet["project"],
             "download": wordnet["url"], "sha256": wordnet["sha256"],
             "license": wordnet["license"], "licenseUrl": wordnet["licenseUrl"],
             "notice": "https://github.com/KianBahasadri/please-mend-my/blob/main/" + wordnet["licenseNotice"]},
            {"id": "wordfreq", "name": "wordfreq", "version": frequency["version"],
             "source": frequency["project"], "license": frequency["dataLicense"],
             "licenseUrl": frequency["licenseUrl"],
             "notice": "https://github.com/KianBahasadri/please-mend-my/blob/main/" + frequency["notice"]}]


def physical_domain(noun):
    domain = noun["domain"].removeprefix("noun.")
    return domain if domain in {"artifact", "body"} else ""


def component_heads(title, device):
    """Extract a component head, not all physical nouns anywhere in a title."""
    words = normalized(title).split()
    if not words:
        return []
    # iFixit-style titles name the affected assembly immediately before the
    # operation. Instructions such as "fix a loose pedal" name it at the end.
    positions = [offset for offset, word in enumerate(words) if word in PART_ACTIONS]
    heads = []
    if positions and positions[-1] >= len(words) - 2:
        heads.append(words[:positions[-1]])
    if positions and positions[0] <= 4:
        clause = words[positions[0] + 1:]
        for offset, word in enumerate(clause):
            if word in {"on", "from", "for"}:
                clause = clause[:offset]
                break
        heads.append(clause)
    elif device and normalized(device) in normalized(title) and len(words) >= 3:
        heads.append(words)
    return [head[-4:] for head in heads if head]


def stem_phrase(value):
    return " ".join(porter(word) for word in normalized(value).split())


def compile_registry(database, revision, art_config=APPROVED_ART):
    """Return a resident registry and art bodies using only linked compact rows."""
    with closing(database_connection(database)) as connection:
        nouns = {row["id"]: dict(row) for row in connection.execute(
            "SELECT id,label,domain,definition,frequency,guide_count FROM nouns ORDER BY id")}
        aliases = defaultdict(list)
        ranks = defaultdict(dict)
        for row in connection.execute("SELECT noun_id,alias,rank FROM noun_aliases ORDER BY noun_id,alias"):
            aliases[row["noun_id"]].append(row["alias"])
            key = normalized(row["alias"])
            ranks[row["noun_id"]][key] = min(row["rank"], ranks[row["noun_id"]].get(key, row["rank"]))
        for identifier, noun in nouns.items():
            aliases[identifier] = sorted_aliases([noun["label"], *aliases[identifier]])
        filled = {identifier: noun for identifier, noun in nouns.items() if noun["guide_count"] > 0}
        meronyms, parents = defaultdict(set), defaultdict(set)
        component_roles = set()
        for row in connection.execute("SELECT parent_id,child_id,relation FROM noun_edges"):
            parent, child = row["parent_id"], row["child_id"]
            if row["relation"] == "has-part":
                meronyms[parent].add(child)
                component_roles.add(child)
            elif row["relation"] == "is-a":
                parents[child].add(parent)
        # Meronym roles and component definitions form one reusable lexical
        # assembly vocabulary. Clothing types, documents, object names, and
        # incidental nouns do not become components merely by recurring.
        physical = defaultdict(list)
        for identifier, noun in nouns.items():
            if not physical_domain(noun) or not (identifier in component_roles or PART_DEFINITION.search(noun["definition"])):
                continue
            for alias in aliases[identifier]:
                if (2 <= len(alias) <= 120 and len(alias.split()) <= 4
                        and not any(char.isdigit() for char in alias)
                        and alias not in PART_STOPWORDS):
                    physical[stem_phrase(alias)].append(identifier)
        for values in physical.values():
            values.sort(key=lambda identifier: (-nouns[identifier]["frequency"], identifier))
        related = defaultdict(set)
        for subject, noun in filled.items():
            domain = physical_domain(noun)
            if not domain:
                continue
            frontier, seen = {subject}, set()
            # A laptop inherits computer assemblies, trousers inherit garment
            # assemblies. Stay inside the physical domain rather than walking
            # up to abstract entity/group concepts.
            for _ in range(8):
                following = set()
                for ancestor in frontier - seen:
                    seen.add(ancestor)
                    related[subject].update(child for child in meronyms.get(ancestor, ())
                                            if child in nouns and physical_domain(nouns[child]) == domain)
                    following.update(parent for parent in parents.get(ancestor, ())
                                     if parent in nouns and physical_domain(nouns[parent]) == domain)
                frontier = following - seen
                if not frontier:
                    break
        counts = defaultdict(Counter)
        linked_rows = connection.execute(
            "SELECT g.noun_id,i.title,i.device FROM guide_nouns AS g "
            "JOIN items AS i ON i.id=g.guide_id WHERE i.kind IN ('guide','article') "
            "ORDER BY g.noun_id,i.id")
        for row in linked_rows:
            subject = row["noun_id"]
            if subject not in filled or not physical_domain(filled[subject]):
                continue
            candidates = set()
            for head in component_heads(row["title"], row["device"]):
                # Prefer the longest known suffix assembly; never choose an
                # arbitrary token earlier in the device/model/title string.
                for length in range(min(4, len(head)), 0, -1):
                    text = " ".join(head[-length:])
                    choices = [choice for choice in physical.get(stem_phrase(text), ())
                               if physical_domain(nouns[choice]) == physical_domain(filled[subject])]
                    if choices:
                        choice = next((value for value in choices if value in related[subject]), choices[0])
                        if choice != subject and text not in aliases[subject]:
                            candidates.add(choice)
                        break
            counts[subject].update(candidates)
        models = [{"name": row["name"], "nounId": row["noun_id"]} for row in connection.execute(
            "SELECT name,noun_id FROM models WHERE guide_count>0 ORDER BY name,noun_id")
                  if row["noun_id"] in filled and 0 < len(row["name"]) <= 160]
    records = []
    for identifier, noun in sorted(filled.items(), key=lambda pair: (-pair[1]["frequency"], pair[1]["label"], pair[0])):
        # WordNet meronyms are trustworthy part candidates; title support orders
        # them beside recurring assemblies actually covered by this catalog.
        candidates = set(counts[identifier]) | related[identifier]
        candidates.discard(identifier)
        candidates = [value for value in candidates if value in nouns and
                      normalized(nouns[value]["label"]) not in PART_STOPWORDS and
                      (value in related[identifier] or counts[identifier][value] >= 2)]
        candidates.sort(key=lambda value: (-int(value in related[identifier]),
                                           -counts[identifier][value], -nouns[value]["frequency"], value))
        seen, parts = set(), []
        for candidate in candidates:
            label = nouns[candidate]["label"]
            key = part_id(label)
            if len(key) < 2 or key in seen or len(label) > 80 or normalized(label) in PART_STOPWORDS:
                continue
            seen.add(key)
            parts.append({"id": key, "label": label[:1].upper() + label[1:], "query": normalized(label),
                          "aliases": sorted_aliases(aliases[candidate], maximum=120)})
            if len(parts) == PART_LIMIT:
                break
        if len(noun["label"]) > 100:
            continue  # Preserve the source label in SQLite; never ship invalid UI rows.
        records.append({"id": identifier, "label": noun["label"], "aliases": aliases[identifier],
                        "aliasRanks": {alias: ranks[identifier].get(alias, 99) for alias in aliases[identifier]},
                        "parts": parts, "guideCount": noun["guide_count"],
                        "frequency": noun["frequency"], "artUrl": None, "attribution": None})
    art = import_approved_art(records, art_config, nouns)
    # The first matching definition wins in the resident alias map. Broad labels
    # retain their normal meaning instead of rare/ambiguous WordNet senses.
    return {"version": 1, "revision": revision, "sources": lexical_sources(), "archetypes": records, "models": models}, art


def import_approved_art(records, config_path, nouns=None):
    config = json.loads(Path(config_path).read_bytes())
    if config.get("version") != 1 or not isinstance(config.get("artifacts"), list):
        raise ValueError("Reviewed artwork configuration must be version 1")
    bodies = {}
    for approved in config["artifacts"]:
        approved_aliases = set(sorted_aliases(approved["aliases"]))
        targets = [row for row in records if approved_aliases.intersection(row["aliases"]) and
                   (nouns is None or nouns[row["id"]]["domain"] in {"artifact", "noun.artifact"})]
        if not targets:
            continue
        # A source approval targets its ordinary object sense; one approved SVG
        # does not silently validate all polysemous senses with a shared alias.
        target = max(targets, key=lambda row: (row["guideCount"], row["frequency"]))
        data = load_approved_art(ROOT, approved)
        data.update(version=1, id=target["id"])
        body = json_bytes(data)
        digest = hashlib.sha256(body).hexdigest()
        bodies[digest] = body
        target["artUrl"] = "/api/art/" + digest + ".json"
        target["attribution"] = data["attribution"]
        target["parts"] = approved["parts"]
        target["aliases"] = sorted_aliases([*target["aliases"], *approved["aliases"]])
        # Reviewed source aliases express this site's object intent. For
        # example OEWN prefers motorcycle for "bike"; the reviewed interactive
        # bicycle uses that alias here. All browser competition stays generic.
        target.setdefault("aliasRanks", {}).update({alias: -1 for alias in approved_aliases})
    return bodies


def prepare(runtime, database, art_config=APPROVED_ART):
    runtime, database = Path(runtime).resolve(), Path(database).resolve()
    index_path, manifest_path = runtime / "index.json", runtime / "manifest.json"
    index, manifest = json.loads(index_path.read_bytes()), json.loads(manifest_path.read_bytes())
    if not manifest.get("runtimeComplete") or manifest.get("revision") != index.get("revision"):
        raise ValueError("Archetypes require a completed unpublished catalog")
    payload, art = compile_registry(database, index["revision"], art_config)
    # Only a finite, reviewed mapping can publish geometry. Generic filled nouns
    # get immediate parts, with no fake SVG box or forced drawing.
    art_dir = runtime / "art"
    art_dir.mkdir(mode=0o755, exist_ok=True)
    for digest, body in art.items():
        atomic_write(art_dir / (digest + ".json"), body)
        atomic_write(art_dir / (digest + ".json.gz"), gzip.compress(body, compresslevel=9, mtime=0))
    body = json_bytes(payload)
    packed = gzip.compress(body, compresslevel=9, mtime=0)
    credits = render_credits(payload).encode()
    atomic_write(runtime / "archetypes.json", body)
    atomic_write(runtime / "archetypes.json.gz", packed)
    atomic_write(runtime / "diagram-credits.html", credits)
    index["archetypes"] = payload
    index.pop("searchIndex", None)
    index.setdefault("search", {}).pop("inverted", None)
    index_body = json_bytes(index)
    index_packed = gzip.compress(index_body, compresslevel=9, mtime=0)
    atomic_write(index_path, index_body)
    atomic_write(runtime / "index.json.gz", index_packed)
    links_sha = hashlib.sha256()
    with closing(database_connection(database)) as connection:
        for row in connection.execute("SELECT guide_id,noun_id,confidence,reason FROM guide_nouns ORDER BY guide_id,noun_id"):
            links_sha.update(json_bytes(tuple(row)))
            links_sha.update(b"\n")
    metadata = {"version": 1, "count": len(payload["archetypes"]), "models": len(payload["models"]),
                "bytes": len(body), "gzipBytes": len(packed), "sha256": hashlib.sha256(body).hexdigest(),
                "artFiles": len(art), "linksSha256": links_sha.hexdigest()}
    manifest.pop("searchIndex", None)
    manifest.update(archetypes=metadata, diagramCredits={"bytes": len(credits), "sha256": hashlib.sha256(credits).hexdigest()},
                    indexBytes=len(index_body), indexGzipBytes=len(index_packed))
    atomic_write(manifest_path, json_bytes(manifest))
    for path in [runtime, art_dir, *art_dir.iterdir(), runtime / "archetypes.json", runtime / "archetypes.json.gz", runtime / "diagram-credits.html"]:
        path.chmod(0o755 if path.is_dir() else 0o644)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--art-config", type=Path, default=APPROVED_ART)
    args = parser.parse_args()
    print(json.dumps(prepare(args.runtime_dir, args.database, args.art_config)), flush=True)


if __name__ == "__main__":
    main()
