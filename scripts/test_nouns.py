import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape

from link_nouns import alias_ranks, link_catalog
from seed_nouns import build_seed, normalize


def write_wordnet(path):
    # Independent noun senses exercise domains and relationships; the production
    # algorithm uses the complete pinned source, not these fixture subjects.
    definitions = [
        ('car', 'car', 'artifact', 'A motor vehicle with four wheels.', [('hypernym', 'vehicle')]),
        ('vehicle', 'vehicle', 'artifact', 'A conveyance for transport.', []),
        ('arm', 'arm', 'body', 'A human upper limb.', [('mero_part', 'bone')]),
        ('bone', 'bone', 'body', 'Rigid body tissue.', [('holo_part', 'arm')]),
        ('fracture', 'fracture', 'state', 'The breaking of hard tissue.', []),
        ('defib', 'defibrillator', 'artifact', 'An electronic instrument for restarting the heart.', [('hypernym', 'medical-instrument')]),
        ('medical-instrument', 'medical instrument', 'artifact', 'An instrument used by a patient for diagnosis.', []),
        ('screen', 'screen', 'artifact', 'An electronic display surface.', []),
        ('phone', 'phone', 'artifact', 'An electronic device for speaking over distance.', [('mero_part', 'screen')]),
        ('laptop', 'laptop', 'artifact', 'A portable computer.', []),
        ('tune', 'tune', 'communication', 'A musical composition.', []),
        ('apple', 'apple', 'food', 'A round edible fruit.', []),
        ('community', 'community', 'group', 'A group living together.', []),
        ('earth', 'earth', 'object', 'The world.', []),
        ('resume', 'resume', 'communication', 'A short account of qualifications.', []),
        ('proper', 'London', 'location', 'A proper place name.', []),
    ]
    entries, synsets = [], []
    for identifier, label, domain, definition, relations in definitions:
        aliases = [label, 'résumé'] if identifier == 'resume' else ([label, 'air'] if identifier == 'tune' else [label])
        for index, alias in enumerate(aliases):
            entries.append(f'<LexicalEntry id="entry-{identifier}-{index}"><Lemma writtenForm="{escape(alias)}" partOfSpeech="n"/><Sense synset="{identifier}"/></LexicalEntry>')
        edges = ''.join(f'<SynsetRelation relType="{kind}" target="{target}"/>' for kind, target in relations)
        synsets.append(f'<Synset id="{identifier}" partOfSpeech="n" lexfile="noun.{domain}"><Definition>{escape(definition)}</Definition>{edges}</Synset>')
    path.write_text('<LexicalResource><Lexicon>' + ''.join(entries + synsets) + '</Lexicon></LexicalResource>')


class NounPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source, self.seed = self.root / 'source.xml', self.root / 'seed.sqlite'
        write_wordnet(self.source)
        self.receipt = build_seed(self.source, self.seed, frequency=lambda _: 4.0)

    def connection(self, path):
        connection = sqlite3.connect(path)
        self.addCleanup(connection.close)
        return connection

    def write_index(self, rows):
        path = self.root / 'index.json'
        path.write_text(json.dumps({'version': 1, 'revision': 'merged-fixture',
                                   'fields': ['id', 'title', 'category', 'kind', 'device'], 'items': rows}))
        return path

    def source_context(self):
        path = self.root / 'ifixit.sqlite'
        connection = self.connection(path)
        connection.executescript('''
            CREATE TABLE items(id TEXT,source_path TEXT);
            CREATE TABLE source_categories(path TEXT,title TEXT,category TEXT);
            CREATE TABLE category_edges(parent TEXT,child TEXT);
            CREATE TABLE guide_categories(guide TEXT,category TEXT);
            INSERT INTO items VALUES('phone-guide','Guide/123');
            INSERT INTO source_categories VALUES('Device/Phone','Phone','technology');
            INSERT INTO source_categories VALUES('Device/Samsung_Galaxy','Samsung Galaxy','technology');
            INSERT INTO category_edges VALUES('Device/Phone','Device/Samsung_Galaxy');
            INSERT INTO guide_categories VALUES('Guide/123','Device/Samsung_Galaxy');
        ''')
        connection.commit()
        return path

    def test_seed_preserves_synonyms_parent_and_part_edges_without_proper_names(self):
        connection = self.connection(self.seed)
        self.assertEqual(self.receipt['nouns'], 15)
        self.assertIsNone(connection.execute("SELECT id FROM nouns WHERE id='proper'").fetchone())
        self.assertEqual(connection.execute("SELECT frequency FROM nouns WHERE id='earth'").fetchone(), (4.0,))
        self.assertEqual(connection.execute("SELECT normalized FROM noun_aliases WHERE alias='résumé'").fetchone(), ('resume',))
        edges = set(connection.execute('SELECT parent_id,child_id,relation FROM noun_edges'))
        self.assertIn(('vehicle', 'car', 'is-a'), edges)
        self.assertIn(('arm', 'bone', 'has-part'), edges)
        self.assertEqual(sum(row == ('arm', 'bone', 'has-part') for row in edges), 1)
        self.assertEqual(normalize('SM-G991B'), 'sm g991b')
        self.assertEqual(alias_ranks(connection)['resume']['resume'], 0)

    def test_links_all_sources_keeps_models_and_counts_actionable_guides_only(self):
        index = self.write_index([
            ['phone-guide', 'Samsung Galaxy screen replacement', 'technology', 'guide', 'Samsung SM-G991B'],
            ['phone-topic', 'Phone', 'technology', 'topic', 'Phone'],
            ['medical-guide', 'Arm fracture', 'health', 'article', ''],
            ['first-aid-guide', 'Arm injuries', 'health', 'article', ''],
            ['community-guide', 'Community', 'other', 'article', ''],
        ])
        database = self.root / 'build/catalog.sqlite'
        receipt = link_catalog(index, database, self.seed, self.source_context())
        connection = self.connection(database)
        self.assertEqual(receipt['items'], 5)
        self.assertEqual(receipt['sources']['health']['linked'], 2)
        self.assertEqual(connection.execute("SELECT guide_count FROM nouns WHERE id='phone'").fetchone(), (1,))
        self.assertEqual(connection.execute("SELECT name,noun_id,guide_count FROM models").fetchone(), ('Samsung SM-G991B', 'phone', 1))
        self.assertEqual(connection.execute("SELECT guide_count FROM nouns WHERE id='arm'").fetchone(), (2,))
        self.assertEqual(connection.execute("SELECT noun_id FROM guide_nouns WHERE guide_id='phone-guide' AND reason='source-category'").fetchone(), ('phone',))
        gaps = json.loads((database.parent / 'gaps.json').read_text())
        self.assertIn('earth', {noun['id'] for noun in gaps['unfilledNouns']})
        self.assertTrue(link_catalog(index, database, self.seed, self.root / 'ifixit.sqlite')['reused'])

    def test_domain_guard_accepts_medical_equipment_but_rejects_cart_and_apple_collision(self):
        index = self.write_index([
            ['medical-device', 'Defibrillator', 'health', 'article', ''],
            ['cart-therapy', 'CAR-T therapy', 'health', 'article', ''],
            ['apple-brand', 'Apple repair', 'technology', 'guide', 'Apple'],
            ['fruit-article', 'Apple', 'other', 'article', ''],
        ])
        database = self.root / 'catalog.sqlite'
        link_catalog(index, database, self.seed)
        connection = self.connection(database)
        self.assertIsNotNone(connection.execute("SELECT 1 FROM guide_nouns WHERE guide_id='medical-device' AND noun_id='defib'").fetchone())
        self.assertIsNone(connection.execute("SELECT 1 FROM guide_nouns WHERE guide_id='cart-therapy' AND noun_id='car'").fetchone())
        self.assertIsNone(connection.execute("SELECT 1 FROM guide_nouns WHERE guide_id='apple-brand' AND noun_id='apple'").fetchone())
        self.assertIsNotNone(connection.execute("SELECT 1 FROM guide_nouns WHERE guide_id='fruit-article' AND noun_id='apple'").fetchone())
        gaps = json.loads((database.parent / 'gaps.json').read_text())
        self.assertEqual({row['id'] for row in gaps['unlinkedItems']}, {'cart-therapy', 'apple-brand'})

    def test_rebuild_replaces_scratch_database_and_invalidates_changed_input(self):
        index = self.write_index([['one', 'Laptop', 'technology', 'guide', 'Laptop']])
        database = self.root / 'catalog.sqlite'
        first = link_catalog(index, database, self.seed)
        self.write_index([['two', 'Arm', 'health', 'article', '']])
        second = link_catalog(index, database, self.seed)
        self.assertFalse(second['reused'])
        self.assertNotEqual(first['fingerprint'], second['fingerprint'])
        connection = self.connection(database)
        self.assertEqual(connection.execute('SELECT id FROM items').fetchall(), [('two',)])
        self.assertEqual(connection.execute("SELECT guide_count FROM nouns WHERE id='laptop'").fetchone(), (0,))

    def test_shared_text_change_invalidates_unchanged_noun_link_cache(self):
        index = self.write_index([['one', 'Laptop', 'technology', 'guide', 'Laptop']])
        database = self.root / 'catalog.sqlite'
        helpers = self.root / 'shared_text.py'
        helpers.write_text('initial text implementation')
        with patch('link_nouns.TEXT_HELPERS', helpers):
            first = link_catalog(index, database, self.seed)
            self.assertTrue(link_catalog(index, database, self.seed)['reused'])
            helpers.write_text('updated text implementation')
            rebuilt = link_catalog(index, database, self.seed)
        self.assertFalse(rebuilt['reused'])
        self.assertNotEqual(first['fingerprint'], rebuilt['fingerprint'])
        connection = self.connection(database)
        self.assertEqual(connection.execute('SELECT id FROM items').fetchall(), [('one',)])

    def test_generic_model_ancestry_beats_unrelated_model_name_words(self):
        context = self.source_context()
        connection = self.connection(context)
        connection.executescript('''
            INSERT INTO items VALUES('mac-guide','Guide/456');
            INSERT INTO items VALUES('mac-topic','Device/MacBook_Air');
            INSERT INTO source_categories VALUES('Device/MacBook_Air','MacBook Air','technology');
            INSERT INTO source_categories VALUES('Device/Laptop','Laptop','technology');
            INSERT INTO category_edges VALUES('Device/Laptop','Device/MacBook_Air');
            INSERT INTO guide_categories VALUES('Guide/456','Device/MacBook_Air');
        ''')
        connection.commit()
        index = self.write_index([
            ['mac-guide', 'MacBook Air screen replacement', 'technology', 'guide', 'MacBook Air'],
            ['mac-topic', 'MacBook Air', 'technology', 'topic', 'MacBook Air'],
            ['phone-guide', 'Samsung screen replacement', 'technology', 'guide', 'Samsung SM-G991B'],
        ])
        database = self.root / 'catalog.sqlite'
        link_catalog(index, database, self.seed, context)
        linked = self.connection(database)
        self.assertEqual(dict(linked.execute('SELECT name,noun_id FROM models')), {'MacBook Air': 'laptop', 'Samsung SM-G991B': 'phone'})
        self.assertEqual(linked.execute("SELECT noun_id FROM guide_nouns WHERE guide_id='mac-guide' AND reason='source-category'").fetchone(), ('laptop',))
        self.assertEqual(linked.execute("SELECT noun_id FROM guide_nouns WHERE guide_id='mac-topic' AND reason='source-category'").fetchone(), ('laptop',))
        self.assertEqual(linked.execute("SELECT noun_id FROM source_category_links WHERE path='Device/MacBook_Air'").fetchone(), ('laptop',))
        self.assertIsNone(linked.execute("SELECT guide_id FROM guide_nouns WHERE noun_id='tune'").fetchone())

    def test_category_graphs_from_new_sources_stay_isolated(self):
        paths = []
        for name, identifier, label, category in [('ifixit', 'one', 'Phone', 'technology'),
                                                   ('appropedia', 'two', 'Arm', 'health')]:
            path = self.root / (name + '.sqlite')
            db = self.connection(path)
            db.executescript('''
                CREATE TABLE items(id TEXT,source_path TEXT);
                CREATE TABLE source_categories(path TEXT,title TEXT,category TEXT);
                CREATE TABLE category_edges(parent TEXT,child TEXT);
                CREATE TABLE guide_categories(guide TEXT,category TEXT);
            ''')
            db.execute('INSERT INTO items VALUES(?,?)', (identifier, 'Shared_path'))
            db.execute('INSERT INTO source_categories VALUES(?,?,?)', ('Category:Shared', label, category))
            db.execute('INSERT INTO guide_categories VALUES(?,?)', ('Shared_path', 'Category:Shared'))
            db.commit()
            paths.append(path)
        index = self.write_index([['one', 'Unfamiliar procedure', 'technology', 'guide', ''],
                                  ['two', 'Unfamiliar procedure', 'health', 'article', '']])
        database = self.root / 'catalog.sqlite'
        link_catalog(index, database, self.seed, paths[0], [paths[1]])
        db = self.connection(database)
        links = dict(db.execute('SELECT guide_id,noun_id FROM guide_nouns'))
        self.assertEqual(links, {'one': 'phone', 'two': 'arm'})
        self.assertEqual(db.execute('SELECT count(*) FROM source_category_links').fetchone()[0], 2)



if __name__ == '__main__':
    unittest.main()
