#!/usr/bin/env python3
"""Resume catalog-wide diagram work in bounded, disposable geometry windows.

All filled physical noun senses are considered. Exact paid-response caches and
licensed source pins are shared across windows; only regenerable preview files
are discarded. Available safe drawings, including context-only art, are kept
without visual approval. This command never changes the selected release.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import time

import diagram_ai as ai
import diagram_batch as batch
from diagram_svg import canonical, compile_art, digest_bytes, dimensions, flatten, parse_source
from prepare_candidate_art import export_batches

VERSION = 'diagram-rollout-v1'
ROOT = Path(__file__).resolve().parents[1]


def disk_gate(directory, reserve_gib):
    available = shutil.disk_usage(directory).free
    if available < reserve_gib * 1024 ** 3:
        raise RuntimeError(f'Disk reserve reached ({available / 1024**3:.2f} GiB free); '
                           'completed caches and windows are safe to resume')


def import_cache(work, previous):
    """Hardlink immutable paid objects/source responses; never copy a corpus."""
    imported = 0
    folders = ('cache/taxonomy', 'passes/primary/cache/mapping',
               'passes/secondary/cache/mapping', 'discovery-cache/commons',
               'discovery-cache/openclipart', 'discovery-cache/downloads',
               'sources', 'source-metadata', 'discovery')
    for old in previous:
        for name in folders:
            target = work / name
            target.mkdir(parents=True, exist_ok=True)
            for original in sorted((Path(old) / name).glob('*')):
                if not original.is_file() or original.is_symlink():
                    continue
                destination = target / original.name
                if destination.exists():
                    continue
                try:
                    os.link(original, destination)
                except OSError:
                    shutil.copyfile(original, destination)
                imported += 1
    return imported


def share_caches(window, work):
    for name in ('cache', 'passes/primary/cache', 'passes/secondary/cache',
                 'discovery-cache', 'discovery', 'sources', 'source-metadata'):
        destination, source = window / name, work / name
        source.mkdir(parents=True, exist_ok=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            destination.symlink_to(source.resolve(), target_is_directory=True)
        elif destination.resolve() != source.resolve():
            raise ValueError('Window cache belongs to a different rollout')


def compile_available(taxonomy, discovery, digests, receipt, work, *, phase='primary', previous=None):
    """Keep safe source geometry even if its optional part map is absent/bad."""
    targets = {batch.target_key(row): row for row in batch.view_targets(taxonomy, phase, previous)}
    mappings = {row['id']: row for row in receipt['entries']}
    entries = {row['id']: {'id': row['id'], 'label': row['label'],
                'parts': row['parts'], 'views': [], 'status': 'list-only'}
               for row in taxonomy['archetypes']}
    for key, target in targets.items():
        candidates = discovery['views'].get(key, {}).get('candidates', [])
        proposed = mappings.get(key, {}).get('content', {})
        ordered = sorted(candidates, key=lambda row: row['id'] != proposed.get('selectedSource'))
        for source in ordered:
            try:
                tree = parse_source(source['file'], source['sha256'])
                leaves, _ = flatten(tree)
                known = {row['key'] for row in leaves}
                known.update(parent['key'] for row in leaves for parent in row['ancestors'])
                digest = {'viewBox': dimensions(tree), 'nodes': [{'key': value} for value in sorted(known)]}
                mapping = proposed if proposed.get('selectedSource') == source['id'] else {
                    'selectedSource': source['id'], 'groupMap': []}
                try:
                    art = compile_art(target, source, mapping, digest, leaves, minimum_parts=0)
                except (ValueError, KeyError, TypeError):
                    mapping = {'selectedSource': source['id'], 'groupMap': []}
                    art = compile_art(target, source, mapping, digest, leaves, minimum_parts=0)
                directory = work / 'compiled' / target['id'] / target['viewId']
                body = canonical(art) + b'\n'
                ai.atomic(directory / 'art.json', body)
                view = {'id': target['viewId'], 'label': target['viewLabel'],
                        'status': 'needs-visual-review', 'qualityApprovalRequired': False,
                        'mappedParts': sorted(art['parts']), 'source': source,
                        'sourceSha256': source['sha256'], 'mappingSha256': digest_bytes(canonical(mapping)),
                        'artSha256': digest_bytes(body), 'artPath': str(directory / 'art.json'),
                        'contextOnly': not bool(art['parts'])}
                entries[target['id']]['views'].append(view)
                entries[target['id']]['status'] = 'needs-visual-review'
                break
            except (OSError, ValueError, KeyError, TypeError):
                continue
    result = {'version': VERSION, 'phase': phase, 'archetypes': list(entries.values())}
    ai.save(work / ('compiled-' + phase + '-report.json'), result)
    return result


def discard_previews(window):
    """Never remove source pins, paid results, mappings, reports or compiled art."""
    directory = window / 'digests'
    if directory.is_dir() and not directory.is_symlink():
        shutil.rmtree(directory)


def response_objects(work):
    return {str(path.relative_to(work)): path for folder in
            ('cache/taxonomy', 'passes/primary/cache/mapping', 'passes/secondary/cache/mapping')
            for path in (work / folder).glob('*.json')}


def summarize(work, selection, windows, started):
    baseline = set(batch.read(work / 'cache-baseline.json')['objects'])
    current = response_objects(work)
    paid = [batch.read(path) for name, path in current.items() if name not in baseline]
    accepted_cost = sum(row.get('usage', {}).get('cost', 0) for row in paid)
    receipts = {}
    for directory in (work, *windows, *[window / 'passes' / phase for window in windows
                                     for phase in ('primary', 'secondary')]):
        for path in (*directory.glob('*receipt*.json'), *(directory / 'receipts').glob('*.json')):
            receipt = batch.read(path)
            if receipt.get('stage') in {'taxonomy', 'mapping'} and isinstance(receipt.get('entries'), list):
                receipts[receipt.get('sourceReceiptSha256', digest_bytes(canonical(receipt)))] = receipt
    observed = {row['cacheKey'] for receipt in receipts.values() for row in receipt['entries']
                if row.get('apiCalls')}
    calls = sum(receipt.get('apiCalls', 0) for receipt in receipts.values())
    calls += sum(row.get('cacheKey') not in observed for row in paid)
    failed_cost = sum(row.get('failedUsage', {}).get('cost', 0)
                      for receipt in receipts.values() for row in receipt['entries'])
    completed = [batch.read(path / 'done.json') for path in windows if (path / 'done.json').is_file()]
    result = {'version': VERSION, 'selected': selection['count'], 'windows': len(windows),
              'completedWindows': len(completed),
              'completedSubjects': sum(row['subjects'] for row in completed),
              'subjectsWithArt': sum(row['subjectsWithArt'] for row in completed),
              'newAcceptedResponses': len(paid), 'reportedAcceptedCost': round(accepted_cost, 9),
              'knownApiCalls': calls, 'reportedFailedCost': round(failed_cost, 9),
              'reportedCost': round(accepted_cost + failed_cost, 9),
              'costLimitation': 'Provider-reported usage; failures without usage may have additional charges.',
              'secondsThisRun': round(time.monotonic() - started, 3),
              'complete': len(completed) == len(windows), 'published': False}
    ai.save(work / 'progress.json', result)
    batch.progress('rollout-progress', **result)
    return result


def run(args):
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with ai.lock(work / '.rollout.lock'):
        disk_gate(work, args.min_free_gib)
        imported = import_cache(work, args.reuse_batch)
        baseline = work / 'cache-baseline.json'
        if not baseline.exists():
            ai.save(baseline, {'objects': sorted(response_objects(work))})
        selection = batch.prepare_selection(args.runtime_dir, args.database, work, None, all_physical=True)
        key = None if args.offline else ai.load_api_key(env_file=args.env_file)
        if not args.offline and not key:
            raise ValueError('OpenRouter API key is missing; no API requests started')
        batch.progress('rollout-start', subjects=selection['count'], importedObjects=imported,
                       chunkSize=args.chunk_size, concurrency=args.concurrency)
        taxonomy = batch.run_taxonomy(selection, work, api_key=key, concurrency=args.concurrency, model=args.model)
        windows = []
        for start in range(0, len(taxonomy['archetypes']), args.chunk_size):
            rows = taxonomy['archetypes'][start:start + args.chunk_size]
            identity = digest_bytes(canonical({'version': VERSION, 'model': args.model, 'rows': rows}))
            window = work / 'windows' / (str(start).zfill(5) + '-' + identity[:12])
            windows.append(window)
        summarize(work, selection, windows, started)
        for number, window in enumerate(windows):
            if (window / 'done.json').is_file():
                continue
            disk_gate(work, args.min_free_gib + .5)
            window.mkdir(parents=True, exist_ok=True)
            share_caches(window, work)
            subjects = taxonomy['archetypes'][number * args.chunk_size:(number + 1) * args.chunk_size]
            current = {'version': VERSION, 'revision': taxonomy.get('revision'),
                       'allowSparseViews': True, 'archetypes': subjects}
            ai.save(window / 'taxonomy.json', current)
            previous = None
            with_art = set()
            for phase in ('primary', 'secondary'):
                discovery = batch.run_discovery(current, window, phase=phase, previous=previous,
                    workers=args.discovery_workers, offline=args.offline)
                disk_gate(work, args.min_free_gib)
                digests = batch.run_digests(current, discovery, window, phase=phase, previous=previous,
                    workers=args.geometry_workers)
                receipt = batch.run_mapping(current, digests, window, phase=phase, previous=previous,
                    api_key=key, concurrency=args.concurrency, model=args.model)
                compiled = compile_available(current, discovery, digests, receipt, window,
                                             phase=phase, previous=previous)
                with_art.update(row['id'] for row in compiled['archetypes'] if row['views'])
                if phase == 'primary':
                    previous = compiled
                discard_previews(window)
            # Portable pins/mappings are sufficient to reproduce art on EC2;
            # exported JSON contains neither source SVGs nor article content.
            exported = export_batches([window], window / 'candidate-sources.json')
            ai.save(window / 'done.json', {'subjects': len(subjects), 'subjectsWithArt': len(with_art),
                'candidatePlacements': exported['candidates'], 'qualityApprovalRequired': False})
            summarize(work, selection, windows, started)
            if args.max_windows and number + 1 >= args.max_windows:
                break
        exported = export_batches([path for path in windows if (path / 'done.json').is_file()],
                                  work / 'candidate-sources.json')
        result = summarize(work, selection, windows, started)
        result['portablePins'] = exported
        ai.save(work / 'progress.json', result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir', type=Path, required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--reuse-batch', type=Path, action='append', default=[])
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--concurrency', type=int, default=128)
    parser.add_argument('--chunk-size', type=int, default=128)
    parser.add_argument('--geometry-workers', type=int, choices=[1, 2], default=2)
    parser.add_argument('--discovery-workers', type=int, default=16)
    parser.add_argument('--min-free-gib', type=float, default=2)
    parser.add_argument('--max-windows', type=int, default=0)
    parser.add_argument('--model', default=ai.DEFAULT_MODEL)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    if (not 1 <= args.concurrency <= 128 or not 1 <= args.chunk_size <= 256
            or not 1 <= args.discovery_workers <= 32 or args.min_free_gib < 1
            or args.max_windows < 0):
        parser.error('Use concurrency1..128, chunks1..256, discovery1..32, and at least1GiB disk reserve')
    try:
        run(args)
    except RuntimeError as error:
        parser.exit(75, str(error) + '\n')


if __name__ == '__main__':
    main()
