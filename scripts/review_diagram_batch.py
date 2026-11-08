"""Bounded geometry heuristics and one-sheet visual evidence for batch SVGs.

A successful numeric check is *not* semantic approval. The source drawing and
each highlighted region still require visual review before publication. Bounds
come from the compiler's existing Inkscape digest; this module never starts a
second geometry pass, fetches sources, or calls an LLM.
"""
from __future__ import annotations

from collections import Counter
import math
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from diagram_svg import GEOMETRY, NS, SHAPES, _descriptor_xml, canonical

VERSION = "diagram-review-v1"
MAX_SHAPES = 500
MAX_ART_BYTES = 300_000


def _physical(part):
    kind = part.get("kind", part.get("type"))
    if kind is not None:
        return kind == "physical"
    return part.get("physical", True) is True  # Existing reviewed v1 lists.


def _box(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(isinstance(item, bool) or not isinstance(item, (int, float))
                   or not math.isfinite(item) for item in value)
            or value[2] < 0 or value[3] < 0):
        return None
    return list(value)


def _union(boxes):
    if not boxes:
        return None
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[0] + box[2] for box in boxes)
    bottom = max(box[1] + box[3] for box in boxes)
    return [left, top, max(0, right - left), max(0, bottom - top)]


def _area(box):
    return box[2] * box[3] if box else 0


def _intersection(a, b):
    if not a or not b:
        return 0
    return (max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
            * max(0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])))


def _descriptors(shapes):
    """Yield leaf descriptors without accepting arbitrary recursive payloads."""
    if not isinstance(shapes, list):
        raise ValueError("shape bucket is not a list")
    for shape in shapes:
        if (not isinstance(shape, list) or len(shape) != 2
                or shape[0] not in SHAPES or not isinstance(shape[1], dict)):
            raise ValueError("shape bucket contains a non-compiler descriptor")
        yield shape


def validate_geometry(art, parts, digest=None, leaves=None, mapping=None, *, minimum_parts=3):
    """Return deterministic rejection reasons plus measured mapping metrics.

    The 0.94 whole-object and 0.90 identical-bbox thresholds are conservative
    triage heuristics, not evidence that a region names the correct assembly.
    A legitimate complex/nested drawing can be held for manual adaptation.
    """
    if type(minimum_parts) is not int or minimum_parts not in {2, 3}:
        raise ValueError("A diagram requires two or three independent physical parts")
    issues, metrics = [], {"reviewVersion": VERSION, "requiresVisualReview": True,
                          "minimumPhysicalParts": minimum_parts}
    if not isinstance(art, dict) or not isinstance(art.get("parts"), dict):
        return {"valid": False, "issues": ["invalid compiled art"], "metrics": metrics}
    try:
        body_size = len(canonical(art))
        view_box = [float(value) for value in art.get("viewBox", "").split()]
    except (ValueError, TypeError, OverflowError):
        return {"valid": False, "issues": ["non-finite or malformed compiled art"], "metrics": metrics}
    metrics["bytes"] = body_size
    if (len(view_box) != 4 or not all(math.isfinite(value) for value in view_box)
            or min(view_box[2:]) <= 0):
        issues.append("invalid viewBox")
    if body_size > MAX_ART_BYTES:
        issues.append("compiled art exceeds 300000 bytes")
    definitions = {part["id"]: part for part in parts if isinstance(part, dict) and isinstance(part.get("id"), str)}
    physical = {identifier for identifier, part in definitions.items() if _physical(part)}
    if len(art["parts"]) < minimum_parts:
        issues.append("fewer than " + str(minimum_parts) + " independently mapped physical parts")
    for identifier in art["parts"]:
        if identifier not in definitions:
            issues.append("unknown mapped part: " + identifier)
        elif identifier not in physical:
            issues.append("symptom/nonphysical part mapped to geometry: " + identifier)
    if mapping is not None:
        for assignment in mapping.get("groupMap", []):
            if assignment.get("confidence") == "low":
                issues.append("low-confidence mapping: " + str(assignment.get("node", "")))

    boxes = {}
    if digest:
        for node in digest.get("nodes", []):
            if isinstance(node, dict) and isinstance(node.get("key"), str):
                boxes[node["key"]] = _box(node.get("bbox"))
    for leaf in leaves or []:
        if isinstance(leaf, dict) and isinstance(leaf.get("key"), str):
            boxes[leaf["key"]] = _box(leaf.get("bbox"))
    # Validate the actual compiled buckets, where ancestor overrides and omitted
    # paths have already been resolved, rather than trusting proposed groupMap.
    shape_owners, geometry_owners, part_boxes = {}, {}, {}
    counts, bounded, total = Counter(), 0, 0
    all_boxes = []
    try:
        buckets = [("context", art.get("context", [])), *art["parts"].items()]
        for identifier, bucket in buckets:
            current = []
            for tag, attrs in _descriptors(bucket):
                total += 1
                if total > MAX_SHAPES:
                    issues.append("compiled art exceeds 500 visible shapes")
                    raise StopIteration
                key = attrs.get("data-source-id")
                box = boxes.get(key)
                if box:
                    bounded += 1
                    current.append(box)
                    all_boxes.append(box)
                if identifier == "context":
                    continue
                counts[identifier] += 1
                if isinstance(key, str):
                    if key in shape_owners:
                        issues.append("source shape repeated across parts: " + key)
                    shape_owners[key] = identifier
                geometry = canonical([tag, {key: value for key, value in attrs.items()
                                            if key in GEOMETRY or key == "transform"}])
                owner = geometry_owners.get(geometry)
                if owner and owner != identifier:
                    issues.append("identical geometry assigned to different parts: " + owner + "/" + identifier)
                geometry_owners[geometry] = identifier
            if identifier != "context":
                part_boxes[identifier] = _union(current)
                if not counts[identifier]:
                    issues.append("empty mapped part: " + identifier)
    except StopIteration:
        pass
    except (ValueError, TypeError, OverflowError) as error:
        issues.append("invalid shape descriptors: " + str(error))

    object_box = _union(all_boxes)
    object_area = _area(object_box)
    metrics.update(visibleShapes=total, mappedPhysicalParts=len(set(art["parts"]) & physical),
                   totalPhysicalParts=len(physical),
                   physicalCoverage=round(len(set(art["parts"]) & physical) / len(physical), 5) if physical else 0,
                   boundsAvailable=bool(boxes), boundedShapes=bounded, objectBBox=object_box,
                   parts={identifier: {"shapes": counts[identifier], "bbox": box,
                           "objectAreaRatio": round(_area(box) / object_area, 5) if object_area else None}
                          for identifier, box in part_boxes.items()})
    if boxes and total and bounded < total * .8:
        issues.append("fewer than 80% of compiled shapes have finite geometric bounds")
    if boxes and not object_area:
        issues.append("compiled drawing has no measurable area")
    for identifier, box in part_boxes.items():
        if not box:
            if boxes:
                issues.append("mapped part lacks measured bounds: " + identifier)
            continue
        if _area(box) <= 1e-9:
            issues.append("mapped part has no measurable area: " + identifier)
        if object_area and _area(box) / object_area > .94:
            issues.append("mapped part spans almost the entire drawing: " + identifier)
    identifiers = list(part_boxes)
    for offset, first in enumerate(identifiers):
        for second in identifiers[offset + 1:]:
            a, b = part_boxes[first], part_boxes[second]
            intersection = _intersection(a, b)
            union = _area(a) + _area(b) - intersection
            if union > 0 and intersection / union >= .90:
                issues.append("mapped parts have nearly identical bounding regions: " + first + "/" + second)
    return {"valid": not issues, "issues": list(dict.fromkeys(issues)), "metrics": metrics}


def validate_views(views, parts):
    """Enforce deterministic multi-view retention without inventing coverage."""
    issues, mapped = [], set()
    if len(views) > 3:
        issues.append("more than three views")
    physical = {part["id"] for part in parts if _physical(part)}
    metrics = []
    for offset, view in enumerate(views):
        current = set(view.get("mappedParts", []))
        if current - physical:
            issues.append("view maps unknown or nonphysical parts: " + str(view.get("id", offset)))
        newly_mapped = current - mapped
        if offset and len(newly_mapped) < 2:
            issues.append("secondary view adds fewer than two valid parts: " + str(view.get("id", offset)))
        metrics.append({"id": view.get("id"), "newParts": sorted(newly_mapped)})
        mapped.update(current & physical)
    return {"valid": not issues, "issues": issues, "metrics": {"views": metrics,
            "mappedPhysical": len(mapped), "totalPhysical": len(physical),
            "coverage": round(len(mapped) / len(physical), 5) if physical else 0}}


def write_review_sheet(art, output, title="", *, panel_labels=None):
    """One bounded PNG: complete drawing plus every individual highlighted part.

    One librsvg process renders all panels. No browser or source SVG is loaded.
    This is build-only evidence; a sheet is never an approval record.
    """
    parts = list(art["parts"])
    if len(parts) > 12:
        raise ValueError("review sheet supports at most twelve physical parts")
    shape_count = sum(len(bucket) for bucket in [art.get("context", []), *art["parts"].values()])
    if shape_count > MAX_SHAPES or len(canonical(art)) > MAX_ART_BYTES:
        raise ValueError("review sheet input exceeds compiler bounds")
    box = [float(value) for value in art["viewBox"].split()]
    if len(box) != 4 or not all(math.isfinite(value) for value in box) or min(box[2:]) <= 0:
        raise ValueError("review sheet requires a finite viewBox")
    columns, cell_width, cell_height, header = 3, 340, 235, 42
    panels = [None, *parts]
    rows = math.ceil(len(panels) / columns)
    width, height = columns * cell_width, rows * cell_height + header
    root = ET.Element("{" + NS + "}svg", {"viewBox": f"0 0 {width} {height}",
                     "width": str(width), "height": str(height)})
    ET.SubElement(root, "{" + NS + "}rect", {"width": str(width), "height": str(height), "fill": "#101010"})
    heading = ET.SubElement(root, "{" + NS + "}text", {"x": "12", "y": "27", "fill": "#fafafa",
                           "font-family": "sans-serif", "font-size": "17"})
    heading.text = str(title or art.get("id", "Diagram"))[:150] + " — unapproved geometry review"
    for offset, highlighted in enumerate(panels):
        x, y = (offset % columns) * cell_width, header + (offset // columns) * cell_height
        label = ET.SubElement(root, "{" + NS + "}text", {"x": str(x + 12), "y": str(y + 22),
                             "fill": "#fafafa", "font-family": "sans-serif", "font-size": "14"})
        label.text = (panel_labels or {}).get(highlighted, highlighted) if highlighted is not None else "Complete drawing"
        panel = ET.SubElement(root, "{" + NS + "}svg", {"x": str(x + 10), "y": str(y + 32),
                              "width": str(cell_width - 20), "height": str(cell_height - 45),
                              "viewBox": art["viewBox"], "preserveAspectRatio": "xMidYMid meet"})
        context = ET.SubElement(panel, "{" + NS + "}g", {"opacity": ".18" if highlighted else ".4"})
        for descriptor in _descriptors(art.get("context", [])):
            context.append(_descriptor_xml(descriptor, ink="#fafafa", paper="#101010"))
        for identifier, shapes in art["parts"].items():
            group = ET.SubElement(panel, "{" + NS + "}g", {"opacity": "1" if identifier == highlighted else ".10" if highlighted else ".8"})
            for descriptor in _descriptors(shapes):
                group.append(_descriptor_xml(descriptor, ink="#80ffcc" if identifier == highlighted else "#fafafa", paper="#101010"))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    svg = output.with_suffix(".svg")
    ET.ElementTree(root).write(svg, encoding="utf-8", xml_declaration=True)
    subprocess.run(["rsvg-convert", "-o", str(output), str(svg)], check=True, capture_output=True, timeout=45)
    return {"panels": len(panels), "width": width, "height": height, "parts": parts,
            "requiresVisualReview": True}


def candidate_groups(digest, leaves, *, limit=24):
    """Number real source regions without inventing geometric proxy boxes.

    Prefer meaningful groups, then spread remaining leaves spatially. Exact
    descendant sets are deduplicated. Large silhouette groups/background paths
    and sub-pixel strokes are excluded *before* the model sees candidate IDs.
    The complete sanitized drawing remains context, not an assignable part.
    """
    if type(limit) is not int or not 3 <= limit <= 36:
        raise ValueError('candidate limit must be 3..36')
    if len(leaves) > MAX_SHAPES:
        raise ValueError('candidate leaves exceed shape bound')
    all_box = _union([_box(leaf.get('bbox')) for leaf in leaves if _box(leaf.get('bbox'))])
    area = _area(all_box)
    if not area:
        raise ValueError('candidate groups require finite measurable bounds')
    vb = digest.get('viewBox', [])
    if len(vb) != 4 or min(vb[2:]) <= 0:
        raise ValueError('candidate groups require a finite positive viewBox')
    candidates, seen = [], set()
    for node in digest.get('nodes', []):
        box = _box(node.get('bbox'))
        if not box or not _area(box) or _area(box) / area > .75:
            continue
        descendants = [leaf for leaf in leaves if leaf['key'] == node['key']
                       or leaf['key'].startswith(node['key'] + '.')
                       or leaf['key'].startswith(node['key'] + '@')]
        keys = tuple(leaf['key'] for leaf in descendants)
        if not keys or keys in seen:
            continue
        # A long leader line is not a useful assembly. Keep thin physical groups
        # such as a chain when they have several visible shapes or a label, but
        # reject individually near-invisible low-opacity / tiny-stroke leaves.
        visible = False
        for leaf in descendants:
            attrs = leaf['ast'][1]
            try:
                opacity = float(attrs.get('opacity', 1))
                fill_alpha = opacity * float(attrs.get('fill-opacity', 1))
                stroke_alpha = opacity * float(attrs.get('stroke-opacity', 1))
                width = float(str(attrs.get('stroke-width', '1')).removesuffix('px'))
            except (ValueError, TypeError, OverflowError):
                continue
            leaf_box = _box(leaf.get('bbox'))
            # Paper-colored cutouts do not respond to currentColor highlights.
            # Keep that geometry as drawing context, never as an unlit mark.
            filled = attrs.get('fill', 'currentColor') not in {'none', 'var(--paper)'} and fill_alpha >= .08 and _area(leaf_box) / area >= .00005
            stroked = attrs.get('stroke', 'none') not in {'none', 'var(--paper)'} and stroke_alpha >= .12 and width * 720 / max(vb[2:]) >= .6
            visible |= filled or stroked
        if not visible:
            continue
        seen.add(keys)
        named = bool(node.get('label') or (node.get('id') and not str(node['id']).startswith(('path', 'rect', 'g', 'shape'))))
        group = len(keys) > 1
        candidates.append({'node': node['key'], 'sourceId': node.get('id', '')[:100],
            'sourceLabel': node.get('label', '')[:120], 'shapeCount': len(keys),
            'leafKeys': list(keys), 'bbox': box, '_priority': (int(named), int(group), -abs(len(keys) - 8))})
    candidates.sort(key=lambda item: (item['_priority'], item['node']), reverse=True)
    chosen = []
    # Group-first coverage, then disjoint regions before nested refinements.
    # A bounded list avoids hundreds of images on unstructured technical art.
    for disjoint in (True, False):
        covered = {key for item in chosen for key in item['leafKeys']}
        for item in candidates:
            if item in chosen or (disjoint and set(item['leafKeys']) & covered):
                continue
            chosen.append(item)
            covered.update(item['leafKeys'])
            if len(chosen) == limit:
                break
        if len(chosen) == limit:
            break
    for number, item in enumerate(chosen, 1):
        item['candidate'] = number
        del item['_priority']
    return chosen


def write_candidate_sheets(digest, leaves, directory, *, title='', limit=24):
    """Render paginated 12-mark sheets with a stable number→treePath manifest."""
    groups = candidate_groups(digest, leaves, limit=limit)
    if len(groups) < 3:
        raise ValueError('Fewer than three useful candidate regions')
    directory = Path(directory)
    images = []
    for page, start in enumerate(range(0, len(groups), 12), 1):
        batch = groups[start:start + 12]
        # Candidate memberships can overlap (ancestor/child refinements); the
        # existing review renderer has disjoint part buckets, so one tile per
        # candidate is generated in a bounded paginated source document below.
        output = directory / ('candidates-' + str(page) + '.png')
        _write_candidate_page(digest, leaves, batch, output, title)
        images.append(output)
    return {'groups': groups, 'images': images}


def _write_candidate_page(digest, leaves, groups, output, title):
    columns, cw, ch, header = 3, 340, 235, 42
    panels = [None, *groups]
    width, height = columns * cw, math.ceil(len(panels) / columns) * ch + header
    root = ET.Element('{' + NS + '}svg', {'viewBox': f'0 0 {width} {height}',
        'width': str(width), 'height': str(height)})
    ET.SubElement(root, '{' + NS + '}rect', {'width': str(width), 'height': str(height), 'fill': '#101010'})
    heading = ET.SubElement(root, '{' + NS + '}text', {'x': '12', 'y': '27', 'fill': '#fafafa',
        'font-family': 'sans-serif', 'font-size': '17'})
    heading.text = str(title)[:110] + ' — numbered source regions'
    for offset, mark in enumerate(panels):
        x, y = (offset % columns) * cw, header + (offset // columns) * ch
        caption = ET.SubElement(root, '{' + NS + '}text', {'x': str(x + 12), 'y': str(y + 22),
            'fill': '#fafafa', 'font-family': 'sans-serif', 'font-size': '14'})
        caption.text = 'Complete drawing' if mark is None else 'Candidate ' + str(mark['candidate'])
        panel = ET.SubElement(root, '{' + NS + '}svg', {'x': str(x + 10), 'y': str(y + 32),
            'width': str(cw - 20), 'height': str(ch - 45),
            'viewBox': ' '.join(str(value) for value in digest['viewBox']), 'preserveAspectRatio': 'xMidYMid meet'})
        keys = set(mark['leafKeys']) if mark else set()
        for leaf in leaves:
            selected = leaf['key'] in keys
            bucket = ET.SubElement(panel, '{' + NS + '}g', {'opacity': '1' if selected else '.12' if mark else '.7'})
            bucket.append(_descriptor_xml(leaf['ast'], ink='#80ffcc' if selected else '#fafafa', paper='#101010'))
    output = Path(output); output.parent.mkdir(parents=True, exist_ok=True)
    svg = output.with_suffix('.svg')
    ET.ElementTree(root).write(svg, encoding='utf-8', xml_declaration=True)
    subprocess.run(['rsvg-convert', '-o', str(output), str(svg)], check=True, capture_output=True, timeout=45)
