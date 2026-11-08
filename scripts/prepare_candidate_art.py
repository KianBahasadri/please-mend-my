#!/usr/bin/env python3
"""Replay pinned diagram candidates without a visual-quality publication gate.

The small source/mapping configuration is portable; SVGs and compiled ASTs remain
outside Git. Fresh hosts download only SHA-pinned originals at build time.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import tempfile
import os

from diagram_discovery import license_from_metadata, request_bytes
from diagram_svg import (VERSION as COMPILER_VERSION, canonical, compile_art,
                         dimensions, flatten, inspect_svg, parse_source, validate_art)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'scripts/data/candidate-sources.json'
VERSION = 'candidate-art-v1'
SHA = re.compile(r'[a-f0-9]{64}\Z')
SLUG = re.compile(r'[a-z][a-z0-9-]{0,99}\Z')


def read(path):
    return json.loads(Path(path).read_bytes())


def atomic(path, body):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body)
        Path(temporary).chmod(0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def fingerprint(config=CONFIG):
    result = hashlib.sha256()
    for path in (Path(config), Path(__file__), ROOT / 'scripts/diagram_svg.py', ROOT / 'scripts/diagram_discovery.py'):
        result.update(path.read_bytes()); result.update(b'\0')
    return result.hexdigest()


def portable_source(source):
    fields = ('id', 'title', 'author', 'source', 'license', 'licenseUrl', 'sha256',
              'bytes', 'download', 'revision', 'copyright', 'disclaimer')
    result = {key: source[key] for key in fields if key in source}
    result.setdefault('download', source.get('downloadUrl', ''))
    if not SHA.fullmatch(result.get('sha256', '')):
        raise ValueError('Candidate source needs an exact SHA-256 pin')
    license_from_metadata({'LicenseShortName': {'value': result['license']},
                           'LicenseUrl': {'value': result['licenseUrl']}})
    return result


def export_batches(batches, config=CONFIG):
    """Export every cached source placement, never SVG/AST coordinates or bodies."""
    subjects = defaultdict(dict)
    for batch in sorted(map(Path, batches)):
        taxonomy = {row['id']: row for row in read(batch / 'taxonomy.json')['archetypes']}
        for phase in ('primary', 'secondary'):
            discovery_path = batch / ('discovery-' + phase + '.json')
            if not discovery_path.exists():
                continue
            discovery = read(discovery_path)['views']
            mapping_path = batch / ('mapping-' + phase + '-receipt.json')
            mappings = {row['id']: row.get('content', {}) for row in read(mapping_path).get('entries', [])} if mapping_path.exists() else {}
            for view, row in sorted(discovery.items()):
                noun = view.split('/', 1)[0]
                subject = taxonomy.get(noun)
                if subject is None:
                    continue
                parts = subject.get('parts') or subject.get('baselineParts', [])
                parts = [{key: value[key] for key in ('id', 'label', 'query', 'aliases')}
                         for value in parts if all(key in value for key in ('id', 'label', 'query', 'aliases'))]
                for source in row.get('candidates', []):
                    pin = portable_source(source)
                    mapping = mappings.get(view, {})
                    assignments = mapping.get('groupMap', []) if mapping.get('selectedSource') == pin['id'] else []
                    # Legacy/current mapping receipts have already converted mark
                    # numbers into pinned tree paths. Unknown paths fail to context.
                    assignments = [{key: value[key] for key in ('node', 'part', 'confidence')}
                                   for value in assignments if all(key in value for key in ('node', 'part', 'confidence'))]
                    item = {'source': pin, 'parts': parts, 'mapping': assignments,
                            'batch': batch.name, 'view': view.split('/', 1)[1],
                            'phase': phase, 'label': subject['label']}
                    previous = subjects[noun].get(pin['sha256'])
                    if previous is None or len(assignments) > len(previous['mapping']):
                        subjects[noun][pin['sha256']] = item
    result = {'version': 1, 'subjects': []}
    for noun, choices in sorted(subjects.items()):
        # Existing mapped drawings are useful even when their visual review was
        # poor. Otherwise prefer a source title naming this noun, then primary.
        def priority(item):
            label = item['label'].lower()
            return (-len(item['mapping']), -int(label in item['source']['title'].lower()),
                    item['phase'] != 'primary', item['source']['sha256'])
        result['subjects'].append({'id': noun, 'candidates': sorted(choices.values(), key=priority)})
    atomic(config, canonical(result) + b'\n')
    return {'subjects': len(result['subjects']), 'candidates': sum(len(row['candidates']) for row in result['subjects']),
            'bytes': Path(config).stat().st_size}


def source_file(source, directory, source_caches, offline):
    path = directory / 'sources' / (source['sha256'] + '.svg')
    if path.exists():
        body = path.read_bytes()
    else:
        body = None
        for cache in source_caches:
            for name in (source['sha256'] + '.original.svg', source['sha256'] + '.svg'):
                candidate = Path(cache) / name
                if candidate.is_file():
                    body = candidate.read_bytes(); break
            if body is not None: break
        if body is None:
            if offline: raise ValueError('Pinned candidate source is unavailable offline')
            body = request_bytes(source['download'])
    if len(body) > 500_000 or hashlib.sha256(body).hexdigest() != source['sha256']:
        raise ValueError('Candidate source differs from its SHA pin or size bound')
    inspect_svg(body)
    if not path.exists(): atomic(path, body)
    return path


def prepare(config=CONFIG, directory=None, *, source_caches=(), offline=False):
    config, directory = Path(config), Path(directory or ROOT / 'catalog-data/build/candidate-art')
    directory.mkdir(parents=True, exist_ok=True)
    identity = fingerprint(config)
    manifest_path = directory / 'manifest.json'
    if manifest_path.exists():
        previous = read(manifest_path)
        if previous.get('fingerprint') == identity:
            # Validate only the bounded diagram store, never article bodies.
            for row in previous.get('artifacts', []):
                body = (directory / 'art' / (row['sha256'] + '.json')).read_bytes()
                if hashlib.sha256(body).hexdigest() != row['sha256']:
                    raise ValueError('Prepared candidate art hash changed')
                validate_art(json.loads(body))
            return previous
    payload = read(config)
    if payload.get('version') != 1 or not isinstance(payload.get('subjects'), list):
        raise ValueError('Candidate source configuration must be version 1')
    artifacts, rejected = [], []
    for subject in payload['subjects']:
        identifier = subject['id']
        if not SLUG.fullmatch(identifier): raise ValueError('Invalid candidate noun ID')
        for candidate in subject['candidates']:
            source = portable_source(candidate['source'])
            try:
                original = source_file(source, directory, source_caches, offline)
                root = parse_source(original, source['sha256'])
                leaves, _ = flatten(root)
                keys = {leaf['key'] for leaf in leaves}
                keys.update(ancestor['key'] for leaf in leaves for ancestor in leaf['ancestors'])
                digest = {'viewBox': dimensions(root), 'nodes': [{'key': key} for key in sorted(keys)]}
                parts = candidate['parts'][:12]
                mapping = {'selectedSource': source['id'], 'groupMap': candidate['mapping']}
                attempted_mapping_sha = hashlib.sha256(canonical(mapping)).hexdigest()
                context_fallback = False
                try:
                    art = compile_art({'id': identifier, 'parts': parts}, source, mapping, digest, leaves, minimum_parts=0)
                except ValueError:
                    # Broken or obsolete part assignments do not suppress an
                    # otherwise safe drawing. Keep its real geometry as context.
                    mapping = {'selectedSource': source['id'], 'groupMap': []}
                    context_fallback = True
                    art = compile_art({'id': identifier, 'parts': parts}, source, mapping, digest, leaves, minimum_parts=0)
                body = canonical(art) + b'\n'
                sha = hashlib.sha256(body).hexdigest()
                atomic(directory / 'art' / (sha + '.json'), body)
                artifacts.append({'id': identifier, 'sha256': sha, 'parts': parts,
                    'sourceSha256': source['sha256'], 'mappingSha256': hashlib.sha256(canonical(mapping)).hexdigest(),
                    'attemptedMappingSha256': attempted_mapping_sha, 'contextFallback': context_fallback,
                    'compilerVersion': COMPILER_VERSION, 'qualityApprovalRequired': False})
                break
            except (OSError, ValueError, KeyError, TypeError) as error:
                rejected.append({'id': identifier, 'sourceSha256': source['sha256'],
                                 'reason': type(error).__name__ + ': ' + str(error)[:300]})
    manifest = {'version': 1, 'fingerprint': identity, 'artifacts': artifacts,
                'rejected': rejected, 'qualityApprovalRequired': False}
    atomic(manifest_path, canonical(manifest) + b'\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'catalog-data/build/candidate-art')
    parser.add_argument('--export-batches', type=Path, nargs='+')
    parser.add_argument('--source-cache', type=Path, action='append')
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    if args.export_batches:
        print(json.dumps(export_batches(args.export_batches, args.config))); return
    caches = args.source_cache or sorted((ROOT / 'catalog-data/build').glob('diagram-batch-*/sources'))
    result = prepare(args.config, args.output_dir, source_caches=caches, offline=args.offline)
    print(json.dumps({'artifacts': len(result['artifacts']), 'rejected': len(result['rejected']),
                      'fingerprint': result['fingerprint']}), flush=True)


if __name__ == '__main__':
    main()
