#!/usr/bin/env python3
"""Generate static diagram credits from the published pilot manifest, offline."""

from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
from urllib.parse import urlsplit

START = "<!-- diagram-pilot-credits:start -->"
END = "<!-- diagram-pilot-credits:end -->"


def link(label: str, url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Diagram credit links must be public HTTP(S) URLs")
    return f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(label)}</a>'


def render(manifest: dict) -> str:
    if manifest.get("version") != 1 or not isinstance(manifest.get("archetypes"), list):
        raise ValueError("Invalid published diagram manifest")
    entries = []
    for entry in manifest["archetypes"]:
        if not entry.get("artUrl"):
            continue
        source = entry.get("attribution")
        if not isinstance(source, dict):
            raise ValueError("Published diagrams need source attribution")
        sources = [source, *source.get("supplementary", [])]
        for number, record in enumerate(sources):
            title = record["title"]
            heading = f'{entry.get("label", entry["id"])} — {title}' if number == 0 else title
            entries.append("\n".join([
                "          <article>",
                "            <h3>" + link(heading, record["source"]) + "</h3>",
                "            <p>By " + escape(record["author"]) + ".</p>",
                '            <p class="license">' + link(record["license"], record["licenseUrl"]) + "</p>",
                '            <p class="detail">' + escape(record["changes"]) + "</p>",
                "          </article>",
            ]))
    if not entries:
        return ""
    return "\n".join([
        '      <section aria-labelledby="diagram-heading">',
        '        <h2 id="diagram-heading">More diagrams</h2>',
        '        <div class="entries">',
        *entries,
        '          <p class="detail">Adapted for this project. The source artists do not endorse the site.</p>',
        "        </div>",
        "      </section>",
    ])


def update_credits(page: Path, manifest: dict) -> None:
    text = page.read_text()
    if text.count(START) != 1 or text.count(END) != 1:
        raise ValueError("Credits page needs one diagram section marker pair")
    before, remainder = text.split(START, 1)
    _, after = remainder.split(END, 1)
    body = render(manifest)
    updated = before + START + "\n" + (body + "\n      " if body else "      ") + END + after
    if updated != text:
        page.write_text(updated)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=root / "assets/art/manifest.json")
    parser.add_argument("--page", type=Path, default=root / "credits.html")
    args = parser.parse_args()
    update_credits(args.page, json.loads(args.manifest.read_text()))


if __name__ == "__main__":
    main()
