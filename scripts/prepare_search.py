#!/usr/bin/env python3
"""Prepare compact browser-resident token postings and Porter stem expansions.

Run after alias enrichment on an unpublished runtime. Only index/manifest files
change; repair articles, media, provenance, and source licenses remain untouched.
"""
from __future__ import annotations

import argparse
import base64
from collections.abc import Mapping
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import re
import stat
import unicodedata

ROOT = Path(__file__).resolve().parent.parent


def clean(value):
    return "".join(char for char in unicodedata.normalize("NFKD", str(value or ""))
                   if not unicodedata.category(char).startswith("M")).lower()


def tokens(value):
    # Unicode letters/numbers; underscore and punctuation separate tokens.
    words = re.findall(r"[^\W_]+", clean(value))
    # Preserve number-only searches such as 1708 for the model token A1708.
    return words + [number for word in words if any(char.isalpha() for char in word)
                    for number in re.findall(r"\d+", word)]


def porter(word):
    """Original Porter (1980) suffix stripping; ASCII lowercase words only.

    Rules: https://tartarus.org/martin/PorterStemmer/def.txt
    In particular this uses ABLI -> ABLE, not later BLI/LOGI modifications.
    Numeric identifiers and non-ASCII words are intentionally never stemmed.
    """
    if len(word) <= 2 or not re.fullmatch("[a-z]+", word):
        return word

    def vowels(part):
        flags = []
        for char in part:
            flags.append(char in "aeiou" or char == "y" and bool(flags) and not flags[-1])
        return flags

    def measure(part):
        flags = vowels(part)
        return sum(flags[index - 1] and not flags[index] for index in range(1, len(flags)))

    def contains_vowel(part):
        return any(vowels(part))

    def cvc(part):
        flags = vowels(part)
        return (len(flags) >= 3 and not flags[-3] and flags[-2] and not flags[-1]
                and part[-1] not in "wxy")

    def replace(part, rules, minimum):
        for suffix, replacement in rules:
            if part.endswith(suffix):
                stem = part[:-len(suffix)]
                return stem + replacement if measure(stem) > minimum else part
        return part

    # 1a: longest matching suffix wins.
    if word.endswith("sses"):
        word = word[:-2]
    elif word.endswith("ies"):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    # 1b.
    if word.endswith("eed"):
        if measure(word[:-3]) > 0:
            word = word[:-1]
    else:
        for suffix in ("ed", "ing"):
            if word.endswith(suffix) and contains_vowel(word[:-len(suffix)]):
                word = word[:-len(suffix)]
                if word.endswith(("at", "bl", "iz")):
                    word += "e"
                elif (len(word) >= 2 and word[-1] == word[-2] and not vowels(word)[-1]
                      and word[-1] not in "lsz"):
                    word = word[:-1]
                elif measure(word) == 1 and cvc(word):
                    word += "e"
                break
    # 1c.
    if word.endswith("y") and contains_vowel(word[:-1]):
        word = word[:-1] + "i"
    # 2. Sort by suffix length so TIONAL/ATION cannot hide ATIONAL/IZATION.
    step2 = {"ational": "ate", "tional": "tion", "enci": "ence", "anci": "ance",
             "izer": "ize", "abli": "able", "alli": "al", "entli": "ent", "eli": "e",
             "ousli": "ous", "ization": "ize", "ation": "ate", "ator": "ate", "alism": "al",
             "iveness": "ive", "fulness": "ful", "ousness": "ous", "aliti": "al",
             "iviti": "ive", "biliti": "ble"}
    word = replace(word, sorted(step2.items(), key=lambda item: -len(item[0])), 0)
    # 3.
    step3 = {"icate": "ic", "ative": "", "alize": "al", "iciti": "ic", "ical": "ic",
             "ful": "", "ness": ""}
    word = replace(word, sorted(step3.items(), key=lambda item: -len(item[0])), 0)
    # 4; ION additionally requires the stem to end in S/T.
    for suffix in sorted(("al", "ance", "ence", "er", "ic", "able", "ible", "ant", "ement",
                          "ment", "ent", "ion", "ou", "ism", "ate", "iti", "ous", "ive", "ize"),
                         key=lambda value: -len(value)):
        if word.endswith(suffix):
            part = word[:-len(suffix)]
            if measure(part) > 1 and (suffix != "ion" or part.endswith(("s", "t"))):
                word = part
            break
    # 5a / 5b.
    if word.endswith("e"):
        part = word[:-1]
        count = measure(part)
        if count > 1 or count == 1 and not cvc(part):
            word = part
    if word.endswith("ll") and measure(word) > 1:
        word = word[:-1]
    return word


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


def prepare_index(index, guide_records=None):
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
    guide_literals = []
    for identifier, row in enumerate(items):
        if not isinstance(row, list) or len(row) < 5:
            raise ValueError("Search rows must retain the five-field catalog shape.")
        own = set(tokens(row[1])) | set(tokens(row[4])) | set(tokens(names.get(row[2], "")))
        if extra and row[0] in extra:
            if row[0] not in unseen:
                raise ValueError("Structured guide ID occurs more than once in the catalog.")
            unseen.remove(row[0])
            own.update(extra[row[0]])
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
    if guide_literals:
        # Numeric/model queries retain the worker's strict literal gate. Only
        # these few source fields need text beside the compact token postings.
        result["guideLiterals"] = guide_literals
    return result


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


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


def prepare(data_dir, runtime=None):
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
    prepared = prepare_index(index, records)
    index["search"]["inverted"] = prepared
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
    args = parser.parse_args()
    prepare(args.data_dir, args.runtime_dir)
