"use strict";

const clean = (value) => String(value || "").normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
let catalog;
let entries = [];
let pending;
let scheduled = false;
let ranked = [];
let activeRequest = 0;
let searchGroups = [""];
let itemGroups;

function schedule(request) {
  pending = request;
  if (!catalog || scheduled) return;
  scheduled = true;
  setTimeout(() => {
    scheduled = false;
    const latest = pending;
    pending = null;
    if (latest) search(latest);
  }, 0);
}

function sendRange(request) {
  if (request.requestId !== activeRequest) return;
  const start = Math.max(0, Math.min(ranked.length, request.start || 0));
  const end = Math.min(ranked.length, start + Math.min(60, request.count || 30));
  postMessage({ type: "range", requestId: activeRequest, rangeId: request.rangeId,
    start, items: ranked.slice(start, end).map((index) => catalog.items[index]) });
}

function search(request) {
  const started = performance.now();
  const query = clean(request.query).trim();
  const tokens = query.split(/\s+/).filter(Boolean);
  // Match shared ancestry/alias text once per group, rather than duplicating it
  // across 100k documents. No network access occurs during a search.
  const compact = tokens.length <= 31;
  const groupMasks = new Uint32Array(searchGroups.length);
  const wideMatches = compact ? null : tokens.map(() => new Uint8Array(searchGroups.length));
  for (let group = 1; group < searchGroups.length; group++) {
    for (let term = 0; term < tokens.length; term++) {
      if (searchGroups[group].includes(tokens[term])) {
        if (compact) groupMasks[group] |= 1 << term;
        else wideMatches[term][group] = 1;
      }
    }
  }
  const buckets = new Map();
  for (let index = 0; index < entries.length; index++) {
    const entry = entries[index];
    const group = itemGroups[index];
    const aliasMask = groupMasks[group];
    let matches = true;
    for (let term = 0; term < tokens.length; term++) {
      if ((compact ? aliasMask & (1 << term) : wideMatches[term][group]) || entry.text.includes(tokens[term])) continue;
      matches = false;
      break;
    }
    if (!matches) continue;
    let score = entry.title === query ? 1000 : entry.title.startsWith(query) ? 500 : entry.title.includes(query) ? 250 : 0;
    if (entry.row[3] === "guide") score += 15;
    for (const token of tokens) {
      if (entry.title.startsWith(token) || entry.title.includes(` ${token}`)) score += 35;
      else if (entry.title.includes(token)) score += 10;
    }
    if (!buckets.has(score)) buckets.set(score, []);
    buckets.get(score).push(index);
  }
  // Rank the entire match set once. Only viewport rows cross to the main thread.
  ranked = [];
  for (const score of [...buckets.keys()].sort((a, b) => b - a)) {
    for (const index of buckets.get(score)) ranked.push(index);
  }
  activeRequest = request.requestId;
  postMessage({ type: "results", requestId: activeRequest, total: ranked.length,
    items: ranked.slice(0, request.count || 30).map((index) => catalog.items[index]), duration: performance.now() - started });
}

self.onmessage = ({ data }) => {
  if (data.type === "search") schedule(data);
  else if (data.type === "range") sendRange(data);
};

(async () => {
  try {
    const started = performance.now();
    const response = await fetch("/api/catalog", { credentials: "same-origin", mode: "same-origin" });
    if (!response.ok) throw new Error(`The repair library is unavailable (${response.status}).`);
    catalog = await response.json();
    if (!Array.isArray(catalog.items) || !Array.isArray(catalog.categories)) throw new Error("The repair library format is invalid.");
    const categories = new Map(catalog.categories.map((category) => [category.id, category.name]));
    itemGroups = new Uint32Array(catalog.items.length);
    if (catalog.search) {
      const metadata = catalog.search;
      if (metadata.version !== 1 || !Array.isArray(metadata.groups) || metadata.groups[0] !== "" ||
          !Array.isArray(metadata.itemGroups) || metadata.itemGroups.length !== catalog.items.length ||
          !metadata.groups.every((group) => typeof group === "string") ||
          !metadata.itemGroups.every((group) => Number.isInteger(group) && group >= 0 && group < metadata.groups.length)) {
        throw new Error("The prepared search alias format is invalid.");
      }
      searchGroups = metadata.groups;
      itemGroups = Uint32Array.from(metadata.itemGroups);
      delete metadata.itemGroups;
    }
    entries = catalog.items.map((row) => {
      const title = clean(row[1]);
      return { row, title, text: `${title} ${clean(row[4])} ${clean(categories.get(row[2]))}` };
    });
    postMessage({ type: "ready", total: entries.length, categories: catalog.categories, source: catalog.source,
      revision: catalog.revision, aliasGroups: searchGroups.length - 1, loadDuration: performance.now() - started });
    if (pending) schedule(pending);
  } catch (error) {
    postMessage({ type: "error", message: error.message || "The repair library could not be loaded." });
  }
})();
