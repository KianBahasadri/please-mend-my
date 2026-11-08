#!/usr/bin/env python3
"""Link every merged catalog source to pinned nouns using compact metadata only."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
from contextlib import closing
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3

from ingest_common import TEXT_HELPERS, normalize_noun as normalize
from seed_nouns import CONFIG, ROOT, configuration, file_digest

LINK_VERSION = 1
ACTIONABLE = {'guide', 'article'}
PHYSICAL_CATEGORIES = {'technology', 'vehicles', 'household', 'handyman', 'medical'}
# These are grammatical operations, not a registry of supported object names.
NOISE = {'replacement', 'repair', 'repairing', 'fix', 'fixing', 'disassembly',
         'assembly', 'guide', 'troubleshooting', 'introduction', 'overview',
         'step', 'steps', 'instruction', 'instructions', 'remove', 'removal'}


def read_connection(path: Path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def alias_ranks(connection: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Minimum ordered WordNet sense rank for each normalized noun alias.

    Lower ranks are lexical preferences, not application intent: WordNet puts
    motorcycle before bicycle for "bike". Approved object policy is separate.
    """
    result = defaultdict(dict)
    for identifier, alias, rank in connection.execute(
            'SELECT noun_id,normalized,min(rank) FROM noun_aliases GROUP BY noun_id,normalized'):
        result[identifier][alias] = rank
    return dict(result)


def forms(value: str):
    yield value
    if value.endswith('ies') and len(value) > 4:
        yield value[:-3] + 'y'
    if value.endswith('es') and len(value) > 4:
        yield value[:-2]
    if value.endswith('s') and not value.endswith(('ss', 'us', 'is')) and len(value) > 3:
        yield value[:-1]


class NounMatcher:
    def __init__(self, connection: sqlite3.Connection):
        self.nouns = {row[0]: {'label': row[1], 'domain': row[2], 'frequency': row[3]}
                      for row in connection.execute('SELECT id,label,domain,frequency FROM nouns')}
        self.aliases = defaultdict(list)
        self.maximum_words = 1
        for identifier, alias, rank in connection.execute('SELECT noun_id,normalized,rank FROM noun_aliases'):
            if len(alias) < 2 or alias in NOISE:
                continue
            self.aliases[alias].append((identifier, rank))
            self.maximum_words = max(self.maximum_words, min(6, len(alias.split())))
        self.domains = configuration()['categoryDomains']
        # Medical equipment is a physical artifact, but arbitrary artifacts in
        # clinical titles (CAR-T, Apple food, etc.) must not become object links.
        # Classify artifact branches from their source definitions, then inherit
        # down WordNet's is-a tree. No catalog-specific object whitelist is used.
        clinical = re.compile(r'\b(?:medic\w*|surg\w*|diagnos\w*|therap\w*|prosthe\w*|hospital\w*|patient\w*|wound\w*|injur\w*|dentist\w*|dental\w*|orthop\w*|anesthe\w*|clinical\w*)\b', re.I)
        self.medical_artifacts = {identifier for identifier, definition in connection.execute("SELECT id,definition FROM nouns WHERE domain IN ('artifact','noun.artifact')") if clinical.search(definition)}
        children = defaultdict(list)
        for parent, child in connection.execute("SELECT parent_id,child_id FROM noun_edges WHERE relation='is-a'"):
            children[parent].append(child)
        queue = deque(self.medical_artifacts)
        while queue:
            for child in children.get(queue.popleft(), ()):
                if child not in self.medical_artifacts and self.nouns[child]['domain'].removeprefix('noun.') == 'artifact':
                    self.medical_artifacts.add(child)
                    queue.append(child)

    def candidates(self, value: str, category: str, *, artifact_only=False, exact_only=False):
        words = normalize(value).split()
        allowed = set(self.domains.get(category, self.domains['other']))
        found = {}
        for length in range(min(len(words), self.maximum_words), 0, -1):
            if exact_only and length != len(words):
                continue
            for offset in range(len(words) - length + 1):
                term = ' '.join(words[offset:offset + length])
                for variant in forms(term):
                    for identifier, rank in self.aliases.get(variant, ()):
                        noun = self.nouns[identifier]
                        if noun['domain'].removeprefix('noun.') not in allowed:
                            continue
                        if artifact_only and noun['domain'].removeprefix('noun.') != 'artifact':
                            continue
                        if category == 'health' and noun['domain'].removeprefix('noun.') == 'artifact' and identifier not in self.medical_artifacts:
                            continue
                        # Prefer whole concepts, common sense ordering, and nouns
                        # from the source's object/clinical domain. Frequency
                        # breaks ties; it is never an inclusion threshold.
                        domain = noun['domain'].removeprefix('noun.')
                        preferred = domain in ({'body', 'state'} if category == 'health' else {'artifact'})
                        score = (int(term == ' '.join(words)), length,
                                 int(preferred), -min(rank, 12),
                                 noun['frequency'], -offset, identifier)
                        if identifier not in found or score > found[identifier]:
                            found[identifier] = score
        return sorted(found, key=lambda identifier: found[identifier], reverse=True)

    @lru_cache(maxsize=32768)
    def choose(self, value: str, category: str, artifact_only=False, exact_only=False):
        candidates = self.candidates(value, category, artifact_only=artifact_only, exact_only=exact_only)
        return candidates[0] if candidates else None


def subject_for_item(matcher, contexts, identifier, device, category):
    """Literal device concepts or source ancestry outrank model-name fragments.

    Product names contain ordinary words (Air, Go, One, Mini). A fragment is not
    evidence that its unrelated WordNet meaning is the product's generic parent.
    Check whole concepts, then physical ancestry, before considering fragments.
    """
    physical = category in PHYSICAL_CATEGORIES
    if device:
        subject = matcher.choose(device, category, physical, True)
        if subject:
            return subject, 'device', 0.95
    context = contexts.for_item(identifier)
    for exact in (True, False):
        for depth, title in context:
            subject = matcher.choose(title, category, physical, exact)
            if subject:
                return subject, 'source-category', max(0.75, 0.94 - depth * 0.02)
    if physical:
        # Software and other nonphysical technical concepts remain linkable,
        # but a model fragment can never establish one (MacBook Air -> tune).
        for depth, title in context:
            subject = matcher.choose(title, category, False, True)
            if subject:
                return subject, 'source-category', max(0.75, 0.90 - depth * 0.02)
    if device:
        subject = matcher.choose(device, category, physical)
        if subject:
            return subject, 'device-fragment', 0.75
    return None, 'title', 0.7


class SourceContext:
    """Bounded category ancestry, loaded from rows rather than guide documents."""
    def __init__(self, path: Path | None):
        self.categories, self.parents, self.memberships, self.guide_paths = {}, defaultdict(list), defaultdict(list), {}
        if path is None or not Path(path).is_file():
            return
        with closing(read_connection(path)) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'items', 'source_categories', 'category_edges', 'guide_categories'} <= tables:
                raise ValueError('Source category database is missing expected compact tables')
            self.categories = {row[0]: (row[1], row[2]) for row in connection.execute('SELECT path,title,category FROM source_categories')}
            for parent, child in connection.execute('SELECT parent,child FROM category_edges'):
                self.parents[child].append(parent)
            for guide, category in connection.execute('SELECT guide,category FROM guide_categories'):
                self.memberships[guide].append(category)
            self.guide_paths = dict(connection.execute('SELECT id,source_path FROM items'))
        for rows in (self.parents, self.memberships):
            for values in rows.values():
                values.sort()

    @lru_cache(maxsize=4096)
    def ancestors(self, category: str):
        queue, seen, result = deque([(category, 0)]), set(), []
        while queue and len(seen) < 64:
            current, depth = queue.popleft()
            if current in seen or depth > 10:
                continue
            seen.add(current)
            if current in self.categories:
                title, _ = self.categories[current]
                result.append((depth, title))
            queue.extend((parent, depth + 1) for parent in self.parents.get(current, ()))
        return tuple(result)

    def for_item(self, identifier: str):
        # A device/topic row is itself a category node. Its own parent edges
        # carry the same canonical context as a guide's category memberships.
        # Accepting a category path directly also lets category-link reporting
        # reuse exactly the same resolver instead of matching raw title words.
        path = self.guide_paths.get(identifier, identifier)
        values = []
        if path in self.categories:
            values.extend(self.ancestors(path))
        for category in self.memberships.get(path, ()):
            values.extend(self.ancestors(category))
        return sorted(set(values), key=lambda row: (row[0], row[1]))


class SourceContexts:
    """Keep each source category graph isolated; guide IDs select its context."""
    def __init__(self, paths):
        self.contexts = [SourceContext(path) for path in paths if path is not None and Path(path).is_file()]
        self.by_guide = {identifier: context for context in self.contexts for identifier in context.guide_paths}

    def for_item(self, identifier):
        context = self.by_guide.get(identifier)
        return context.for_item(identifier) if context else []


def _index_rows(document: dict):
    fields = document.get('fields', ['id', 'title', 'category', 'kind', 'device'])
    needed = ('id', 'title', 'category', 'kind', 'device')
    if document.get('version') != 1 or not document.get('revision') or not isinstance(document.get('items'), list):
        raise ValueError('Noun linking requires a version 1 merged compact index')
    positions = [fields.index(name) for name in needed]
    for row in document['items']:
        yield tuple(str(row[position] or '') for position in positions)


def _fingerprint(index_path, seed_database, ifixit_database, source_databases=()):
    digest = hashlib.sha256()
    for path in (Path(__file__), TEXT_HELPERS, CONFIG, index_path, seed_database):
        digest.update(str(path.name).encode() + b'\0' + file_digest(path).encode() + b'\0')
    # Source graph databases are immutable ingestion products. Include their
    # stat identity without hashing gigabytes of raw/source columns.
    for path in (ifixit_database, *source_databases):
        if path is not None and Path(path).is_file():
            stat = Path(path).stat()
            digest.update(f'{Path(path).resolve()}:{stat.st_size}:{stat.st_mtime_ns}'.encode())
    return digest.hexdigest()


def write_gaps(connection, destination, revision):
    gaps = [{'id': row[0], 'label': row[1], 'domain': row[2], 'frequency': row[3]}
            for row in connection.execute('SELECT id,label,domain,frequency FROM nouns WHERE guide_count=0 ORDER BY frequency DESC,label,id')]
    unlinked = [{'id': row[0], 'title': row[1], 'category': row[2], 'reason': row[3]}
                for row in connection.execute('SELECT i.id,i.title,i.category,d.reason FROM link_diagnostics d JOIN items i ON i.id=d.item_id ORDER BY i.id')]
    categories = [{'path': row[0], 'title': row[1], 'category': row[2]}
                  for row in connection.execute('SELECT path,title,category FROM source_category_links WHERE noun_id IS NULL ORDER BY path')]
    payload = {'version': 1, 'revision': revision, 'unfilledNouns': gaps,
               'unlinkedItems': unlinked, 'unlinkedSourceCategories': categories}
    temporary = destination.with_suffix('.json.part')
    with temporary.open('w') as stream:
        json.dump(payload, stream, ensure_ascii=False, separators=(',', ':'))
        stream.write('\n')
    temporary.chmod(0o644)
    temporary.replace(destination)


def link_catalog(index_path: Path, database: Path, seed_database: Path,
                 ifixit_database: Path | None = None, source_databases=()) -> dict:
    index_path, database, seed_database = map(Path, (index_path, database, seed_database))
    fingerprint = _fingerprint(index_path, seed_database, ifixit_database, source_databases)
    receipt_path = database.with_suffix('.json')
    gaps_path = database.parent / 'gaps.json'
    try:
        receipt = json.loads(receipt_path.read_bytes())
        if database.is_file() and gaps_path.is_file() and receipt.get('fingerprint') == fingerprint:
            with closing(read_connection(database)) as connection:
                if connection.execute("SELECT value FROM metadata WHERE key='linkFingerprint'").fetchone() == (fingerprint,):
                    return {**receipt, 'reused': True}
    except (OSError, ValueError, sqlite3.Error):
        pass
    with index_path.open() as stream:
        document = json.load(stream)
    database.parent.mkdir(parents=True, exist_ok=True)
    temporary = database.with_name(database.name + '.building')
    temporary.unlink(missing_ok=True)
    shutil.copyfile(seed_database, temporary)
    contexts = SourceContexts((ifixit_database, *source_databases))
    with closing(sqlite3.connect(temporary)) as connection:
        connection.executescript('''
            PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-16384;
            CREATE TABLE items(id TEXT PRIMARY KEY,title TEXT NOT NULL,category TEXT NOT NULL,kind TEXT NOT NULL,device TEXT NOT NULL);
            CREATE TABLE guide_nouns(guide_id TEXT NOT NULL,noun_id TEXT NOT NULL,confidence REAL NOT NULL,reason TEXT NOT NULL,PRIMARY KEY(guide_id,noun_id));
            CREATE TABLE models(name TEXT NOT NULL,noun_id TEXT NOT NULL,guide_count INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(name,noun_id));
            CREATE TABLE link_diagnostics(item_id TEXT PRIMARY KEY,reason TEXT NOT NULL);
            CREATE TABLE source_category_links(path TEXT PRIMARY KEY,title TEXT NOT NULL,category TEXT NOT NULL,noun_id TEXT);
        ''')
        matcher = NounMatcher(connection)
        for number, context in enumerate(contexts.contexts):
            for path, (title, category) in sorted(context.categories.items()):
                subject, _, _ = subject_for_item(matcher, context, path, title, category)
                qualified = path if number == 0 else str(number) + ':' + path
                connection.execute('INSERT INTO source_category_links VALUES(?,?,?,?)', (qualified, title, category, subject))
        source_counts, linked_counts = Counter(), Counter()
        for identifier, title, category, kind, device in _index_rows(document):
            connection.execute('INSERT INTO items VALUES(?,?,?,?,?)', (identifier, title, category, kind, device))
            source_counts[category] += 1
            subject, reason, confidence = subject_for_item(matcher, contexts, identifier, device, category)
            title_candidates = matcher.candidates(title, category)
            if subject is None and title_candidates:
                subject = title_candidates[0]
                reason, confidence = 'title', 0.7
            if subject is None:
                connection.execute('INSERT INTO link_diagnostics VALUES(?,?)', (identifier, 'No category-compatible noun matched compact source metadata'))
                continue
            links = {subject: (confidence, reason)}
            # Title-supported components receive their own coverage; never link
            # every part of an archetype to every guide using only group aliases.
            for candidate in title_candidates[:3]:
                noun = matcher.nouns[candidate]
                if noun['domain'].removeprefix('noun.') in ({'body', 'state'} if category == 'health' else {'artifact'}):
                    links.setdefault(candidate, (0.65, 'title-component'))
            connection.executemany('INSERT INTO guide_nouns VALUES(?,?,?,?)', [(identifier, noun, score, evidence) for noun, (score, evidence) in links.items()])
            linked_counts[category] += 1
            if device and normalize(device) not in {alias for alias in forms(normalize(matcher.nouns[subject]['label']))}:
                connection.execute('INSERT INTO models VALUES(?,?,?) ON CONFLICT(name,noun_id) DO UPDATE SET guide_count=guide_count+excluded.guide_count', (device, subject, int(kind in ACTIONABLE)))
        connection.executescript('''
            CREATE INDEX links_noun ON guide_nouns(noun_id,guide_id);
            CREATE INDEX items_category ON items(category,kind);
            CREATE INDEX models_noun ON models(noun_id);
            UPDATE nouns SET guide_count=(SELECT count(*) FROM guide_nouns g JOIN items i ON i.id=g.guide_id WHERE g.noun_id=nouns.id AND i.kind IN ('guide','article'));
        ''')
        connection.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('linkFingerprint', fingerprint))
        connection.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('catalogRevision', document['revision']))
        connection.commit()
        receipt = {'version': LINK_VERSION, 'revision': document['revision'], 'fingerprint': fingerprint,
                   'items': sum(source_counts.values()), 'linkedItems': sum(linked_counts.values()),
                   'filledNouns': connection.execute('SELECT count(*) FROM nouns WHERE guide_count>0').fetchone()[0],
                   'models': connection.execute('SELECT count(*) FROM models WHERE guide_count>0').fetchone()[0],
                   'links': connection.execute('SELECT count(*) FROM guide_nouns').fetchone()[0],
                   'sources': {category: {'items': source_counts[category], 'linked': linked_counts[category]} for category in sorted(source_counts)},
                   'reused': False}
        write_gaps(connection, gaps_path, document['revision'])
    temporary.chmod(0o644)
    temporary.replace(database)
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + '\n')
    receipt_path.chmod(0o644)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--database', type=Path, default=ROOT / 'catalog-data/build/nouns/catalog.sqlite')
    parser.add_argument('--seed-database', type=Path, default=ROOT / 'catalog-data/build/nouns/seed.sqlite')
    parser.add_argument('--ifixit-database', type=Path)
    parser.add_argument('--source-database', type=Path, action='append', default=[])
    args = parser.parse_args()
    print(json.dumps(link_catalog(args.index, args.database, args.seed_database, args.ifixit_database, args.source_database)), flush=True)


if __name__ == '__main__':
    main()
