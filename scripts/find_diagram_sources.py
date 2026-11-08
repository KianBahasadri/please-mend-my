#!/usr/bin/env python3
"""Discover, pin and recheck reusable SVG candidates for the eight-diagram pilot.

Wikimedia requests are deliberately serial and cached. Nothing here runs in the
website. `verify` never uses the network; `fetch` reproduces pinned source bytes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'docs/references/specifiers'
MANIFEST = DIRECTORY / 'sources.json'
CACHE = ROOT / 'catalog-data/build/diagram-sources'
API = 'https://commons.wikimedia.org/w/api.php'
SOURCE_VERSION = 'diagram-sources-v1'
USER_AGENT = 'PleaseMendMyDiagramPilot/1.0 (https://github.com/KianBahasadri/please-mend-my)'
QUERIES = {
    'arm': ['human arm bones labeled', 'upper limb anatomy diagram'],
    'laptop': ['intitle:laptop -connector -memory -share'],
    'phone': ['intitle:iphone -wordmark -worldwide -availability', 'intitle:smartphone -share -axis -logo'],
    'car': ['intitle:car -transmission -ignition -logo -sign -CARS -railway', 'car side view'],
    'stove': ['gas stove', 'kitchen oven'],
    'toilet': ['flush toilet diagram', 'toilet tank parts'],
    'refrigerator': ['intitle:fridge -icon -symbol', 'refrigerator appliance'],
    'jeans': ['intitle:jeans -logo -ABS -silhouette -signature', 'intitle:trousers -ABS -icon -kit'],
}
SHAPES = {'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon'}

TITLE_HINTS = {
    'arm': ['arm', 'upper limb', 'humerus', 'elbow', 'wrist', 'forearm'],
    'laptop': ['laptop', 'notebook', 'netbook', 'macbook', 'chromebook'],
    'phone': ['phone', 'smartphone', 'iphone', 'cellphone'],
    'car': ['car', 'sedan', 'hatchback', 'automobile'],
    'stove': ['stove', 'oven', 'range', 'cooktop'],
    'toilet': ['toilet', 'cistern', 'wc'],
    'refrigerator': ['refrigerator', 'fridge', 'freezer'],
    'jeans': ['jeans', 'denim', 'trousers', 'pants'],
}


def title_relevance(archetype, title):
    """Cheap discovery guard, not a claim that parts can be mapped visually."""
    title = str(title).casefold().replace('_', ' ')
    bad = r'\b(?:logo|wordmark|ribbon|flag|insignia|prohibition|sign|chart|graph|exports?|prices?|statistics|economy|comparison|book|code|sociology|scheme|schema|flowchart|life cycle)\b'
    if re.search(bad, title): return False
    if archetype != 'arm' and re.search(r'\b(?:woman|women|man|men|boy|girl|child|avatar|cartoon woman)\b', title): return False
    hints = TITLE_HINTS.get(archetype, [archetype.replace('-', ' ')])
    return any(re.search(r'\b' + re.escape(hint) + r'(?:\b|\d)', title) for hint in hints)


class PlainText(HTMLParser):
    def __init__(self): super().__init__(); self.parts = []
    def handle_data(self, text): self.parts.append(text)

def plain(value):
    parser = PlainText(); parser.feed(str(value or ''))
    return ' '.join(' '.join(parser.parts).split())

def canonical(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)

def request_bytes(url, maximum=4_000_000, attempts=3):
    for attempt in range(attempts):
        try:
            with urlopen(Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json,image/svg+xml;q=0.9,*/*;q=0.1'}), timeout=35) as response:
                body = response.read(maximum + 1)
                if len(body) > maximum: raise ValueError('Source exceeds configured byte limit')
                return body
        except HTTPError as error:
            code = error.code
            try: delay = min(30, max(0, float(error.headers.get('Retry-After', '0'))))
            except ValueError: delay = 0
            error.close()
            if code not in {429, 500, 502, 503, 504} or attempt + 1 == attempts: raise RuntimeError(f'Source HTTP {code}') from None
        except (OSError, URLError) as error:
            if attempt + 1 == attempts: raise RuntimeError('Source connection failed') from None
            delay = 0
        time.sleep(max(delay, 2 ** attempt))
    raise RuntimeError('Request attempts exhausted')

def commons_query(params):
    """Cache successful API responses only; serially respect server maxlag."""
    key = hashlib.sha256(canonical(params)).hexdigest()
    cached = CACHE / 'search' / (key + '.json')
    if cached.is_file(): return json.loads(cached.read_bytes())
    for attempt in range(3):
        data = json.loads(request_bytes(API + '?' + urlencode(params)))
        error = data.get('error')
        if not error:
            write_json(cached, data)
            return data
        code = error.get('code', 'unknown') if isinstance(error, dict) else 'unknown'
        if code not in {'maxlag', 'ratelimited'} or attempt == 2:
            raise RuntimeError('Commons API error: ' + str(code))
        # MediaWiki may report maxlag as a JSON error with HTTP 200, outside
        # the transport's Retry-After/5xx handling. Never cache that failure.
        time.sleep(5 * (attempt + 1))


def commons_search(query, limit=10):
    params = {'action': 'query', 'format': 'json', 'formatversion': 2,
              'generator': 'search', 'gsrsearch': 'filetype:svg ' + query,
              'gsrnamespace': 6, 'gsrlimit': limit, 'prop': 'imageinfo',
              'iiprop': 'url|size|mime|extmetadata|timestamp|sha1', 'maxlag': 5}
    return commons_query(params)

def license_from_metadata(metadata):
    def value(key): return plain(metadata.get(key, {}).get('value', ''))
    name, url = value('LicenseShortName'), value('LicenseUrl')
    if url.startswith('//'): url = 'https:' + url
    url = re.sub(r'^http:', 'https:', url)
    match = re.fullmatch(r'CC (BY(?:-SA)?) (1\.0|2\.0|2\.5|3\.0|4\.0)', name)
    if match and url.rstrip('/') == 'https://creativecommons.org/licenses/' + match[1].lower() + '/' + match[2]:
        return name, url.rstrip('/') + '/'
    if name in {'CC0', 'CC0 1.0', 'Public domain'}:
        if name.startswith('CC0') or 'creativecommons.org/publicdomain/zero/' in url:
            return 'CC0 1.0', 'https://creativecommons.org/publicdomain/zero/1.0/'
        return 'Public domain', 'https://creativecommons.org/publicdomain/mark/1.0/'
    raise ValueError('Missing or unsupported explicit license')

def safe_svg_xml(body):
    """Remove DTDs without resolving them; allow only unused constant entities.

    Some Illustrator exports declare an unused namespace entity. That does not
    make their vector geometry raster/active. Used, nested, external and parameter
    entities fail closed; no DTD or entity declaration reaches the XML parser.
    Original source bytes remain pinned and are never rewritten on disk.
    """
    declarations = re.findall(rb'<!ENTITY\b[^>]*>', body, re.I)
    if len(declarations) > 32 or len(declarations) != len(re.findall(rb'<!ENTITY\b', body, re.I)):
        raise ValueError('Unsupported XML entity declarations')
    for declaration in declarations:
        match = re.fullmatch(rb'<!ENTITY\s+([A-Za-z_:][A-Za-z0-9_.:\-]*)\s+([\'"])([^&<>]{0,256})\2\s*>', declaration, re.I)
        if not match or b'&' + match.group(1) + b';' in body:
            raise ValueError('Used, nested or external XML entities are unsupported')
    # Scan quoted strings and the internal subset so embedded > characters do
    # not cause the DTD stripper to leave a partial declaration behind.
    cleaned = body
    while True:
        match = re.search(rb'<!DOCTYPE\b', cleaned, re.I)
        if not match: break
        quote = None; depth = 0; end = None
        for at in range(match.end(), len(cleaned)):
            char = cleaned[at]
            if quote:
                if char == quote: quote = None
            elif char in (34, 39): quote = char
            elif char == 91: depth += 1
            elif char == 93: depth -= 1
            elif char == 62 and depth == 0: end = at + 1; break
            if depth < 0: raise ValueError('Invalid XML doctype')
        if end is None: raise ValueError('Unterminated XML doctype')
        cleaned = cleaned[:match.start()] + cleaned[end:]
    if re.search(rb'<!ENTITY\b', cleaned, re.I):
        raise ValueError('XML entity declaration outside DTD')
    return cleaned


def inspect_svg(body):
    root = ET.fromstring(safe_svg_xml(body))
    if root.tag.rsplit('}', 1)[-1] != 'svg': raise ValueError('Source is not SVG')
    if b'<?xml-stylesheet' in body.lower(): raise ValueError('SVG contains an external stylesheet instruction')
    counts = Counter()
    for node in root.iter():
        tag = node.tag.rsplit('}', 1)[-1]
        counts[tag] += 1
        if tag in {'script', 'image', 'feImage', 'foreignObject', 'iframe', 'audio', 'video', 'object', 'embed', 'animate', 'animateMotion', 'animateTransform', 'set'}:
            raise ValueError('SVG contains raster or active content')
        if tag == 'style':
            css = ''.join(node.itertext())
            if re.search(r'@import', css, re.I): raise ValueError('SVG contains imported CSS')
            for target in re.findall(r'url\(([^)]*)\)', css, re.I):
                if not target.strip().strip("\"'").startswith('#'):
                    raise ValueError('SVG contains external stylesheet paint references')
        for key, value in node.attrib.items():
            key = key.rsplit('}', 1)[-1]
            if key.lower().startswith('on'): raise ValueError('SVG contains event handler')
            if tag == 'use' and key == 'href' and not value.startswith('#'):
                raise ValueError('SVG contains external geometry references')
            for target in re.findall(r'url\(([^)]*)\)', value, re.I):
                if not target.strip().strip("\"'").startswith('#'):
                    raise ValueError('SVG contains external paint references')
    shapes = sum(counts[tag] for tag in SHAPES)
    if shapes < 8: raise ValueError(f'Only {shapes} vector shapes; insufficient independent assemblies')
    return {'shapes': shapes, 'groups': counts['g'], 'labels': counts['text'], 'viewBox': root.get('viewBox')}

def candidate(page, archetype, maximum=500_000):
    info = page.get('imageinfo', [{}])[0]
    if info.get('mime') != 'image/svg+xml': raise ValueError('Candidate is not SVG MIME')
    if not isinstance(info.get('size'), int) or info['size'] > maximum: raise ValueError('Candidate exceeds source size budget')
    metadata = info.get('extmetadata', {})
    name, license_url = license_from_metadata(metadata)
    artist = plain(metadata.get('Artist', {}).get('value', ''))
    if not artist: raise ValueError('Source author is unspecified')
    original_url = info['url']
    parsed = urlsplit(original_url)
    if parsed.scheme != 'https' or parsed.hostname != 'upload.wikimedia.org': raise ValueError('Unexpected Commons source host')
    download = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))
    key = hashlib.sha256(download.encode()).hexdigest()
    cached = CACHE / 'downloads' / (key + '.svg')
    if cached.is_file(): body = cached.read_bytes()
    else:
        body = request_bytes(download, maximum=maximum)
        cached.parent.mkdir(parents=True, exist_ok=True); cached.write_bytes(body)
    if info.get('sha1') and hashlib.sha1(body).hexdigest() != info['sha1']:
        raise ValueError('Source changed between metadata lookup and download')
    geometry = inspect_svg(body)
    digest = hashlib.sha256(body).hexdigest()
    filename = archetype + '-' + digest[:16] + '.original.svg'
    identifier = archetype + '-' + digest[:12]
    source = info.get('descriptionurl') or 'https://commons.wikimedia.org/wiki/' + page['title'].replace(' ', '_')
    record = {'id': identifier, 'title': page['title'].removeprefix('File:'), 'author': artist,
              'source': source, 'download': download, 'file': filename, 'sha256': digest,
              'bytes': len(body), 'license': name, 'licenseUrl': license_url,
              'revision': info.get('timestamp', ''), 'upstreamSha1': info.get('sha1', ''),
              'structure': geometry, 'discovery': 'MediaWiki generator=search + imageinfo/extmetadata',
              'adaptations': 'None in pinned original; runtime adaptations recorded by compiler.'}
    (DIRECTORY / filename).write_bytes(body)
    return record

def commons_titles(titles):
    params = {'action': 'query', 'format': 'json', 'formatversion': 2, 'titles': '|'.join(titles),
              'prop': 'imageinfo', 'iiprop': 'url|size|mime|extmetadata|timestamp|sha1', 'maxlag': 5}
    return commons_query(params)


def openclipart_candidate(source, archetype, maximum=500_000):
    parsed = urlsplit(source)
    match = re.fullmatch(r'/detail/(\d+)(?:/[^/?#]+)?', parsed.path)
    if parsed.scheme != 'https' or parsed.hostname != 'openclipart.org' or not match:
        raise ValueError('Unexpected Openclipart source URL')
    number = match.group(1)
    page_cache = CACHE / 'openclipart' / (number + '.html')
    if page_cache.is_file(): page = page_cache.read_text()
    else:
        page = request_bytes(source).decode()
        page_cache.parent.mkdir(parents=True, exist_ok=True); page_cache.write_text(page)
    blocks = re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.S | re.I)
    records = []
    for block in blocks:
        try:
            value = json.loads(block)
            records.extend(value if isinstance(value, list) else [value])
        except ValueError: continue
    images = [value for value in records if isinstance(value, dict) and value.get('@type') in {'ImageObject', 'VisualArtwork'}]
    if not images or not any(value.get('license') == 'https://creativecommons.org/publicdomain/zero/1.0/' for value in images):
        raise ValueError('Openclipart page lacks explicit CC0 license metadata')
    author = re.search(r'<a[^>]+href=["\']/artist/[^"\']+["\'][^>]*>(.*?)</a>', page, re.S)
    title = re.search(r'<h2[^>]*>(.*?)</h2>', page, re.S)
    if not author or not title: raise ValueError('Openclipart author/title missing')
    download = 'https://openclipart.org/download/' + number
    cache = CACHE / 'openclipart' / (number + '.svg')
    if cache.is_file(): body = cache.read_bytes()
    else: body = request_bytes(download, maximum=maximum); cache.write_bytes(body)
    structure = inspect_svg(body)
    digest = hashlib.sha256(body).hexdigest()
    filename = archetype + '-' + digest[:16] + '.original.svg'
    (DIRECTORY / filename).write_bytes(body)
    return {'id': archetype + '-' + digest[:12], 'title': plain(title.group(1)), 'author': plain(author.group(1)),
            'source': source, 'download': download, 'file': filename, 'sha256': digest, 'bytes': len(body),
            'license': 'CC0 1.0', 'licenseUrl': 'https://creativecommons.org/publicdomain/zero/1.0/',
            'licenseConfirmation': 'https://openclipart.org/share', 'structure': structure,
            'discovery': 'Openclipart detail page with explicit ImageObject CC0 license',
            'adaptations': 'None in pinned original; runtime adaptations recorded by compiler.'}


def openclipart_search(query, limit=10):
    key = hashlib.sha256(query.encode()).hexdigest()
    cached = CACHE / 'openclipart-search' / (key + '.html')
    if cached.is_file(): page = cached.read_text()
    else:
        page = request_bytes('https://openclipart.org/search/?' + urlencode({'query': query})).decode()
        cached.parent.mkdir(parents=True, exist_ok=True); cached.write_text(page)
    links = re.findall(r'href=["\'](/detail/\d+(?:/[^"\'#?]+)?)["\']', page)
    return ['https://openclipart.org' + link for link in dict.fromkeys(links)][:limit]


def query_file(path):
    if not path: return QUERIES
    value = json.loads(Path(path).read_bytes())
    items = value.get('archetypes', value.get('items', [])) if isinstance(value, dict) else value
    return {item['id']: item.get('svgQueries', QUERIES.get(item['id'], [])) for item in items}

def discover(queries, maximum_candidates=3, search_limit=10, use_openclipart=False, report_path=None):
    manifest = json.loads(MANIFEST.read_bytes()) if MANIFEST.is_file() else {'version': 1, 'archetypes': []}
    previous = {item['id']: item for item in manifest['archetypes']}
    items = []
    rejections = []
    report = {'version': 1, 'finderVersion': SOURCE_VERSION, 'finderSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'configuration': {'maximumCandidates': maximum_candidates, 'searchLimit': search_limit, 'openclipart': use_openclipart}, 'queries': [], 'existingCandidates': sum(len(item['candidates']) for item in manifest['archetypes']), 'acceptedCandidates': 0, 'inspectedCandidates': 0}
    for archetype, terms in queries.items():
        item = previous.get(archetype, {'id': archetype, 'candidates': []})
        candidates = item['candidates']
        seen = {entry['source'] for entry in candidates}
        for query in terms:
            if len(candidates) >= maximum_candidates: break
            if not isinstance(query, str) or not 2 <= len(query) <= 160:
                rejections.append({'archetype': archetype, 'query': query, 'reason': 'Query must contain 2 to 160 characters'})
                continue
            pages = commons_search(query, search_limit).get('query', {}).get('pages', [])
            report['queries'].append({'archetype': archetype, 'provider': 'commons', 'query': query, 'results': len(pages)})
            for page in pages:
                if len(candidates) >= maximum_candidates: break
                url = page.get('imageinfo', [{}])[0].get('descriptionurl')
                if url in seen: continue
                report['inspectedCandidates'] += 1
                try:
                    if not title_relevance(archetype, page.get('title', '')):
                        raise ValueError('Title indicates unrelated chart/logo/sign/composite or lacks subject evidence')
                    record = candidate(page, archetype)
                    record['query'] = query
                    candidates.append(record); seen.add(record['source'])
                    report['acceptedCandidates'] += 1
                    print(archetype + ': pinned ' + record['title'], flush=True)
                except (ValueError, ET.ParseError, RuntimeError) as error:
                    rejections.append({'archetype': archetype, 'title': page.get('title'), 'reason': str(error)})
        if use_openclipart and len(candidates) < maximum_candidates:
            links = openclipart_search(archetype, search_limit)
            report['queries'].append({'archetype': archetype, 'provider': 'openclipart', 'query': archetype, 'results': len(links)})
            for source in links:
                if len(candidates) >= maximum_candidates: break
                if source in seen: continue
                report['inspectedCandidates'] += 1
                try:
                    record = openclipart_candidate(source, archetype)
                    if not title_relevance(archetype, record['title']):
                        raise ValueError('Title indicates unrelated chart/logo/sign/composite or lacks subject evidence')
                    record['query'] = archetype
                    candidates.append(record); seen.add(record['source'])
                    report['acceptedCandidates'] += 1
                    print(archetype + ': pinned ' + record['title'], flush=True)
                except (ValueError, ET.ParseError, RuntimeError) as error:
                    rejections.append({'archetype': archetype, 'source': source, 'reason': str(error)})
        item['queries'] = terms
        items.append(item)
        # Publish each archetype incrementally; the compiler can consume early candidates.
        merged = {entry['id']: entry for entry in manifest['archetypes']}
        merged.update({entry['id']: entry for entry in items})
        manifest['archetypes'] = list(merged.values())
        write_json(MANIFEST, manifest)
    report['rejectedCandidates'] = sum('query' not in entry for entry in rejections)
    report['rejectedQueries'] = sum('query' in entry for entry in rejections)
    report['rejections'] = rejections
    report['candidateCounts'] = {item['id']: len(item['candidates']) for item in manifest['archetypes']}
    write_json(report_path or CACHE / 'discovery-report.json', report)
    return manifest

def verify_manifest(path=MANIFEST, fetch_missing=False):
    manifest = json.loads(Path(path).read_bytes())
    if manifest.get('version') != 1: raise ValueError('Unsupported source manifest')
    seen = set()
    for item in manifest['archetypes']:
        for record in item['candidates']:
            identifier = record['id']
            if identifier in seen: raise ValueError('Duplicate source candidate ID')
            seen.add(identifier)
            filename = record['file']
            if Path(filename).name != filename: raise ValueError('Source path must be a basename')
            destination = Path(path).parent / filename
            if not destination.exists() and fetch_missing:
                body = request_bytes(record['download'], maximum=max(500_000, record['bytes']))
                if hashlib.sha256(body).hexdigest() != record['sha256']: raise ValueError('Downloaded original changed SHA-256')
                destination.write_bytes(body)
            if destination.is_symlink(): raise ValueError('Pinned source must be a regular local file')
            body = destination.read_bytes()
            if len(body) != record['bytes'] or hashlib.sha256(body).hexdigest() != record['sha256']:
                raise ValueError('Pinned source bytes changed: ' + identifier)
            inspect_svg(body)
            if record['license'] not in {'Public domain', 'CC0 1.0'} and not re.fullmatch(r'CC BY(?:-SA)? (?:1\.0|2\.0|2\.5|3\.0|4\.0)', record['license']):
                raise ValueError('Unapproved source license')
    return {'archetypes': len(manifest['archetypes']), 'candidates': len(seen)}

def main():
    global DIRECTORY, MANIFEST
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['discover', 'verify', 'fetch'])
    parser.add_argument('--queries', type=Path)
    parser.add_argument('--output-dir', type=Path, help='stage proposed candidates separately from approved pins')
    parser.add_argument('--report', type=Path, help='write discovery counts and rejection reasons')
    parser.add_argument('--max-candidates', type=int, default=3)
    parser.add_argument('--search-limit', type=int, default=10)
    parser.add_argument('--openclipart', action='store_true', help='also search Openclipart for unfilled candidate slots')
    args = parser.parse_args()
    if args.output_dir:
        DIRECTORY = args.output_dir.resolve(); MANIFEST = DIRECTORY / 'sources.json'
    if not 1 <= args.max_candidates <= 10 or not 1 <= args.search_limit <= 50:
        parser.error('candidate/search limits must be within 1..10 / 1..50')
    if args.command == 'discover':
        DIRECTORY.mkdir(parents=True, exist_ok=True)
        discover(query_file(args.queries), args.max_candidates, args.search_limit, args.openclipart, args.report)
        receipt_path = args.report or CACHE / 'discovery-report.json'
        receipt = json.loads(receipt_path.read_bytes())
        receipt['queriesSha256'] = hashlib.sha256(args.queries.read_bytes()).hexdigest() if args.queries else hashlib.sha256(canonical(QUERIES)).hexdigest()
        write_json(receipt_path, receipt)
    else: print(json.dumps(verify_manifest(MANIFEST, fetch_missing=args.command == 'fetch')))

if __name__ == '__main__': main()
