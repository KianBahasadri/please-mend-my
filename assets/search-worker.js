"use strict";

const clean = (value) => String(value || "").normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
let catalog;
let entries = [];
let pending;
let scheduled = false;
let ranked = [];
let activeRequest = 0;

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
  const buckets = new Map();
  for (let index = 0; index < entries.length; index++) {
    const entry = entries[index];
    if (!tokens.every((token) => entry.text.includes(token))) continue;
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
    entries = catalog.items.map((row) => {
      const title = clean(row[1]);
      return { row, title, text: `${title} ${clean(row[4])} ${clean(categories.get(row[2]))}` };
    });
    postMessage({ type: "ready", total: entries.length, categories: catalog.categories, source: catalog.source,
      revision: catalog.revision, loadDuration: performance.now() - started });
    if (pending) schedule(pending);
  } catch (error) {
    postMessage({ type: "error", message: error.message || "The repair library could not be loaded." });
  }
})();
