"""Tiny release fixtures: generic lexical coverage, approved art, and isolation."""
from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import build_archetypes as archetypes


class ArchetypeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / 'catalog.sqlite'
        self.empty_art = self.root / 'art-config.json'
        self.empty_art.write_text('{"version":1,"artifacts":[]}')
        with closing(sqlite3.connect(self.database)) as db:
            db.executescript('''
                CREATE TABLE nouns(id TEXT PRIMARY KEY,label TEXT,domain TEXT,definition TEXT,frequency REAL,guide_count INTEGER);
                CREATE TABLE noun_aliases(noun_id TEXT,alias TEXT,normalized TEXT,rank INTEGER);
                CREATE TABLE noun_edges(parent_id TEXT,child_id TEXT,relation TEXT);
                CREATE TABLE items(id TEXT PRIMARY KEY,title TEXT,category TEXT,kind TEXT,device TEXT);
                CREATE TABLE guide_nouns(guide_id TEXT,noun_id TEXT,confidence REAL,reason TEXT);
                CREATE TABLE models(name TEXT,noun_id TEXT,guide_count INTEGER);
            ''')
            db.executemany('INSERT INTO nouns VALUES(?,?,?,?,?,?)', [
                ('barrow', 'wheelbarrow', 'artifact', 'A small hand vehicle.', 3.2, 2),
                ('wheel', 'wheel', 'artifact', 'A circular assembly.', 5.0, 0),
                ('handle', 'handle', 'artifact', 'A component of a vehicle used as a grip.', 5.0, 0),
                ('arm', 'arm', 'body', 'A body part.', 5.5, 2),
                ('bone', 'bone', 'body', 'A skeletal part.', 5.0, 0),
                ('community', 'community', 'group', 'A social group.', 6.0, 0),
                ('telephone', 'telephone', 'artifact', 'A communication device.', 4.0, 1),
                ('phoneme', 'phone', 'communication', 'A speech sound.', 5.0, 100),
            ])
            aliases = [('barrow', 'wheelbarrow'), ('barrow', 'barrow'), ('wheel', 'wheel'),
                       ('wheel', 'wheels'), ('handle', 'handle'), ('handle', 'grip'),
                       ('arm', 'arm'), ('bone', 'bone'), ('community', 'community'),
                       ('telephone', 'telephone'), ('telephone', 'phone'), ('phoneme', 'phone')]
            db.executemany('INSERT INTO noun_aliases VALUES(?,?,?,0)', [(noun, label, label) for noun, label in aliases])
            db.execute("UPDATE noun_aliases SET rank=1 WHERE noun_id='phoneme'")
            db.executemany('INSERT INTO noun_edges VALUES(?,?,?)', [('barrow', 'wheel', 'has-part'), ('arm', 'bone', 'has-part')])
            db.executemany('INSERT INTO items VALUES(?,?,?,?,?)', [
                ('one', 'Wheelbarrow wheel and handle replacement', 'handyman', 'guide', 'Wheelbarrow'),
                ('two', 'Fix a loose wheelbarrow handle', 'handyman', 'guide', 'Wheelbarrow'),
                ('three', 'Arm bone injury', 'health', 'article', ''),
                ('four', 'First aid for arm fractures', 'health', 'guide', ''),
                ('five', 'Telephone screen repair', 'technology', 'guide', 'Phone SM-G991B'),
            ])
            db.executemany('INSERT INTO guide_nouns VALUES(?,?,?,?)', [(item, noun, 1.0, 'fixture') for item, noun in [
                ('one', 'barrow'), ('two', 'barrow'), ('three', 'arm'), ('four', 'arm'), ('five', 'telephone')]])
            db.execute('INSERT INTO models VALUES(?,?,?)', ('Phone SM-G991B', 'telephone', 1))
            db.commit()

    def test_every_filled_noun_uses_generic_parts_not_a_subject_whitelist(self):
        payload, art = archetypes.compile_registry(self.database, 'fixture', self.empty_art)
        rows = {row['id']: row for row in payload['archetypes']}
        self.assertEqual(set(rows), {'barrow', 'arm', 'telephone', 'phoneme'})
        self.assertNotIn('community', rows)
        self.assertEqual(art, {})
        self.assertEqual([source['id'] for source in payload['sources']], ['oewn', 'wordfreq'])
        self.assertEqual(payload['sources'][0]['license'], 'CC BY 4.0')
        self.assertEqual(payload['sources'][1]['version'], '3.1.1')
        self.assertEqual(len(payload['sources'][0]['sha256']), 64)
        self.assertEqual(rows['barrow']['guideCount'], 2)
        self.assertEqual(rows['telephone']['aliasRanks']['phone'], 0)
        self.assertEqual(rows['phoneme']['aliasRanks']['phone'], 1)
        self.assertEqual({part['query'] for part in rows['barrow']['parts']}, {'wheel', 'handle'})
        self.assertEqual(rows['arm']['parts'][0]['query'], 'bone')
        self.assertIsNone(rows['telephone']['artUrl'])
        self.assertEqual(payload['models'], [{'name': 'Phone SM-G991B', 'nounId': 'telephone'}])
        self.assertEqual(payload, archetypes.compile_registry(self.database, 'fixture', self.empty_art)[0])

    def test_alias_bounds_do_not_drop_an_entire_valid_archetype(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute('INSERT INTO noun_aliases VALUES(?,?,?,0)', ('barrow', 'x' * 200, 'x' * 200))
            db.execute('INSERT INTO noun_aliases VALUES(?,?,?,0)', ('wheel', 'y' * 150, 'y' * 150))
            db.commit()
        payload, _ = archetypes.compile_registry(self.database, 'fixture', self.empty_art)
        row = next(row for row in payload['archetypes'] if row['id'] == 'barrow')
        self.assertIn('wheelbarrow', row['aliases'])
        self.assertTrue(all(len(alias) <= 160 for alias in row['aliases']))
        self.assertTrue(all(len(alias) <= 120 for part in row['parts'] for alias in part['aliases']))

    def test_incidental_nouns_single_letters_and_body_directions_are_not_artifact_parts(self):
        with closing(sqlite3.connect(self.database)) as db:
            extra = [('newspaper', 'newspaper', 'artifact', 'A daily publication.', 5.0, 0),
                     ('miniskirt', 'miniskirt', 'artifact', 'A very short skirt.', 4.0, 0),
                     ('letter', 'A', 'artifact', 'A component of a mechanism.', 6.0, 0),
                     ('rear', 'rear', 'body', 'A body part.', 6.0, 0),
                     ('change', 'change', 'artifact', 'Small coins.', 6.0, 0)]
            db.executemany('INSERT INTO nouns VALUES(?,?,?,?,?,?)', extra)
            db.executemany('INSERT INTO noun_aliases VALUES(?,?,?,0)', [(row[0], row[1], row[1].lower()) for row in extra])
            for number in range(2):
                for identifier, label, *_ in extra:
                    item = identifier + str(number)
                    db.execute('INSERT INTO items VALUES(?,?,?,?,?)', (item, 'Wheelbarrow ' + label + ' replacement', 'handyman', 'guide', 'Wheelbarrow'))
                    db.execute('INSERT INTO guide_nouns VALUES(?,?,?,?)', (item, 'barrow', 1.0, 'fixture'))
            db.commit()
        payload, _ = archetypes.compile_registry(self.database, 'fixture', self.empty_art)
        row = next(row for row in payload['archetypes'] if row['id'] == 'barrow')
        self.assertEqual({part['query'] for part in row['parts']}, {'wheel', 'handle'})

    def test_physical_parent_meronyms_are_inherited_for_any_subject(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.executemany('INSERT INTO nouns VALUES(?,?,?,?,?,?)', [
                ('vehicle', 'hand vehicle', 'artifact', 'A vehicle moved by hand.', 4.0, 0),
                ('axle', 'axle', 'artifact', 'A shaft.', 4.0, 0)])
            db.execute('INSERT INTO noun_aliases VALUES(?,?,?,0)', ('axle', 'axle', 'axle'))
            db.executemany('INSERT INTO noun_edges VALUES(?,?,?)', [('vehicle', 'barrow', 'is-a'), ('vehicle', 'axle', 'has-part')])
            db.commit()
        payload, _ = archetypes.compile_registry(self.database, 'fixture', self.empty_art)
        row = next(row for row in payload['archetypes'] if row['id'] == 'barrow')
        self.assertIn('axle', {part['query'] for part in row['parts']})

    def test_phone_art_uses_physical_lexical_alias_and_exact_reviewed_bytes(self):
        approved = json.loads(archetypes.APPROVED_ART.read_bytes())
        approved['artifacts'] = [row for row in approved['artifacts'] if row['id'] == 'phone']
        config = self.root / 'approved.json'
        config.write_text(json.dumps(approved))
        payload, bodies = archetypes.compile_registry(self.database, 'fixture', config)
        rows = {row['id']: row for row in payload['archetypes']}
        self.assertIsNone(rows['phoneme']['artUrl'], 'High guide count must not attach phone art to a speech sense')
        self.assertTrue(rows['telephone']['artUrl'].startswith('/api/art/'))
        digest, body = next(iter(bodies.items()))
        self.assertEqual(hashlib.sha256(body).hexdigest(), digest)
        art = json.loads(body)
        self.assertEqual(art['id'], 'telephone')
        self.assertEqual(set(art['parts']), {'speaker', 'buttons', 'screen'})
        self.assertNotIn('camera', art['parts'], 'Rejected pilot geometry must stay absent')
        self.assertEqual(rows['telephone']['attribution'], art['attribution'])
        self.assertEqual(rows['telephone']['aliasRanks']['phone'], -1)
        self.assertIn('camera', {part['id'] for part in rows['telephone']['parts']})

    def test_release_registry_credit_and_art_hashes_agree_without_source_database(self):
        runtime = self.root / 'runtime'
        runtime.mkdir()
        index = {'version': 1, 'revision': 'fixture', 'items': [],
                 'search': {'version': 1, 'inverted': {'stale': True}}, 'searchIndex': {'stale': True}}
        (runtime / 'index.json').write_text(json.dumps(index))
        (runtime / 'manifest.json').write_text(json.dumps({'runtimeComplete': True, 'revision': 'fixture'}))
        metadata = archetypes.prepare(runtime, self.database, self.empty_art)
        resident = json.loads((runtime / 'archetypes.json').read_bytes())
        rebuilt = json.loads((runtime / 'index.json').read_bytes())
        self.assertEqual(rebuilt['archetypes'], resident)
        self.assertNotIn('inverted', rebuilt['search'])
        self.assertNotIn('searchIndex', rebuilt)
        self.assertEqual(metadata['sha256'], hashlib.sha256((runtime / 'archetypes.json').read_bytes()).hexdigest())
        self.assertEqual(gzip.decompress((runtime / 'archetypes.json.gz').read_bytes()), (runtime / 'archetypes.json').read_bytes())
        manifest = json.loads((runtime / 'manifest.json').read_bytes())
        credits = (runtime / 'diagram-credits.html').read_bytes()
        self.assertEqual(manifest['diagramCredits'], {'bytes': len(credits), 'sha256': hashlib.sha256(credits).hexdigest()})
        self.assertEqual(metadata['count'], 4)
        self.assertEqual(metadata['models'], 1)
        self.assertEqual(metadata['artFiles'], 0)
        self.assertEqual(len(metadata['linksSha256']), 64)
        self.assertFalse((runtime / 'catalog.sqlite').exists())
        self.assertFalse((runtime / 'gaps.json').exists())
        self.assertEqual((runtime / 'archetypes.json').stat().st_mode & 0o777, 0o644)
        self.assertEqual(runtime.stat().st_mode & 0o777, 0o755)


if __name__ == '__main__':
    unittest.main()
