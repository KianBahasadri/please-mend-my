"use strict";

// The server replaces this token using every embedded shell asset's body hash.
const SHELL_VERSION = "__PMM_SHELL_VERSION__";
const SHELL_PREFIX = "pmm-shell-v1-";
const SHELL_CACHE = SHELL_PREFIX + SHELL_VERSION;
const MANIFEST = "/shell-manifest.json";
let shellPaths;

async function digest(body) {
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", body));
  return Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
}

function validManifest(value) {
  if (!value || value.version !== SHELL_VERSION || !Array.isArray(value.assets) || value.assets.length > 200) return false;
  const seen = new Set();
  return value.assets.length > 0 && value.assets.every(item => {
    if (!item || typeof item.url !== "string" || !/^[a-f0-9]{64}$/.test(item.sha256)) return false;
    const url = new URL(item.url, self.location.origin);
    if (url.origin !== self.location.origin || url.search || url.hash || seen.has(url.pathname) ||
        !(url.pathname === "/" || url.pathname === "/credits" || url.pathname.startsWith("/assets/"))) return false;
    seen.add(url.pathname);
    return true;
  });
}

self.addEventListener("install", event => {
  event.waitUntil((async () => {
    const response = await fetch(MANIFEST, { credentials: "same-origin", mode: "same-origin", cache: "no-store" });
    if (!response.ok) throw new Error("Shell manifest unavailable.");
    const manifest = await response.json();
    if (!validManifest(manifest)) throw new Error("Shell version mismatch.");
    const store = await caches.open(SHELL_CACHE);
    try {
      let cursor = 0;
      async function download() {
        while (cursor < manifest.assets.length) {
          const asset = manifest.assets[cursor++];
          const result = await fetch(asset.url, { credentials: "same-origin", mode: "same-origin", cache: "reload" });
          if (!result.ok || result.status !== 200 || await digest(await result.clone().arrayBuffer()) !== asset.sha256) {
            throw new Error("Shell asset changed while installing.");
          }
          await store.put(asset.url, result);
        }
      }
      await Promise.all(Array.from({ length: Math.min(4, manifest.assets.length) }, download));
      await store.put(MANIFEST, new Response(JSON.stringify(manifest), { headers: { "Content-Type": "application/json" } }));
      await self.skipWaiting();
    } catch (error) {
      await caches.delete(SHELL_CACHE);
      throw error;
    }
  })());
});

self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) if (name.startsWith(SHELL_PREFIX) && name !== SHELL_CACHE) await caches.delete(name);
    await self.clients.claim();
  })());
});

async function paths() {
  if (shellPaths) return shellPaths;
  const store = await caches.open(SHELL_CACHE);
  const response = await store.match(MANIFEST);
  if (!response) return new Set();
  const manifest = await response.json();
  if (!validManifest(manifest)) return new Set();
  shellPaths = new Set(manifest.assets.map(item => item.url));
  return shellPaths;
}

self.addEventListener("fetch", event => {
  const request = event.request, url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || request.headers.has("Range")) return;
  if (!(url.pathname === "/" || url.pathname === "/index.html" || url.pathname === "/credits" || url.pathname.startsWith("/assets/"))) return;
  event.respondWith((async () => {
    try {
      const key = url.pathname === "/index.html" ? "/" : url.pathname;
      if ((await paths()).has(key)) {
        const store = await caches.open(SHELL_CACHE);
        const cached = await store.match(key);
        if (cached) return cached;
      }
    } catch { /* Storage may be unavailable; keep normal online navigation. */ }
    return fetch(request);
  })());
});
