"use strict";

const clean = (value) => String(value || "").normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
let catalog;
let entries = [];
let pending;
let scheduled = false;

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

function search(request) {
  if (!catalog) {
    pending = request;
    return;
  }
  const start = performance.now();
  const query = clean(request.query).trim();
  const tokens = query.split(/\s+/).filter(Boolean);
  const top = [];
  let total = 0;
  for (let index = 0; index < entries.length; index++) {
    const entry = entries[index];
    if (request.category && entry.row[2] !== request.category) continue;
    if (!tokens.every((token) => entry.text.includes(token))) continue;
    let score = entry.title === query ? 1000 : entry.title.startsWith(query) ? 500 : entry.title.includes(query) ? 250 : 0;
    if (entry.row[3] === "guide") score += 15;
    for (const token of tokens) {
      if (entry.title.startsWith(token) || entry.title.includes(` ${token}`)) score += 35;
      else if (entry.title.includes(token)) score += 10;
    }
    total += 1;
    if (top.length === request.limit && score <= top[top.length - 1].score) continue;
    let low = 0, high = top.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (top[middle].score >= score) low = middle + 1;
      else high = middle;
    }
    top.splice(low, 0, { index, score });
    if (top.length > request.limit) top.pop();
  }
  const items = top.map((match) => catalog.items[match.index]);
  postMessage({ type: "results", requestId: request.requestId, query: request.query, category: request.category,
    total, items, duration: performance.now() - start });
}

self.onmessage = ({ data }) => {
  if (data.type === "search") schedule(data);
};

(async () => {
  try {
    const start = performance.now();
    const response = await fetch("/api/catalog", { credentials: "same-origin" });
    if (!response.ok) throw new Error(`The repair library is unavailable (${response.status}).`);
    catalog = await response.json();
    if (!Array.isArray(catalog.items) || !Array.isArray(catalog.categories)) throw new Error("The repair library format is invalid.");
    const categories = new Map(catalog.categories.map((category) => [category.id, category.name]));
    entries = catalog.items.map((row) => {
      const title = clean(row[1]);
      return { row, title, text: `${title} ${clean(row[4])} ${clean(categories.get(row[2]))}` };
    });
    postMessage({ type: "ready", total: entries.length, categories: catalog.categories, source: catalog.source,
      revision: catalog.revision, loadDuration: performance.now() - start });
    if (pending) schedule(pending);
  } catch (error) {
    postMessage({ type: "error", message: error.message || "The repair library could not be loaded." });
  }
})();
