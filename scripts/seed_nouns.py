#!/usr/bin/env -S uv run --script
# /// script
# requires-python = "==3.12.12"
# dependencies = ["wordfreq==3.1.1"]
# ///
"""Pin OEWN common-noun senses, semantic edges, and wordfreq usage in SQLite."""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import urllib.request
import xml.etree.ElementTree as ET

from ingest_common import TEXT_HELPERS, normalize_noun as normalize

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(__file__).with_name("nouns-source.json")
SCHEMA_VERSION = 1


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def configuration() -> dict:
    return json.loads(CONFIG.read_bytes())


def source_file(data_dir: Path, *, offline: bool = False) -> Path:
    record = configuration()["wordnet"]
    directory = Path(data_dir) / "nouns" / "raw"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / record["filename"]
    if not destination.exists():
        if offline:
            raise FileNotFoundError("Pinned OEWN source is unavailable offline: " + str(destination))
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            request = urllib.request.Request(record["url"], headers={"User-Agent": "please-mend-my catalog builder"})
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
                while block := response.read(1024 * 1024):
                    output.write(block)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    if destination.stat().st_size != record["bytes"] or file_digest(destination) != record["sha256"]:
        raise ValueError("Pinned OEWN source size or SHA-256 does not match")
    return destination


def parse_wordnet(source: Path, domains: set[str]) -> tuple[dict, dict, list]:
    """Read lexical rows and senses once; never keep the XML tree in memory."""
    aliases: dict[str, list[tuple[str, int]]] = defaultdict(list)
    synsets: dict[str, tuple[str, str, list[str]]] = {}
    edges: set[tuple[str, str, str]] = set()
    opener = gzip.open if source.suffix == ".gz" else open
    with opener(source, "rb") as stream:
        stack = []
        for event, element in ET.iterparse(stream, events=("start", "end")):
            if event == "start":
                stack.append(element)
                continue
            if element.tag == "LexicalEntry":
                lemma = element.find("Lemma")
                if lemma is not None and lemma.get("partOfSpeech") == "n":
                    forms = [lemma.get("writtenForm", ""), *(form.get("writtenForm", "") for form in element.findall("Form"))]
                    for rank, sense in enumerate(element.findall("Sense")):
                        if sense.get("synset"):
                            for form in forms:
                                if normalize(form) and not any(char.isdigit() for char in form):
                                    aliases[sense.attrib["synset"]].append((form, rank))
                element.clear()
                if len(stack) > 1:
                    stack[-2].remove(element)
            elif element.tag == "Synset":
                domain = element.get("lexfile", "").removeprefix("noun.")
                if element.get("partOfSpeech") == "n" and domain in domains:
                    identifier = element.attrib["id"]
                    # members define the lexicon's canonical sense label, not the
                    # first alphabetically encountered alias or an LLM guess.
                    members = element.get("members", "").split()
                    synsets[identifier] = (domain, " ".join(element.findtext("Definition", "").split()), members)
                    for relation in element.findall("SynsetRelation"):
                        kind, target = relation.get("relType"), relation.get("target")
                        if not target:
                            continue
                        if kind == "hypernym":
                            edges.add((target, identifier, "is-a"))
                        elif kind == "mero_part":
                            edges.add((identifier, target, "has-part"))
                        elif kind == "holo_part":
                            edges.add((target, identifier, "has-part"))
                element.clear()
                if len(stack) > 1:
                    stack[-2].remove(element)
            stack.pop()
    return synsets, aliases, sorted(edges)


def _canonical(identifier: str, members: list[str], choices: list[tuple[str, int]]) -> str:
    if members:
        encoded = members[0].removeprefix("oewn-").removesuffix("-n").replace("_", " ")
        for alias, _ in choices:
            if normalize(alias) == normalize(encoded):
                return alias
    return min(choices, key=lambda value: (value[1], len(value[0]), value[0]))[0]


def build_seed(source: Path, destination: Path, frequency=None, config: dict | None = None) -> dict:
    """Deterministic core, injectable frequencies keep tiny unit fixtures offline."""
    config = config or configuration()
    if frequency is None:
        from wordfreq import zipf_frequency
        frequency = lambda word: zipf_frequency(word, "en", wordlist="best", minimum=0.0)
    synsets, aliases, edges = parse_wordnet(source, set(config["domains"]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".building")
    temporary.unlink(missing_ok=True)
    with closing(sqlite3.connect(temporary)) as connection:
        connection.executescript("""
            PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE nouns(id TEXT PRIMARY KEY,label TEXT NOT NULL,domain TEXT NOT NULL,definition TEXT NOT NULL,frequency REAL NOT NULL,guide_count INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE noun_aliases(noun_id TEXT NOT NULL,alias TEXT NOT NULL,normalized TEXT NOT NULL,rank INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(noun_id,alias));
            CREATE TABLE noun_edges(parent_id TEXT NOT NULL,child_id TEXT NOT NULL,relation TEXT NOT NULL,PRIMARY KEY(parent_id,child_id,relation));
        """)
        included = set()
        for identifier, (domain, definition, members) in sorted(synsets.items()):
            choices = list(dict.fromkeys(aliases.get(identifier, [])))
            if not choices:
                continue
            label = _canonical(identifier, members, choices)
            # Proper names are distributed separately by OEWN. Retain capitalized
            # common acronyms, but not lexical entries lacking a common form.
            if not any(form[0].islower() or form.isupper() for form, _ in choices):
                continue
            included.add(identifier)
            connection.execute("INSERT INTO nouns(id,label,domain,definition,frequency) VALUES(?,?,?,?,?)", (identifier, label, domain, definition, float(frequency(label))))
            connection.executemany("INSERT OR IGNORE INTO noun_aliases VALUES(?,?,?,?)", [(identifier, form, normalize(form), rank) for form, rank in choices])
        connection.executemany("INSERT OR IGNORE INTO noun_edges VALUES(?,?,?)", [edge for edge in edges if edge[0] in included and edge[1] in included])
        connection.executescript("CREATE INDEX aliases_normalized ON noun_aliases(normalized); CREATE INDEX edges_child ON noun_edges(child_id,relation);")
        metadata = {"schemaVersion": str(SCHEMA_VERSION), "sourceSha256": file_digest(source), "wordfreqVersion": config["wordfreq"]["version"]}
        connection.executemany("INSERT INTO metadata VALUES(?,?)", sorted(metadata.items()))
        connection.commit()
        receipt = {"version": 1, "nouns": len(included), "aliases": connection.execute("SELECT count(*) FROM noun_aliases").fetchone()[0], "edges": connection.execute("SELECT count(*) FROM noun_edges").fetchone()[0], **metadata}
    temporary.chmod(0o644)
    temporary.replace(destination)
    return receipt


def seed_nouns(data_dir: Path, *, offline: bool = False) -> Path:
    data_dir = Path(data_dir)
    source = source_file(data_dir, offline=offline)
    destination = data_dir / "nouns" / "seed.sqlite"
    fingerprint = hashlib.sha256(Path(__file__).read_bytes() + TEXT_HELPERS.read_bytes() + CONFIG.read_bytes() + file_digest(source).encode()).hexdigest()
    receipt_path = destination.with_suffix(".json")
    try:
        receipt = json.loads(receipt_path.read_bytes())
        if destination.is_file() and receipt.get("fingerprint") == fingerprint:
            with closing(sqlite3.connect(f"file:{destination}?mode=ro", uri=True)) as connection:
                if connection.execute("SELECT value FROM metadata WHERE key='schemaVersion'").fetchone() == (str(SCHEMA_VERSION),):
                    return destination
    except (OSError, ValueError, sqlite3.Error):
        pass
    receipt = build_seed(source, destination)
    receipt["fingerprint"] = fingerprint
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "catalog-data/build")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    print(seed_nouns(args.data_dir, offline=args.offline))


if __name__ == "__main__":
    main()
