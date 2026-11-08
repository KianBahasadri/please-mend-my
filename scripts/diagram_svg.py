"""Conservative, offline SVG digests and safe monochrome diagram compilation.

The source bytes and tree paths are immutable inputs. Inkscape supplies geometric
bounds (including path curves/transforms); its labels are hints, never approval.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

VERSION = "diagram-svg-v1"
NS = "http://www.w3.org/2000/svg"
INKSCAPE = "http://www.inkscape.org/namespaces/inkscape"
GEOMETRY = {"d", "cx", "cy", "r", "rx", "ry", "x", "y", "width", "height", "points", "x1", "x2", "y1", "y2"}
SHAPES = {"path", "circle", "ellipse", "rect", "line", "polyline", "polygon"}
STYLE = {"fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit",
         "stroke-dasharray", "fill-rule", "clip-rule", "opacity", "fill-opacity", "stroke-opacity", "display", "visibility"}
PRESENTATION = STYLE - {"display", "visibility"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_source(path, expected_sha):
    body = Path(path).read_bytes()
    if len(body) > 500_000 or digest_bytes(body) != expected_sha:
        raise ValueError("SVG source exceeds size bound or differs from its SHA pin")
    from find_diagram_sources import safe_svg_xml
    root = ET.fromstring(safe_svg_xml(body))
    if local(root.tag) != "svg":
        raise ValueError("Source is not an SVG document")
    return root


def paths(root):
    def visit(node, key):
        yield key, node
        for index, child in enumerate(node):
            yield from visit(child, key + "." + str(index))
    return list(visit(root, "0"))


def dimensions(root):
    raw = root.get("viewBox", "")
    values = [float(value) for value in re.split(r"[\s,]+", raw.strip()) if value]
    if not values:
        values = [0., 0., length(root.get("width", "0")), length(root.get("height", "0"))]
    if len(values) != 4 or not all(math.isfinite(value) for value in values) or min(values[2:]) <= 0:
        raise ValueError("SVG requires a finite positive viewBox")
    return values


def length(value):
    match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(px|pt|pc|mm|cm|in)?\s*", value)
    if not match:
        raise ValueError("Unsupported relative SVG length")
    number, unit = match.groups()
    return float(number) * {None: 1, "px": 1, "pt": 96 / 72, "pc": 16, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96}[unit]


def _style(value):
    result = {}
    for declaration in value.split(";"):
        key, _, item = declaration.partition(":")
        if key.strip() in STYLE:
            result[key.strip()] = re.sub(r"\s*!important\s*$", "", item.strip())
    return result


def _rules(root):
    result = []
    for node in root.iter():
        if local(node.tag) != "style":
            continue
        for selectors, value in re.findall(r"([^{}]+)\{([^{}]*)\}", "".join(node.itertext())):
            for selector in selectors.split(","):
                selector = selector.strip()
                if not re.fullmatch(r"(?:[\w-]+)?(?:[.#][\w-]+)?", selector):
                    raise ValueError("Complex SVG CSS requires a source-specific adaptation")
                result.append((selector, _style(value)))
    return result


def _matches(node, selector):
    match = re.fullmatch(r"([\w-]+)?(?:([.#])([\w-]+))?", selector)
    tag, marker, value = match.groups()
    tag_matches = not tag or tag == local(node.tag)
    marker_matches = not marker or (node.get("id") == value if marker == "#" else value in node.get("class", "").split())
    return tag_matches and marker_matches


def _node_style(node, inherited, rules):
    style = dict(inherited)
    style.update({key: value for key, value in node.attrib.items() if key in STYLE})
    for selector, declarations in rules:
        if _matches(node, selector):
            style.update(declarations)
    style.update(_style(node.get("style", "")))
    return style


def _color(value, default="currentColor"):
    value = value.strip().lower()
    if value in {"none", "transparent"}:
        return "none"
    if value == "white":
        return "var(--paper)"
    if value in {"currentcolor", "black"}:
        return "currentColor"
    if value.startswith("#"):
        item = value[1:]
        if len(item) in {3, 4}:
            item = "".join(char * 2 for char in item)
        if len(item) in {6, 8} and re.fullmatch(r"[0-9a-f]+", item):
            rgb = [int(item[at:at + 2], 16) for at in (0, 2, 4)]
            return "var(--paper)" if sum(rgb) / 3 >= 200 else "currentColor"
    if value.startswith("rgb"):
        rgb = re.findall(r"[\d.]+", value)[:3]
        if len(rgb) == 3:
            return "var(--paper)" if sum(map(float, rgb)) / 3 >= 200 else "currentColor"
    if value.startswith("url("):
        # Gradients are adapted to a monochrome fill; previews gate their use.
        return default
    return default


def _validate_presentation(style):
    for key, value in style.items():
        if key in {"fill", "stroke", "display", "visibility"}:
            continue
        if len(value) > 150 or not re.fullmatch(r"[\d.eE+\-,\s]*(?:px)?|none|round|square|butt|miter|bevel|nonzero|evenodd", value):
            raise ValueError("Unsupported SVG presentation value: " + key)


def flatten(root):
    """Expand local use references and inherited presentation into safe leaves."""
    keyed = paths(root)
    if len(keyed) > 5000:
        raise ValueError("SVG exceeds node bound")
    refs = {node.get("id"): (key, node) for key, node in keyed if node.get("id")}
    rules = _rules(root)
    leaves = []
    dropped = {"text": 0, "image": 0, "active": 0}

    def visit(node, key, inherited, transforms, ancestors, seen=frozenset(), referenced=False):
        tag = local(node.tag)
        if tag in {"script", "foreignObject", "image", "iframe", "audio", "video", "animate", "set"}:
            dropped["image" if tag == "image" else "active"] += 1
            return
        if tag in {"defs", "metadata", "title", "desc", "style", "namedview"} and not referenced:
            return
        if tag in {"text", "tspan"}:
            dropped["text"] += 1
            return
        if tag not in {"svg", "g", "a", "symbol", "use", *SHAPES}:
            return
        if any(node.get(attribute) and node.get(attribute) != "none" for attribute in ("clip-path", "mask")):
            raise ValueError("Clipped/masked geometry requires a source-specific adaptation")
        inline = node.get("style", "")
        if re.search(r"(?:clip-path|mask)\s*:", inline):
            raise ValueError("Clipped/masked geometry requires a source-specific adaptation")
        style = _node_style(node, inherited, rules)
        if style.get("display") == "none" or style.get("visibility") == "hidden":
            return
        transforms = [*transforms, *([node.get("transform")] if node.get("transform") else [])]
        label = node.get("{" + INKSCAPE + "}label", "")
        ancestors = [*ancestors, {"key": key, "id": node.get("id", ""), "label": label}]
        if tag == "use":
            href = node.get("href", node.get("{http://www.w3.org/1999/xlink}href", ""))
            if not href.startswith("#") or href[1:] not in refs or href in seen:
                raise ValueError("SVG use references must be local and acyclic")
            target_key, target = refs[href[1:]]
            if local(target.tag) == "symbol" and target.get("viewBox"):
                raise ValueError("Symbol viewBox references require a source-specific adaptation")
            x, y = node.get("x", "0"), node.get("y", "0")
            use_transforms = [*transforms, f"translate({x} {y})"]
            visit(target, key + "@" + target_key, style, use_transforms, ancestors, seen | {href}, True)
            return
        if tag in {"svg", "g", "a", "symbol"}:
            for index, child in enumerate(node):
                visit(child, key + "." + str(index), style, transforms, ancestors, seen, referenced)
            return
        _validate_presentation(style)
        attrs = {name: value for name, value in node.attrib.items() if name in GEOMETRY}
        attrs.update({name: value for name, value in style.items() if name in PRESENTATION})
        attrs["fill"] = _color(style.get("fill", "black"))
        attrs["stroke"] = _color(style.get("stroke", "none"))
        if attrs["stroke"] != "none" and "stroke-width" not in attrs:
            attrs["stroke-width"] = "1"
        if transforms:
            attrs["transform"] = " ".join(transforms)
        attrs["data-source-id"] = key
        if len(attrs.get("d", "")) > 300_000 or any("url(" in value or "<" in value for name, value in attrs.items() if name not in {"fill", "stroke"}):
            raise ValueError("Unsafe SVG geometry/presentation")
        leaves.append({"key": key, "tag": tag, "id": node.get("id", ""), "label": label,
                       "ancestors": ancestors[:-1], "ast": [tag, attrs]})

    visit(root, "0", {}, [], [])
    if dropped["image"] or dropped["active"]:
        raise ValueError("Raster-wrapped or active SVG is not a reusable vector diagram")
    if len(leaves) < 8:
        raise ValueError("SVG has fewer than eight independent vector shapes")
    return leaves, dropped


def _descriptor_xml(descriptor, *, ink="#111", paper="#fafafa"):
    tag, attrs, *children = descriptor
    node = ET.Element("{" + NS + "}" + tag)
    for key, value in attrs.items():
        if key == "data-source-id":
            continue
        node.set(key, str(value).replace("currentColor", ink).replace("var(--paper)", paper))
    for child in (children[0] if children else []):
        node.append(_descriptor_xml(child, ink=ink, paper=paper))
    return node


def bounds(leaves, box, workdir):
    if not shutil.which("inkscape"):
        raise ValueError("Inkscape is required for offline geometric bounds")
    workdir = Path(workdir); workdir.mkdir(parents=True, exist_ok=True)
    root = ET.Element("{" + NS + "}svg", {"viewBox": " ".join(map(str, box)), "width": str(box[2]), "height": str(box[3])})
    for number, leaf in enumerate(leaves):
        node = _descriptor_xml(leaf["ast"])
        node.set("id", "shape-" + str(number)); root.append(node)
    source = workdir / "bounds.svg"
    ET.ElementTree(root).write(source, encoding="utf-8", xml_declaration=True)
    result = subprocess.run(["inkscape", str(source), "--query-all"], capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise ValueError("Inkscape could not compute SVG bounds")
    found = {}
    for line in result.stdout.splitlines():
        cells = line.rsplit(",", 4)
        if len(cells) != 5 or not cells[0].startswith("shape-"):
            continue
        try:
            values = list(map(float, cells[1:]))
        except ValueError:
            continue
        if all(math.isfinite(value) for value in values):
            found[int(cells[0][6:])] = [round(values[0] / box[2], 5), round(values[1] / box[3], 5),
                                      round(values[2] / box[2], 5), round(values[3] / box[3], 5)]
    if len(found) < len(leaves) * .8:
        raise ValueError("Too many source shapes lack reliable geometric bounds")
    return found


def structural_digest(path, expected_sha, workdir):
    root = parse_source(path, expected_sha)
    box = dimensions(root)
    leaves, dropped = flatten(root)
    boxes = bounds(leaves, box, workdir)
    nodes = OrderedDict()
    for index, leaf in enumerate(leaves):
        if index not in boxes:
            continue
        leaf["bbox"] = boxes[index]
        chain = [*leaf["ancestors"], leaf]
        for item in chain:
            entry = nodes.setdefault(item["key"], {"key": item["key"], "tag": item.get("tag", "g"),
                "id": item.get("id", "")[:100], "label": item.get("label", "")[:120], "shapeCount": 0, "bbox": None})
            entry["shapeCount"] += 1
            x, y, width, height = boxes[index]
            prior = entry["bbox"]
            if prior is None:
                entry["bbox"] = [x, y, width, height]
            else:
                right, bottom = max(prior[0] + prior[2], x + width), max(prior[1] + prior[3], y + height)
                left, top = min(prior[0], x), min(prior[1], y)
                entry["bbox"] = [round(left, 5), round(top, 5), round(right - left, 5), round(bottom - top, 5)]
    labels = []
    for key, node in paths(root):
        if local(node.tag) == "text":
            text = " ".join("".join(node.itertext()).split())[:140]
            if text:
                labels.append({"key": key, "text": text, "x": node.get("x", ""), "y": node.get("y", "")})
    public = {"version": VERSION, "sourceSha256": expected_sha, "viewBox": box,
              "shapeCount": len(leaves), "dropped": dropped, "labels": labels[:80], "nodes": list(nodes.values())}
    return public, leaves


def compile_art(subject, source, mapping, digest, leaves, *, omit_geometry_parts=(), minimum_parts=3):
    # A primary drawing must still identify three assemblies. A secondary view
    # may add two previously hidden assemblies; its caller verifies novelty.
    if type(minimum_parts) is not int or minimum_parts not in {2, 3}:
        raise ValueError("A diagram requires two or three independent physical parts")
    if mapping.get("selectedSource") != source["id"]:
        raise ValueError("Mapping selects a different source")
    parts = {part["id"] for part in subject["parts"]}
    known = {node["key"] for node in digest["nodes"]}
    assignments = {}
    for assignment in mapping["groupMap"]:
        key, part = assignment["node"], assignment["part"]
        if key not in known or part not in parts | {"context", "drop"} or key in assignments:
            raise ValueError("Mapping references an unknown or duplicated tree path/part")
        if assignment["confidence"] == "low":
            raise ValueError("Low-confidence component mappings require review")
        assignments[key] = part
    buckets = OrderedDict()
    context = []
    for leaf in leaves:
        eligible = [(key, part) for key, part in assignments.items()
                    if leaf["key"] == key or leaf["key"].startswith(key + ".") or leaf["key"].startswith(key + "@")]
        selected = max(eligible, key=lambda item: len(item[0]))[1] if eligible else "context"
        if selected == "drop":
            continue
        if selected in omit_geometry_parts:
            selected = "context"
        (context if selected == "context" else buckets.setdefault(selected, [])).append(leaf["ast"])
    if len(buckets) < minimum_parts:
        raise ValueError("Diagram maps fewer than " + str(minimum_parts) + " independent repairable assemblies")
    attribution = {key: source[key] for key in ("title", "author", "source", "license", "licenseUrl")}
    attribution["changes"] = ("Adapted for interactive parts: labels and unsupported decorative elements omitted; "
                              "local references expanded, source geometry regrouped, and paints adapted to monochrome light/dark themes.")
    if "BY-SA" in attribution["license"].upper():
        attribution["changes"] += " This artwork adaptation is distributed under " + attribution["license"] + "."
    for field in ("copyright", "disclaimer"):
        if source.get(field):
            attribution[field] = source[field]
    payload = {"version": 1, "id": subject["id"], "viewBox": " ".join(f"{value:g}" for value in digest["viewBox"]),
               "context": context, "parts": buckets, "attribution": attribution}
    if len(canonical(payload)) > 300_000:
        raise ValueError("Compiled diagram exceeds the runtime byte bound")
    return payload


def render_preview(art, output, highlight=None, *, dark=False):
    """Render only sanitized generated geometry; never execute a browser."""
    ink, paper = ("#fafafa", "#030303") if dark else ("#111111", "#fafafa")
    box = list(map(float, art["viewBox"].split()))
    root = ET.Element("{" + NS + "}svg", {"viewBox": art["viewBox"], "width": "720", "height": "450"})
    root.append(ET.Element("{" + NS + "}rect", {"x": str(box[0]), "y": str(box[1]), "width": str(box[2]), "height": str(box[3]), "fill": paper}))
    context = ET.SubElement(root, "{" + NS + "}g", {"opacity": ".3"})
    for descriptor in art["context"]:
        context.append(_descriptor_xml(descriptor, ink=ink, paper=paper))
    for part, shapes in art["parts"].items():
        group = ET.SubElement(root, "{" + NS + "}g", {"opacity": "1" if part == highlight else ".22" if highlight else ".6"})
        for descriptor in shapes:
            group.append(_descriptor_xml(descriptor, ink=ink, paper=paper))
    svg = Path(output).with_suffix(".svg")
    ET.ElementTree(root).write(svg, encoding="utf-8", xml_declaration=True)
    subprocess.run(["rsvg-convert", "-o", str(output), str(svg)], check=True, capture_output=True, timeout=30)
