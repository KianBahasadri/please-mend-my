#!/usr/bin/env python3
"""Build a local, ungated gallery from cached diagram metadata and PNG evidence.

No catalog, SVG renderer, source provider or AI is invoked. Rejected proposals
are deliberately visible here; this gallery grants no approval or publication.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import tempfile
import os
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PNG = b'\x89PNG\r\n\x1a\n'
PAGE_SIZE = 25
MAX_IMAGE_BYTES = 2_000_000

STYLE = """html{color-scheme:dark light}*{box-sizing:border-box}body{margin:0;background:#080808;color:#ededed;font:15px/1.55 system-ui,sans-serif}main{max-width:1450px;margin:auto;padding:36px 24px 80px}a{color:inherit;text-underline-offset:3px}h1{font-size:clamp(2rem,5vw,3.5rem);line-height:1.1;letter-spacing:-.04em}h2{font-size:1.7rem;margin:0}h3{margin:.4rem 0}.muted,small{color:#a0a0a0}.intro{max-width:850px}.nav{display:flex;gap:16px;flex-wrap:wrap;margin:24px 0}.subject{border-top:1px solid #333;margin:36px 0;padding-top:24px}.candidate{border:1px solid #333;border-radius:10px;padding:18px;margin:18px 0}.tag{display:inline-block;border:1px solid #555;border-radius:30px;padding:2px 10px;font-size:12px}.images{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,270px),1fr));gap:16px}figure{margin:12px 0;min-width:0}figure img{display:block;width:100%;height:260px;object-fit:contain;background:#171717;border-radius:6px}figcaption{font-size:12px;margin-top:5px}details{margin:12px 0}summary{cursor:pointer}p{overflow-wrap:anywhere}.credit{font-size:12px}ul{padding-left:24px}@media(prefers-color-scheme:light){body{background:#f7f7f7;color:#171717}.muted,small{color:#666}.subject,.candidate{border-color:#ccc}.tag{border-color:#999}}"""


def escaped(value):
    return html.escape(str(value or ''), quote=True)


def read(path, default=None):
    try:
        return json.loads(Path(path).read_bytes())
    except FileNotFoundError:
        return {} if default is None else default


def write(path, body):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(body)
        Path(temporary).chmod(0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def href(value):
    value = str(value or '')
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {'http', 'https'} and parsed.hostname and not parsed.username and not parsed.password:
            return escaped(value)
    except ValueError:
        pass
    return ''


def link(url, label):
    target = href(url)
    return ('<a href="' + target + '" target="_blank" rel="noopener noreferrer">' + escaped(label) + '</a>') if target else escaped(label)


def image_copy(value, batch, output, expected=None):
    path = Path(value)
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(batch.resolve()) or path.suffix.lower() != '.png':
        return None
    try:
        with path.open('rb') as stream:
            body = stream.read(MAX_IMAGE_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(body) > MAX_IMAGE_BYTES or not body.startswith(PNG):
        return None
    sha = hashlib.sha256(body).hexdigest()
    if expected and sha != expected:
        return None
    name = sha + '.png'
    if not (output / name).exists():
        write(output / name, body)
    return name


def figure(name, caption):
    return ('<figure><a href="' + name + '" target="_blank" rel="noopener noreferrer">'
            '<img src="' + name + '" alt="' + escaped(caption) + '" loading="lazy" decoding="async"></a>'
            '<figcaption>' + escaped(caption) + ' · click for full size</figcaption></figure>')


def page(title, content):
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + escaped(title) + '</title><style>' + STYLE + '</style><main>' + content + '</main></html>').encode()


def batch_sections(batch, output):
    selection = read(batch / 'selection.json').get('archetypes', [])
    taxonomy = {row['id']: row for row in read(batch / 'taxonomy.json').get('archetypes', [])}
    reports = {row['id']: row for row in read(batch / 'report.json').get('archetypes', [])}
    manual = {(row['id'], row['viewId']): row for row in read(batch / 'visual-review.json').get('entries', [])}
    digests = {phase: read(batch / ('digests-' + phase + '.json')).get('views', {}) for phase in ('primary', 'secondary')}
    discovery = {phase: read(batch / ('discovery-' + phase + '.json')).get('views', {}) for phase in ('primary', 'secondary')}
    sections, counts = [], {'subjects': len(selection), 'candidates': 0, 'images': 0, 'compiledViews': 0}
    for baseline in selection:
        identifier, label = baseline['id'], baseline['label']
        subject = taxonomy.get(identifier, baseline)
        report = reports.get(identifier, {})
        body = '<article class="subject" id="' + escaped(identifier) + '"><h2>' + escaped(label) + '</h2>'
        body += '<p class="muted">' + escaped(baseline.get('definition', '')) + '</p>'
        if subject.get('taxonomyReason') or subject.get('reason'):
            body += '<details><summary>Taxonomy notes</summary><p>' + escaped(subject.get('taxonomyReason', subject.get('reason'))) + '</p></details>'
        any_view = False
        for view in report.get('views', []):
            preview_dir = Path(view.get('previewDir', str(batch / 'compiled' / identifier / view['id'])))
            images = []
            for filename, caption in [('light.png', 'Compiled light view'), ('dark.png', 'Compiled dark view'), ('highlights.png', 'Proposed part mappings')]:
                copied = image_copy(preview_dir / filename, batch, output)
                if copied:
                    images.append(figure(copied, caption))
            if images:
                any_view = True
                review = manual.get((identifier, view['id']), {})
                status = review.get('status', view.get('status', 'unreviewed'))
                body += '<div class="candidate"><h3>' + escaped(view.get('label', view['id'])) + ' · compiled mapping <span class="tag">' + escaped(status) + '</span></h3>'
                body += '<p>Mapped parts: ' + escaped(', '.join(view.get('mappedParts', []))) + '</p>'
                for issue in review.get('issues', []):
                    body += '<p class="muted">' + escaped(issue) + '</p>'
                body += '<div class="images">' + ''.join(images) + '</div></div>'
                counts['compiledViews'] += 1
                counts['images'] += len(images)
        for phase in ('primary', 'secondary'):
            keys = sorted(key for key in set(digests[phase]) | set(discovery[phase]) if key.startswith(identifier + '/'))
            for key in keys:
                digest = digests[phase].get(key, {})
                accepted = {candidate['source']['id']: candidate for candidate in digest.get('candidates', [])}
                rejected = {row.get('id'): row.get('reason', '') for row in digest.get('rejected', [])}
                sources = {row['id']: row for row in discovery[phase].get(key, {}).get('candidates', [])}
                sources.update({sid: value['source'] for sid, value in accepted.items()})
                for sid, source in sorted(sources.items()):
                    any_view = True
                    counts['candidates'] += 1
                    candidate = accepted.get(sid, {})
                    title = source.get('title', sid)
                    body += '<div class="candidate"><h3>' + escaped(title) + '</h3><small>' + escaped(phase + ' / ' + key.split('/', 1)[1]) + '</small>'
                    if sid in rejected:
                        body += '<p><span class="tag">geometry rejected</span> ' + escaped(rejected[sid]) + '</p>'
                    else:
                        body += '<p><span class="tag">source candidate · unapproved</span></p>'
                    images, seen = [], set()
                    evidence = list(candidate.get('images', []))
                    directory = batch / 'digests' / identifier / key.split('/', 1)[1] / sid
                    if not candidate:
                        evidence += [{'path': str(directory / filename), 'id': caption} for filename, caption in [('original.png', 'Original colors'), ('monochrome.png', 'Monochrome')]]
                    for evidence_image in evidence:
                        copied = image_copy(evidence_image['path'], batch, output, evidence_image.get('sha256'))
                        if copied and copied not in seen:
                            seen.add(copied)
                            caption = str(evidence_image.get('id', 'Source preview')).removeprefix(sid + ' ')
                            images.append(figure(copied, caption))
                    body += '<div class="images">' + ''.join(images) + '</div>' if images else '<p class="muted">No sanitized preview was generated for this source.</p>'
                    body += '<p class="credit">' + link(source.get('source', source.get('sourceUrl')), title) + ' · ' + escaped(source.get('author', '')) + ' · ' + link(source.get('licenseUrl'), source.get('license', 'License metadata unavailable')) + '</p></div>'
                    counts['images'] += len(images)
        if not any_view:
            body += '<p class="muted">No diagram candidates were retained for this subject.</p>'
        body += '</article>'
        sections.append(body)
    return sections, counts


def build(batches, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(0o755)
    summaries, generated = [], []
    for batch in batches:
        batch = Path(batch).resolve()
        if not (batch / 'selection.json').exists():
            continue
        label = batch.name
        if not label.startswith('diagram-batch-') or not label.removeprefix('diagram-batch-').isdigit():
            raise ValueError('Batch directory must be diagram-batch-<number>')
        sections, counts = batch_sections(batch, output)
        pages = max(1, (len(sections) + PAGE_SIZE - 1) // PAGE_SIZE)
        prefix = label.removeprefix('diagram-')
        names = [prefix + '-' + str(number) + '.html' for number in range(1, pages + 1)]
        nav = '<nav class="nav"><a href="./">All batches</a>' + ''.join('<a href="' + name + '">Page ' + str(at) + '</a>' for at, name in enumerate(names, 1)) + '</nav>'
        for at, name in enumerate(names):
            content = '<h1>' + escaped(label) + '</h1><p class="intro muted">Local candidate preview. Rejected and unreviewed proposals are shown for inspection.</p>' + nav
            content += ''.join(sections[at * PAGE_SIZE:(at + 1) * PAGE_SIZE]) + nav
            write(output / name, page(label, content))
            generated.append(name)
        summaries.append({'batch': label, **counts, 'pages': names})
    content = '<h1>Diagram candidates</h1><p class="intro">All cached source candidates and available part-mapping sheets, including rejected proposals. Images open at full size. These are local experiments.</p>'
    for summary in summaries:
        content += '<section class="candidate"><h2><a href="' + summary['pages'][0] + '">' + escaped(summary['batch']) + '</a></h2><p>'
        content += escaped(str(summary['subjects']) + ' subjects · ' + str(summary['candidates']) + ' candidate placements · ' + str(summary['compiledViews']) + ' compiled mappings') + '</p></section>'
    write(output / 'index.html', page('Diagram candidates', content))
    manifest = {'version': 1, 'batches': summaries, 'pages': ['index.html', *generated]}
    write(output / 'manifest.json', (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode())
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch-dir', type=Path, action='append')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'catalog-data/build/diagram-preview')
    args = parser.parse_args()
    batches = args.batch_dir or [ROOT / 'catalog-data/build/diagram-batch-10', ROOT / 'catalog-data/build/diagram-batch-300']
    print(json.dumps(build(batches, args.output_dir), ensure_ascii=False))


if __name__ == '__main__':
    main()
