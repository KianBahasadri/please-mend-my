#!/usr/bin/env python3
"""Prepare local, attributed bicycle geometry from pinned reusable SVGs.

No network, SVG interpretation, or arbitrary markup insertion occurs at runtime.
Run normally to regenerate scripts/data/approved-bicycle-art.js; --check verifies exact bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "docs/references/bicycle"
OUTPUT = ROOT / "scripts/data/approved-bicycle-art.js"
PINNED = {
    "commons-bicycle-diagram.original.svg": "fb46ca1c99668629b8111776031bc5b09db9e17735f95642ab933461ada52605",
    "openclipart-bicycle.original.svg": "5e0d8b79821d231246e3b0616e0e12f52e903c9c4910bfd3023d6b83b3dc52e4",
}
ORDER = ["wheels", "tires", "fork", "chain", "gears", "seat", "handlebars",
         "brakes", "pedals", "bottom-bracket", "lights"]
GEOMETRY = {"d", "cx", "cy", "r", "rx", "ry", "x", "y", "width", "height", "points"}
PRESENTATION = {"stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit",
                "stroke-dasharray", "fill-rule", "clip-rule", "opacity", "fill-opacity", "stroke-opacity"}


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_source(name: str) -> ET.Element:
    data = (SOURCES / name).read_bytes()
    if hashlib.sha256(data).hexdigest() != PINNED[name]:
        raise ValueError(f"Pinned bicycle source changed: {name}")
    if b"<!ENTITY" in data or b"<!DOCTYPE" in data:
        raise ValueError("Unexpected XML declaration in pinned source")
    return ET.fromstring(data)


def component(path: tuple[int, ...]) -> str | None:
    """Classify actual source subassemblies, not replacement drawn geometry.

    Positions are tied to the pinned original. Shared wheel <use> nodes resolve
    to the original's referenced geometry with their ancestor transforms intact.
    """
    if path[0] == 4:
        if path[:2] == (4, 1) or path[:3] in {(4, 2, 6), (4, 2, 7)}:
            return None  # Drop blurred ground shadows and repeated tyre tread.
        return "tires" if path[:3] in {(4, 2, 3), (4, 2, 5)} else "wheels"
    if path[0] == 1:
        if path[1] == 0:
            return "wheels"
        if path[1] == 1:
            return "gears"
        if path[1] in {2, 3, 5}:
            return "brakes"
        if path[1] == 4:
            return "handlebars" if path[2] < 4 else "context"
        if path[1] == 6:
            return "context" if path[2] < 2 else "brakes"
    if path[0] == 3:
        return "brakes"
    if path[0] == 5:
        if path[1] == 0:
            return "context"
        if path[1] in {1, 3}:
            return "brakes"
        if path[1] == 2:
            return ["brakes", "context", "handlebars"][path[2]]
        if path[1] == 4:
            return "handlebars" if path[2] == 0 else "seat"
    if path[0] == 6:
        return "pedals"
    if path[0] == 7:
        return "gears"
    if path[0] == 8:
        return ["seat", "seat", "fork", "brakes"][path[1]]
    if path[0] == 9:
        return "brakes"
    if path[0] == 10:
        if path[1] == 0:
            return "handlebars" if path[2] >= 8 else "brakes" if path[2] == 3 else "fork"
        if path[1] == 1:
            return "gears"
        if path[1] == 2:
            return "wheels" if path[2] < 2 else "bottom-bracket" if path[2] == 2 else "brakes"
    if path[0] == 11:
        return "brakes"
    if path[0] == 12:
        return "seat" if path[1] < 2 else "context"
    if path[0] in {13, 14}:
        return "context"
    if path[0] == 15:
        return "handlebars" if path[1] < 2 else "context"
    if path[0] == 16:
        return "gears"
    if path[0] == 17:
        if path[1] == 0:
            return "bottom-bracket" if path[2] == 6 else "fork" if path[2] in {1, 2, 3, 4, 5} else "context"
        return {1: "brakes", 2: "brakes", 3: "seat", 4: "gears", 5: "context",
                6: "bottom-bracket", 7: "bottom-bracket", 8: "chain"}[path[1]]
    if path[0] == 18:
        return "chain"
    if path[0] == 19:
        if path[1] == 0:
            return ["bottom-bracket", "pedals", "pedals", "pedals", "pedals", "context", "gears"][path[2]]
        return "gears"
    return None  # Labels, callouts, dots, translation switches, editor metadata.


def paint(value: str, default: str) -> str:
    if value == "none":
        return "none"
    if value.startswith("#"):
        color = value[1:]
        if len(color) == 3:
            color = "".join(char * 2 for char in color)
        if len(color) == 6:
            levels = [int(color[at:at + 2], 16) for at in (0, 2, 4)]
            if min(levels) > 185:
                return "var(--paper)"
    return default


def prepare() -> dict:
    root = read_source("commons-bicycle-diagram.original.svg")
    references = {node.get("id"): (node, path) for node, path in descendants(root) if node.get("id")}
    buckets: dict[str, list] = {name: [] for name in ["context", *ORDER]}

    def visit(node: ET.Element, path: tuple[int, ...], inherited: dict, transforms: list[str], seen: frozenset = frozenset()) -> None:
        tag = local(node.tag)
        if tag not in {"svg", "g", "use", "path", "circle", "rect", "ellipse", "line", "polyline", "polygon"}:
            return
        style = {**inherited, **{key: value for key, value in node.attrib.items() if key in PRESENTATION | {"fill", "stroke"}}}
        transforms = [*transforms, *([node.get("transform")] if node.get("transform") else [])]
        if tag == "use":
            href = node.get("{http://www.w3.org/1999/xlink}href", "")
            if not href.startswith("#") or href[1:] not in references or href in seen:
                raise ValueError("Unexpected source SVG reference")
            target, target_path = references[href[1:]]
            visit(target, target_path, style, transforms, seen | {href})
            return
        if tag in {"svg", "g"}:
            for at, child in enumerate(node):
                visit(child, path + (at,), style, transforms, seen)
            return
        bucket = component(path)
        if bucket is None:
            return
        attrs = {key: value for key, value in node.attrib.items() if key in GEOMETRY}
        attrs.update({key: value for key, value in style.items() if key in PRESENTATION})
        attrs["fill"] = paint(style.get("fill", "#000"), "currentColor")
        attrs["stroke"] = "currentColor" if style.get("stroke", "none") != "none" else "none"
        if attrs["stroke"] != "none":
            attrs["stroke-width"] = str(max(1.2, float(style.get("stroke-width", "1"))))
        if transforms:
            attrs["transform"] = " ".join(transforms)
        attrs["data-source-id"] = "commons:" + ".".join(map(str, path))
        buckets[bucket].append([tag, attrs])

    visit(root, (), {}, [])
    openclipart = read_source("openclipart-bicycle.original.svg")
    lamp = next(node for node in openclipart.iter() if node.get("id") == "path10148")
    buckets["lights"].append(["path", {
        "d": lamp.attrib["d"], "transform": "translate(656 170) scale(-2 2) translate(-273 -300)",
        "fill": "var(--paper)", "stroke": "currentColor", "stroke-width": "1.2",
        "data-source-id": "openclipart:path10148",
    }])
    if any(not buckets[name] for name in ORDER):
        raise ValueError("A sourced bicycle part has no geometry")
    return {
        "viewBox": "250 65 565 355", "context": buckets.pop("context"), "parts": buckets,
        "attribution": {
            "title": "Bicycle diagram", "author": "Al2 and Wikimedia Commons contributors",
            "source": "https://commons.wikimedia.org/wiki/File:Bicycle_diagram.svg",
            "license": "CC BY 3.0", "licenseUrl": "https://creativecommons.org/licenses/by/3.0/",
            "changes": "Adapted: labels, callouts, shadows and repeated tread removed; cropped, recolored and regrouped for interaction; headlight added from Openclipart.",
            "supplementary": [{"title": "Bicycle (headlight)", "author": "WRomey",
                "source": "https://openclipart.org/detail/3891/bicycle", "license": "CC0 1.0",
                "licenseUrl": "https://creativecommons.org/publicdomain/zero/1.0/",
                "changes": "Headlight path extracted, reflected, scaled and positioned on the bicycle; recolored."}],
        },
    }


def descendants(root: ET.Element):
    def walk(node, path):
        yield node, path
        for at, child in enumerate(node):
            yield from walk(child, path + (at,))
    return walk(root, ())


def javascript(data: dict) -> bytes:
    header = "// Generated offline by scripts/prepare_bicycle_art.py; do not hand-edit.\n// Bicycle: Al2 et al., CC BY 3.0; headlight: WRomey, CC0. See docs/references/bicycle/NOTICE.md.\n"
    return (header + "window.mendBicycleArt = Object.freeze(" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ");\n").encode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify generated bytes without writing")
    args = parser.parse_args()
    data = prepare()
    rendered = javascript(data)
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != rendered:
            parser.error("scripts/data/approved-bicycle-art.js is stale; run this script without --check")
        print("Pinned sources and generated bicycle artwork match")
    else:
        OUTPUT.write_bytes(rendered)
        print(f"Prepared {sum(len(v) for v in data['parts'].values())} sourced part shapes, {len(rendered)} JS bytes")


if __name__ == "__main__":
    main()
