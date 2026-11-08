"""Bounded source downloads and atomic, alias-enriched catalog snapshots."""
from __future__ import annotations

from collections import Counter
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import unicodedata
from urllib.request import urlopen
import xml.etree.ElementTree as ET

FIELDS = ["id", "title", "category", "kind", "device"]
CATEGORIES = [
    {"id": "technology", "name": "Technology"},
    {"id": "household", "name": "Household & appliances"},
    {"id": "handyman", "name": "Everyday objects & DIY"},
    {"id": "vehicles", "name": "Vehicles & transport"},
    {"id": "health", "name": "Health & medicine"},
    {"id": "other", "name": "Other repairs & reference"},
]
# Classification only: object/part discovery remains driven by the noun database.
RULES = [(key, re.compile(pattern, re.I)) for key, pattern in [
    ("health", r"\b(?:first aid|medical|medicine|fracture|bleeding|injur\w*|wound\w*|burns?|sprain\w*|disease\w*|symptoms?|health|surg\w*)\b"),
    ("technology", r"\b(?:computer\w*|laptop\w*|phone\w*|software|windows|linux|android|iphone|ipad|macbook|printer\w*|wifi|wi-fi|bluetooth|internet|electronic\w*|camera\w*)\b"),
    ("vehicles", r"\b(?:cars?|automotive|vehicles?|motorcycles?|bicycles?|bikes?|truck\w*|tires?|tyres?|engine\w*|automobile\w*)\b"),
    ("household", r"\b(?:appliance\w*|refrigerator\w*|fridge\w*|stoves?|ovens?|dishwash\w*|washing machine|laundry|vacuum\w*|microwave\w*|toilets?|faucets?)\b"),
    ("handyman", r"\b(?:repair\w*|mend\w*|sew\w*|jeans?|garment\w*|drywall|plumb\w*|pipes?|hinges?|doors?|roof\w*|construct\w*|build\w*|solar|water|irrigat\w*|agricultur\w*|garden\w*|compost\w*|sustainab\w*|energy|sanitation|soil|earth|recycl\w*)\b"),
]]


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def atomic_write(path, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(body)
    temporary.chmod(0o644)
    temporary.replace(path)


def normalize(value):
    return " ".join("".join(char for char in unicodedata.normalize("NFKD", value)
                            if not unicodedata.category(char).startswith("M")).lower().split())


def classify(title, categories=(), default="other"):
    # Source category names carry more domain information than article prose.
    for value in (" ".join(categories).replace("_", " "), re.sub(r"(?<=[A-Za-z])(?=[0-9])", " ", title)):
        for key, pattern in RULES:
            if pattern.search(value):
                return key
    return default


def short_id(source, identity):
    return hashlib.sha256((source + ":" + identity).encode()).hexdigest()[:20]


def digest_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def ingestion_key(script, config):
    digest = hashlib.sha256(Path(script).read_bytes())
    digest.update(Path(__file__).read_bytes())
    digest.update(json_bytes(config))
    return digest.hexdigest()


def verify(path, config):
    path = Path(path)
    if not path.is_file() or path.stat().st_size != config["bytes"]:
        raise RuntimeError("Source absent/incomplete: " + str(path))
    checksum = digest_file(path)
    if checksum != config["sha256"]:
        raise RuntimeError("Source SHA-256 mismatch; refusing ingestion: " + str(path))
    return checksum


def download(data_dir, config, build_only=False):
    raw = Path(data_dir) / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    path = raw / config["filename"]
    if path.exists():
        verify(path, config)
        return path
    if build_only:
        raise FileNotFoundError("Download pinned source before --build-only: " + str(path))
    partial = path.with_name(path.name + ".part")
    remaining = config["bytes"] - (partial.stat().st_size if partial.exists() else 0)
    if shutil.disk_usage(raw).free < max(0, remaining) + 256 * 1024**2:
        raise RuntimeError("Not enough disk for source plus reserve; completed catalogs are untouched.")
    urls = config["urls"]
    if config.get("metalink"):
        metadata_path = raw / (config["filename"] + ".meta4")
        if not metadata_path.exists():
            with urlopen(config["metalink"], timeout=30) as response:
                payload = response.read(2 * 1024**2 + 1)
            if len(payload) > 2 * 1024**2:
                raise ValueError("Source Metalink exceeds metadata limit")
            atomic_write(metadata_path, payload)
        root = ET.fromstring(metadata_path.read_bytes())
        ns = {"m": "urn:ietf:params:xml:ns:metalink"}
        entry = root.find("m:file", ns)
        if (entry is None or entry.get("name") != config["filename"] or
                entry.findtext("m:size", namespaces=ns) != str(config["bytes"]) or
                entry.findtext("m:hash[@type='sha-256']", namespaces=ns) != config["sha256"]):
            raise ValueError("Official Metalink differs from pinned source size/SHA-256")
        mirrors = [url.text for url in entry.findall("m:url", ns) if url.text and url.text.startswith("https://")]
        urls = mirrors + [url for url in urls if url not in mirrors]
    if shutil.which("aria2c"):
        result = subprocess.run(["aria2c", "--continue=true", "--auto-file-renaming=false",
                                 "--split=4", "--max-connection-per-server=4", "--min-split-size=16M",
                                 "--file-allocation=none", "--max-tries=2", "--connect-timeout=10",
                                 "--timeout=30", "--summary-interval=30", "--console-log-level=warn",
                                 "--show-console-readout=false", "--dir=" + str(raw),
                                 "--out=" + partial.name, *urls])
        if result.returncode == 0:
            verify(partial, config)
            partial.replace(path)
            atomic_write(raw / "source.json", json_bytes(config))
            return path
    for url in urls:
        if not url.startswith("https://"):
            raise ValueError("Pinned source download must use HTTPS")
        result = subprocess.run(["curl", "--fail", "--location", "--retry", "2",
                                 "--connect-timeout", "15", "--max-time", "1200",
                                 "--continue-at", "-", "--silent", "--show-error",
                                 "--proto", "=https", "--proto-redir", "=https",
                                 "--output", str(partial), url])
        if result.returncode == 0:
            verify(partial, config)
            partial.replace(path)
            atomic_write(raw / "source.json", json_bytes(config))
            return path
    raise RuntimeError("All pinned download mirrors failed; partial source retained for resume.")


def completed(data_dir, key, config):
    data_dir = Path(data_dir)
    runtime = data_dir / "runtime"
    try:
        m = json.loads((runtime / "manifest.json").read_bytes())
        proof = "zimChecksumVerified" if config["format"] == "zim" else "datasetVerified"
        if (m.get("runtimeComplete") is True and m.get("ingestionKey") == key and
                m.get("archiveSha256") == config["sha256"] == m.get("expectedSha256") and
                m.get("archiveChecksumVerified") is True and m.get(proof) is True and
                m.get("indexedItems", 0) > 0 and m.get("searchEnrichment", {}).get("version") == 1 and
                (runtime / "index.json").stat().st_size == m["indexBytes"] and
                (runtime / "index.json.gz").stat().st_size == m["indexGzipBytes"] and
                (runtime / "guides").is_dir() and (runtime / "media").is_dir() and
                (data_dir / "catalog.sqlite").is_file()):
            return m
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


class CatalogWriter:
    """Stream guide bodies to disk; retain only compact rows for the final index."""
    def __init__(self, data_dir, config, key):
        self.data = Path(data_dir)
        self.data.mkdir(parents=True, exist_ok=True)
        self.config, self.key = config, key
        # The caller's source lock excludes another active writer. A killed job
        # may leave a private stage behind; it is not a completed source cache.
        for path in self.data.iterdir():
            if path.name.startswith('.runtime-') and path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
        self.stage = Path(tempfile.mkdtemp(prefix=".runtime-", dir=self.data))
        for name in ("guides", "media"):
            (self.stage / name).mkdir(mode=0o755)
        self.database = self.data / "catalog-building.sqlite"
        self.database.unlink(missing_ok=True)
        self.db = sqlite3.connect(self.database)
        self.db.execute("PRAGMA journal_mode=OFF")
        self.db.execute("PRAGMA synchronous=OFF")
        self.db.execute("PRAGMA cache_size=-16384")
        self.db.executescript("""
          CREATE TABLE items(id TEXT PRIMARY KEY,source_path TEXT UNIQUE,title TEXT,category TEXT,kind TEXT,device TEXT,summary TEXT,source_url TEXT);
          CREATE TABLE aliases(id TEXT PRIMARY KEY,value TEXT);
          CREATE TABLE source_categories(path TEXT PRIMARY KEY,title TEXT,category TEXT);
          CREATE TABLE category_edges(parent TEXT,child TEXT,PRIMARY KEY(parent,child));
          CREATE TABLE guide_categories(guide TEXT,category TEXT,PRIMARY KEY(guide,category));
          CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
          CREATE TABLE redirects(path TEXT PRIMARY KEY,title TEXT,target TEXT);
          CREATE TABLE media(path TEXT PRIMARY KEY,filename TEXT,mime TEXT,bytes INTEGER);
          CREATE TABLE media_attribution(article_id TEXT,url TEXT,source_path TEXT,file_page_url TEXT,PRIMARY KEY(article_id,url,source_path,file_page_url));
        """)
        self.count = self.guide_bytes = 0
        self.media_sizes = {}
        self.published = False

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.db.close()
        if not self.published:
            shutil.rmtree(self.stage, ignore_errors=True)

    def add(self, record, path, aliases=()):
        payload = json_bytes(record)
        atomic_write(self.stage / "guides" / (record["id"] + ".json"), payload)
        atomic_write(self.stage / "guides" / (record["id"] + ".json.gz"), gzip.compress(payload, compresslevel=6, mtime=0))
        self.db.execute("INSERT INTO items VALUES(?,?,?,?,?,?,?,?)", tuple(record[name] for name in ("id",)) +
                        (path,) + tuple(record[name] for name in ("title", "category", "kind", "device", "summary", "sourceUrl")))
        own = normalize(record["title"])
        group = "\n".join(sorted({normalize(alias) for alias in aliases if normalize(alias) and normalize(alias) != own}))
        self.db.execute("INSERT INTO aliases VALUES(?,?)", (record["id"], group))
        for category in record.get("sourceCategories", []):
            self.db.execute("INSERT OR IGNORE INTO source_categories VALUES(?,?,?)", (category, category.removeprefix("Category:").replace("_", " "), record["category"]))
            self.db.execute("INSERT OR IGNORE INTO guide_categories VALUES(?,?)", (path, category))
        for media in record.get("mediaSources", []):
            self.db.execute("INSERT OR IGNORE INTO media_attribution VALUES(?,?,?,?)", (record["id"], media["url"], media["sourcePath"], media.get("filePageUrl", "")))
        self.count += 1
        self.guide_bytes += len(payload)
        if self.count % 1000 == 0:
            self.db.commit()
            if shutil.disk_usage(self.data).free < 256 * 1024**2:
                raise RuntimeError("Disk reserve reached; incomplete source cannot publish")

    def add_media(self, path, payload, mime, extension):
        filename = hashlib.sha256(payload).hexdigest() + extension
        if filename not in self.media_sizes:
            atomic_write(self.stage / "media" / filename, payload)
            self.media_sizes[filename] = len(payload)
        self.db.execute("INSERT OR REPLACE INTO media VALUES(?,?,?,?)", (path, filename, mime, len(payload)))
        return "/media/" + filename

    def finish(self, extra=None):
        if not self.count:
            raise ValueError("Empty source cannot publish")
        groups = [""] + [row[0] for row in self.db.execute("SELECT DISTINCT value FROM aliases WHERE value!='' ORDER BY value")]
        group_ids = {value: i for i, value in enumerate(groups)}
        rows, references = [], []
        for row in self.db.execute("SELECT i.id,i.title,i.category,i.kind,i.device,a.value FROM items i JOIN aliases a ON a.id=i.id ORDER BY lower(i.title),i.id"):
            rows.append(list(row[:5]))
            references.append(group_ids[row[5]])
        source = {key: self.config[key] for key in ("id", "name", "url", "license", "attribution")}
        source.update(archive=self.config["filename"], sourceFormat=self.config["format"])
        search = {"version": 1, "normalization": "NFKD-no-marks-lower", "groups": groups,
                  "itemGroups": references, "enrichedItems": sum(bool(i) for i in references), "sources": [source]}
        index = {"version": 1, "revision": self.config["revision"], "source": source,
                 "categories": CATEGORIES, "fields": FIELDS, "items": rows, "search": search}
        body = json_bytes(index)
        packed = gzip.compress(body, compresslevel=9, mtime=0)
        proof = "zimChecksumVerified" if self.config["format"] == "zim" else "datasetVerified"
        manifest = {"version": 1, "revision": index["revision"], "source": source,
                    "sourceId": source["id"], "sourceFormat": self.config["format"], "ingestionKey": self.key,
                    "archiveBytes": self.config["bytes"], "archiveSha256": self.config["sha256"],
                    "expectedSha256": self.config["sha256"], "archiveChecksumVerified": True, proof: True,
                    "archiveUrl": self.config["urls"][0], "sourcePin": self.config,
                    "indexedItems": len(rows), "categoryCounts": dict(Counter(row[2] for row in rows)),
                    "uniqueMediaFiles": len(self.media_sizes), "mediaBytes": sum(self.media_sizes.values()),
                    "guideJsonBytes": self.guide_bytes, "indexBytes": len(body), "indexGzipBytes": len(packed),
                    "searchEnrichment": {"version": 1, "enrichedItems": search["enrichedItems"], "sharedGroups": len(groups)},
                    "runtimeComplete": True, **(extra or {})}
        for name, value in (("index.json", body), ("index.json.gz", packed), ("manifest.json", json_bytes(manifest))):
            atomic_write(self.stage / name, value)
        self.db.execute("INSERT INTO metadata VALUES('ingestion_key',?)", (self.key,))
        self.db.commit()
        self.db.close()
        self.stage.chmod(0o755)
        self.database.chmod(0o644)
        destination, previous = self.data / "runtime", self.data / "runtime.previous"
        if previous.exists():
            shutil.rmtree(previous)
        if destination.exists():
            destination.rename(previous)
        self.stage.rename(destination)
        self.database.replace(self.data / "catalog.sqlite")
        atomic_write(self.data / "manifest.json", json_bytes(manifest))
        self.published = True
        if previous.exists():
            shutil.rmtree(previous)
        return manifest
