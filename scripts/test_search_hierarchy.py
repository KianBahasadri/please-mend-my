"""Offline subject ancestry uses lexical rows, never per-guide file scans."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from prepare_search import prepare_hierarchy, prepare_index
from test_prepare_search import unpack


class SearchHierarchyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / 'nouns.sqlite'
        self.config = self.root / 'nouns.json'
        self.config.write_text(json.dumps({'categoryDomains': {
            'technology': ['artifact', 'state'], 'health': ['body', 'state', 'person'],
            'medical': ['body', 'state', 'person'],
            'other': ['artifact', 'animal', 'body', 'state', 'person'],
        }}))
        self.index = {'version': 1, 'revision': 'fixture',
            'categories': [{'id': value, 'name': value} for value in ('technology', 'health', 'medical', 'other')],
            'items': [
                ['model', 'Portable X123 Screen Replacement', 'technology', 'guide', 'Portable X123'],
                ['computer', 'Computer Screen Repair', 'technology', 'guide', 'Computer'],
                ['topic', 'Laptop', 'technology', 'topic', 'Laptop'],
                ['choking', 'Choking', 'health', 'article', ''],
            ],
            'search': {'version': 1, 'groups': [''], 'itemGroups': [0, 0, 0, 0]},
            'archetypes': {'version': 1, 'archetypes': [
                {'id': 'laptop', 'label': 'Laptop', 'aliases': ['laptop'], 'parts': []},
                {'id': 'computer', 'label': 'Computer', 'aliases': ['computer'], 'parts': []},
                {'id': 'choking', 'label': 'Choking', 'aliases': ['choking'], 'parts': []},
            ], 'models': [{'name': 'Portable X123', 'nounId': 'laptop'}]},
        }
        with closing(sqlite3.connect(self.database)) as db:
            db.executescript('''
                CREATE TABLE nouns(id TEXT PRIMARY KEY,label TEXT,domain TEXT,frequency REAL,guide_count INTEGER);
                CREATE TABLE noun_aliases(noun_id TEXT,alias TEXT,rank INTEGER);
                CREATE TABLE noun_edges(parent_id TEXT,child_id TEXT,relation TEXT);
                CREATE TABLE guide_nouns(guide_id TEXT,noun_id TEXT,reason TEXT);
                CREATE TABLE items(id TEXT PRIMARY KEY,kind TEXT);
            ''')
            db.executemany('INSERT INTO nouns VALUES(?,?,?,?,?)', [
                ('computer', 'computer', 'artifact', 5.0, 1),
                ('laptop', 'laptop', 'artifact', 4.0, 1),
                ('ultrabook', 'ultrabook', 'artifact', 3.0, 0),
                ('grandmother', 'grandmother', 'person', 4.0, 0),
                ('person', 'person', 'person', 6.0, 0),
                ('animal', 'animal', 'animal', 5.0, 0),
                ('choking', 'choking', 'state', 4.0, 1),
            ])
            db.executemany('INSERT INTO noun_aliases VALUES(?,?,?)', [
                ('computer', 'computer', 0), ('laptop', 'laptop', 0),
                ('ultrabook', 'Ultrabook', 0), ('grandmother', 'grandmother', 0),
                ('grandmother', 'grandma', 0), ('grandmother', 'grandma', 1),
                ('person', 'person', 0), ('person', 'human', 0),
                ('animal', 'animal', 0), ('choking', 'choking', 0),
                ('ultrabook', 'bad X123 alias', 0),
            ])
            db.executemany('INSERT INTO noun_edges VALUES(?,?,?)', [
                ('computer', 'laptop', 'is-a'), ('laptop', 'ultrabook', 'is-a'),
                ('person', 'grandmother', 'is-a'), ('animal', 'person', 'is-a'),
                ('laptop', 'computer', 'has-part'),
            ])
            db.executemany('INSERT INTO items VALUES(?,?)', [(row[0], row[3]) for row in self.index['items']])
            db.executemany('INSERT INTO guide_nouns VALUES(?,?,?)', [
                ('model', 'laptop', 'device'), ('computer', 'computer', 'title'),
                ('topic', 'laptop', 'device'), ('choking', 'choking', 'title'),
                ('model', 'computer', 'title-component'),
            ])
            db.commit()

    def hierarchy(self):
        return prepare_hierarchy(self.index, self.database, self.config)

    def test_relationship_direction_unfilled_structural_aliases_and_sense_domains(self):
        before = deepcopy(self.index)
        result = self.hierarchy()
        positions = {row[0]: position for position, row in enumerate(result['nodes'])}
        self.assertEqual(result['fields'], ['id', 'label', 'domain', 'frequency', 'guideCount'])
        self.assertEqual(result['parents'][positions['ultrabook']], [positions['laptop']])
        self.assertEqual(result['parents'][positions['laptop']], [positions['computer']])
        self.assertEqual(result['parents'][positions['grandmother']], [positions['person']])
        self.assertEqual(result['nodes'][positions['person']][2], 'person')
        self.assertEqual(result['nodes'][positions['animal']][2], 'animal', 'cross-domain edges remain explicit, never merged senses')
        self.assertIn(['grandma', positions['grandmother'], 0], result['aliases'])
        self.assertIn(['human', positions['person'], 0], result['aliases'])
        self.assertEqual(sum(alias[0] == 'grandma' for alias in result['aliases']), 1)
        self.assertNotIn('laptop', [alias[0] for alias in result['aliases']], 'filled aliases already reside in registry')
        self.assertFalse(any('123' in alias[0] for alias in result['aliases']))
        self.assertEqual(self.index, before, 'structural nodes must not become filled UI archetypes')
        self.assertEqual(self.hierarchy(), result)

    def test_primary_membership_preserves_catalog_positions_excludes_topics_and_incidental_parts(self):
        result = self.hierarchy()
        identifiers = [row[0] for row in result['nodes']]
        documents = unpack({'terms': identifiers, 'offsets': result['documents']['offsets'],
                            'postings': result['documents']['data']})
        self.assertEqual(documents['laptop'], [0])
        self.assertEqual(documents['computer'], [1], 'incidental component link cannot establish subject ancestry')
        self.assertEqual(documents['choking'], [3], 'topic position 2 is retained, never renumbered')
        self.assertEqual(documents['grandmother'], [])
        self.assertEqual(result['categories'][identifiers.index('laptop')], ['technology'])
        self.assertEqual(result['domainCategories']['person'], ['health', 'medical'])
        self.assertNotIn('other', {value for values in result['domainCategories'].values() for value in values})

    def test_hierarchy_does_not_change_strict_inverted_postings_or_model_contract(self):
        before = prepare_index(self.index)
        rebuilt = deepcopy(self.index)
        rebuilt['search']['hierarchy'] = self.hierarchy()
        self.assertEqual(prepare_index(rebuilt), before)
        self.assertEqual(rebuilt['archetypes']['models'], [{'name': 'Portable X123', 'nounId': 'laptop'}])
        self.assertNotIn('grandma', unpack(before), 'inheritance must not rewrite every primary document alias')

    def test_cycles_unknown_nouns_and_missing_guide_ids_reject_before_publication(self):
        cases = ["INSERT INTO noun_edges VALUES('ultrabook','computer','is-a')",
                 "INSERT INTO noun_edges VALUES('missing','laptop','is-a')",
                 "INSERT INTO guide_nouns VALUES('absent','laptop','title')"]
        for statement in cases:
            with self.subTest(statement=statement), closing(sqlite3.connect(self.database)) as db:
                if 'absent' in statement:
                    db.execute("INSERT INTO items VALUES('absent','guide')")
                db.execute(statement)
                db.commit()
                with self.assertRaises(ValueError):
                    self.hierarchy()
                if 'noun_edges' in statement:
                    db.execute("DELETE FROM noun_edges WHERE parent_id IN ('ultrabook','missing')")
                else:
                    db.execute("DELETE FROM guide_nouns WHERE guide_id='absent'")
                db.commit()

    def test_legacy_build_without_noun_database_has_no_hierarchy(self):
        self.assertIsNone(prepare_hierarchy(self.index, None))


if __name__ == '__main__':
    unittest.main()
