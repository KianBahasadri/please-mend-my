(() => {
  "use strict";

  const scope = globalThis;
  const CATALOG = "pmm-catalog-v1", GUIDES = "pmm-guides-v1";
  const CATALOG_URL = "/api/catalog", CANDIDATES = "/__pmm_catalog_candidate__/";
  const CATALOG_GENERATION = "/__pmm_catalog_generation__";
  const GUIDE_META = "/__pmm_guides_state__", GUIDE_LIMIT = 500;
  const listeners = new Set();
  const stats = { catalogSource: "", catalogReadMs: 0, catalogParseMs: 0,
    probes: 0, unchanged: 0, staged: 0, guideHits: 0, guideMisses: 0, storageFailures: 0 };
  let currentEtag = "", guideState, guideSerial = Promise.resolve(), catalogSerial = Promise.resolve(), probe;
  const now = () => scope.performance?.now?.() ?? Date.now();
  const origin = scope.location?.origin || "http://localhost";
  const options = { credentials: "same-origin", mode: "same-origin", redirect: "error" };
  const validEtag = value => typeof value === "string" && value.length > 0 && value.length <= 256 && !/[\x00-\x1f\x7f]/.test(value);
  const etagOf = response => response?.headers?.get("ETag") || "";
  const storageFailure = () => { stats.storageFailures++; };
  const discard = response => { try { response?.body?.cancel?.().catch?.(() => {}); } catch {} };
  const emit = event => { for (const listener of listeners) { try { listener(event); } catch {} } };
  const serial = action => {
    const result = guideSerial.catch(() => {}).then(action);
    guideSerial = result.catch(() => {});
    return result;
  };
  const exclusiveCatalog = action => {
    if (typeof scope.navigator?.locks?.request === "function") {
      return scope.navigator.locks.request("pmm-catalog-publication", { mode: "exclusive" }, action);
    }
    const result = catalogSerial.catch(() => {}).then(action);
    catalogSerial = result.catch(() => {});
    return result;
  };

  async function cache(name) {
    try { return typeof scope.caches?.open === "function" ? await scope.caches.open(name) : null; }
    catch { storageFailure(); return null; }
  }

  function candidateURL(value) {
    if (typeof value !== "string" || value.length > 900) return "";
    try {
      const url = new URL(value, origin);
      return url.origin === origin && url.pathname.startsWith(CANDIDATES) &&
        !url.search && !url.hash && /^[a-zA-Z0-9%._~/-]+$/.test(url.pathname) ? url.pathname : "";
    } catch { return ""; }
  }

  async function activeEtag(signal) {
    const store = await cache(CATALOG);
    checkGuideAbort(signal);
    if (!store) return "";
    try {
      const response = await store.match(CATALOG_URL);
      checkGuideAbort(signal, response);
      const etag = etagOf(response); discard(response);
      return etag;
    } catch { checkGuideAbort(signal); storageFailure(); return ""; }
  }

  async function guideEtag(signal) {
    const store = await cache(CATALOG);
    checkGuideAbort(signal);
    if (!store) return "";
    try {
      const response = await store.match(CATALOG_GENERATION);
      checkGuideAbort(signal, response);
      const etag = etagOf(response); discard(response);
      if (validEtag(etag)) return etag;
    } catch { checkGuideAbort(signal); storageFailure(); }
    // Compatibility with catalogs saved before the tiny generation marker.
    // Publication/CAS still checks the authoritative catalog response itself.
    return activeEtag(signal);
  }

  async function saveGuideGeneration(store, etag) {
    if (!store || !validEtag(etag)) return;
    try {
      await store.put(CATALOG_GENERATION, new Response(null, { headers: { ETag: etag } }));
    } catch {
      storageFailure();
      // A stale marker must not survive a successful catalog replacement.
      try { await store.delete(CATALOG_GENERATION); } catch {}
    }
  }

  async function stage(response, baseEtag) {
    const etag = etagOf(response), headers = new Headers(response.headers);
    headers.set("X-PMM-Base-Catalog-ETag", baseEtag || "");
    const duplicate = new Response(response.clone().body, { status: response.status, statusText: response.statusText, headers });
    const store = await cache(CATALOG);
    if (!store || !validEtag(etag)) { discard(duplicate); return ""; }
    const key = CANDIDATES + encodeURIComponent(etag) + "/" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 11);
    try {
      await store.put(key, duplicate);
      // Orphaned attempts are bounded; never remove the last validated catalog.
      const keys = (await store.keys()).filter(request => candidateURL(request.url));
      for (const stale of keys.slice(0, Math.max(0, keys.length - 3))) if (stale.url !== new URL(key, origin).href) await store.delete(stale);
      return key;
    } catch { discard(duplicate); storageFailure(); return ""; }
  }

  function validCatalog(value) {
    return value && value.version === 1 && typeof value.revision === "string" &&
      Array.isArray(value.items) && Array.isArray(value.categories);
  }

  async function loadCatalog({ candidateKey = "", forceNetwork = false } = {}) {
    const started = now(), store = await cache(CATALOG);
    let response, source = "network", key = "", staging;
    if (candidateKey) {
      key = candidateURL(candidateKey);
      if (!key || !store) throw new Error("The staged catalog is unavailable.");
      response = await store.match(key);
      source = "candidate";
      if (!response) throw new Error("The staged catalog is unavailable.");
    } else if (!forceNetwork && store) {
      try { response = await store.match(CATALOG_URL); } catch { storageFailure(); }
      if (response) source = "cache";
    }
    if (!response) {
      const baseline = store ? await activeEtag() : "";
      response = await scope.fetch(CATALOG_URL, { ...options, cache: forceNetwork ? "no-store" : "default" });
      if (!response.ok || response.status !== 200) {
        const error = new Error(`The repair library is unavailable (${response.status}).`);
        error.status = response.status; discard(response); throw error;
      }
      // Disk streaming can happen alongside JSON parsing, but publication waits
      // for the caller's full inverted-index/schema validation.
      staging = stage(response, baseline);
    }
    const readMs = now() - started, parseAt = now(), etag = etagOf(response);
    let catalog;
    try {
      if (response.status !== 200 || !response.ok) throw new Error("Invalid cached HTTP response.");
      if (source !== "network" && !validEtag(etag)) throw new Error("Invalid cached catalog validator.");
      catalog = await response.json();
      if (!validCatalog(catalog)) throw new Error("Invalid cached catalog.");
    } catch (error) {
      await rejectCatalog({ etag, candidateKey: key, source, staging });
      if (source === "cache" && !forceNetwork) return loadCatalog({ forceNetwork: true });
      throw error;
    }
    const parseMs = now() - parseAt;
    Object.assign(stats, { catalogSource: source, catalogReadMs: readMs, catalogParseMs: parseMs });
    return { catalog, etag, source, candidateKey: key, staging, timings: { readMs, parseMs } };
  }

  async function rejectCatalog(entry = {}) {
    const store = await cache(CATALOG);
    if (!store) return;
    try {
      const key = candidateURL(entry.staging ? await entry.staging : entry.candidateKey);
      if (key) await store.delete(key);
      else if (entry.source === "cache" && await activeEtag() === entry.etag) {
        await store.delete(CATALOG_URL);
        await store.delete(CATALOG_GENERATION);
      }
    } catch { storageFailure(); }
  }

  async function acceptCatalog(entry = {}) {
    const candidate = entry.staging ? await entry.staging : entry.candidateKey;
    const etag = entry.etag || "", key = candidateURL(candidate);
    if (candidate && !key) return false;
    return exclusiveCatalog(async () => {
      const store = await cache(CATALOG);
      const previous = await activeEtag();
      if (key) {
        if (!store) return false;
        try {
          const response = await store.match(key);
          if (!response || !response.ok || etagOf(response) !== etag) { discard(response); return false; }
          if (previous !== etag && response.headers.get("X-PMM-Base-Catalog-ETag") !== previous) {
            discard(response); await store.delete(key); return false;
          }
          if (previous !== etag) await store.put(CATALOG_URL, response); else discard(response);
          await store.delete(key);
          if (await activeEtag() !== etag) return false;
        } catch { storageFailure(); return false; }
      } else if (entry.source !== "network" && previous !== etag) return false;
      // The validated index can be stored before the visible worker swaps.
      // Guide reads keep the previous generation until that swap commits.
      if (entry.deferGuides) return true;
      const marker = await storedGuideEtag(store);
      currentEtag = etag;
      await saveGuideGeneration(store, etag);
      if ((previous && etag && previous !== etag) || (marker && etag && marker !== etag)) {
        await retireGuides(etag);
      }
      return true;
    });
  }

  async function storedGuideEtag(store) {
    if (!store) return "";
    try {
      const response = await store.match(CATALOG_GENERATION);
      const etag = etagOf(response);
      discard(response);
      return validEtag(etag) ? etag : "";
    } catch { storageFailure(); return ""; }
  }

  async function retireGuides(etag) {
    guideState = null;
    try { await scope.caches?.delete?.(GUIDES); } catch { storageFailure(); }
    emit({ type: "catalog-changed", etag });
  }

  async function publishCatalog(etag) {
    if (!validEtag(etag)) return false;
    return exclusiveCatalog(async () => {
      const store = await cache(CATALOG);
      const active = await activeEtag();
      if (active && active !== etag) return false;
      const marker = await storedGuideEtag(store);
      currentEtag = etag;
      await saveGuideGeneration(store, etag);
      if (marker !== etag) await retireGuides(etag);
      return true;
    });
  }

  async function probeCatalog(etag) {
    if (probe) return probe;
    probe = (async () => {
      if (scope.navigator?.onLine === false) return null;
      try {
        stats.probes++;
        const check = await scope.fetch(CATALOG_URL, { ...options, cache: "no-store", method: "HEAD",
          headers: validEtag(etag) ? { "If-None-Match": etag } : {} });
        if (check.status === 304 || check.ok && etag && etagOf(check) === etag) {
          stats.unchanged++; discard(check); return null;
        }
        if (!check.ok || check.status !== 200) { discard(check); return null; }
        const expected = etagOf(check); discard(check);
        const response = await scope.fetch(CATALOG_URL, { ...options, cache: "no-store" });
        if (!response.ok || response.status !== 200 || !validEtag(etagOf(response)) ||
            expected && etagOf(response) !== expected) { discard(response); return null; }
        const next = etagOf(response), key = await stage(response, etag); discard(response);
        if (!key) return null;
        stats.staged++;
        return { etag: next, candidateKey: key };
      } catch { return null; }
    })();
    try { return await probe; } finally { probe = null; }
  }

  function guideKey(id, etag) {
    return "/api/guides/" + encodeURIComponent(id) + "?catalog=" + encodeURIComponent(etag);
  }

  async function stateFor(etag, refresh = false) {
    if (!refresh && guideState?.etag === etag) return guideState;
    const store = await cache(GUIDES);
    if (!store) return null;
    const order = new Map();
    try {
      const metadata = await store.match(GUIDE_META);
      const state = metadata ? await metadata.json() : null;
      if (state?.etag === etag && Array.isArray(state.keys)) {
        for (const key of state.keys.slice(-GUIDE_LIMIT)) if (typeof key === "string" &&
            key.startsWith("/api/guides/") && new URL(key, origin).searchParams.get("catalog") === etag) order.set(key, true);
      } else {
        for (const request of await store.keys()) {
          const url = new URL(request.url);
          if (url.pathname.startsWith("/api/guides/") && url.searchParams.get("catalog") === etag) order.set(url.pathname + url.search, true);
        }
      }
    } catch { storageFailure(); }
    guideState = { etag, store, order };
    return guideState;
  }

  async function remember(state, key) {
    state.order.delete(key); state.order.set(key, true);
    while (state.order.size > GUIDE_LIMIT) {
      const oldest = state.order.keys().next().value;
      state.order.delete(oldest); await state.store.delete(oldest);
    }
    await state.store.put(GUIDE_META, new Response(JSON.stringify({ etag: state.etag, keys: [...state.order.keys()] }),
      { headers: { "Content-Type": "application/json" } }));
  }

  function changed(expected, actual) {
    if (actual && expected !== actual) {
      emit({ type: "catalog-changed", etag: actual });
      return true;
    }
    return false;
  }

  function validGuide(detail, id) {
    return detail && detail.id === id && typeof detail.title === "string" &&
      (typeof detail.html === "string" || Array.isArray(detail.steps));
  }

  function checkGuideAbort(signal, response) {
    if (!signal?.aborted) return;
    discard(response);
    throw new DOMException("Aborted", "AbortError");
  }

  function abortableGuide(task, signal) {
    if (!signal) return task();
    return new Promise((resolve, reject) => {
      let finished = false;
      const abort = () => {
        if (finished) return;
        finished = true;
        signal.removeEventListener("abort", abort);
        reject(new DOMException("Aborted", "AbortError"));
      };
      signal.addEventListener("abort", abort, { once: true });
      if (signal.aborted) { abort(); return; }
      // CacheStorage does not accept AbortSignal. Release the caller's fetch
      // slot immediately; the late native read is checked before further work.
      task().then(response => {
        signal.removeEventListener("abort", abort);
        if (finished) { discard(response); return; }
        finished = true;
        resolve(response);
      }, error => {
        signal.removeEventListener("abort", abort);
        if (finished) return;
        finished = true;
        reject(error);
      });
    });
  }

  async function fetchGuide(id, { etag = currentEtag, signal } = {}) {
    if (typeof id !== "string" || !/^[a-zA-Z0-9_-]{1,64}$/.test(id)) throw new Error("Invalid guide identifier.");
    checkGuideAbort(signal);
    return abortableGuide(() => readGuide(id, etag, signal), signal);
  }

  async function readGuide(id, etag, signal) {
    if (!validEtag(etag)) etag = await guideEtag(signal);
    checkGuideAbort(signal);
    const key = guideKey(id, etag);
    if (validEtag(etag) && await guideEtag(signal) === etag) {
      checkGuideAbort(signal);
      // Reading a guide must never queue behind prefetch persistence, full
      // response validation or an LRU write. Only mutations are serialized.
      const response = await (async () => {
        const store = await cache(GUIDES);
        checkGuideAbort(signal);
        if (!store) return null;
        const hit = await store.match(key);
        checkGuideAbort(signal, hit);
        if (!hit || hit.status !== 200 || hit.headers.get("X-PMM-Catalog-ETag") !== etag) { discard(hit); return null; }
        try {
          const detail = await hit.clone().json();
          checkGuideAbort(signal, hit);
          if (!validGuide(detail, id)) throw new Error("Invalid cached guide.");
        } catch (error) {
          discard(hit);
          checkGuideAbort(signal);
          await store.delete(key); return null;
        }
        // Web Locks serialize updates with other tabs/workers and catalog
        // publication. Refresh the small LRU metadata while holding the lock;
        // another context may have inserted guides since this one's last hit.
        void serial(() => exclusiveCatalog(async () => {
          if (signal?.aborted || await guideEtag(signal) !== etag || signal?.aborted) return;
          const latest = await stateFor(etag, true);
          if (latest && !signal?.aborted) await remember(latest, key);
        })).catch(error => { if (error?.name !== "AbortError") storageFailure(); });
        return hit;
      })().catch(() => { checkGuideAbort(signal); storageFailure(); return null; });
      checkGuideAbort(signal, response);
      if (response) {
        const latest = await guideEtag(signal);
        checkGuideAbort(signal, response);
        if (latest && changed(etag, latest)) { discard(response); throw new Error("Catalog changed."); }
        stats.guideHits++; return response;
      }
    }
    checkGuideAbort(signal);
    stats.guideMisses++;
    const response = await scope.fetch("/api/guides/" + encodeURIComponent(id), { ...options, signal });
    checkGuideAbort(signal, response);
    const actual = response.headers?.get("X-PMM-Catalog-ETag");
    if (etag && actual && changed(etag, actual)) { discard(response); throw new Error("Catalog changed."); }
    if (response.ok && response.status === 200 && actual === etag && validEtag(etag)) {
      const copy = response.clone();
      void serial(() => exclusiveCatalog(async () => {
        try {
          if (signal?.aborted) { discard(copy); return; }
          const detail = await copy.clone().json();
          if (signal?.aborted) { discard(copy); return; }
          if (!validGuide(detail, id)) { discard(copy); return; }
          if (signal?.aborted || await guideEtag(signal) !== etag || signal?.aborted) { discard(copy); return; }
          const state = await stateFor(etag, true);
          if (!state || signal?.aborted) { discard(copy); return; }
          await state.store.put(key, copy);
          if (signal?.aborted || await guideEtag() !== etag || signal?.aborted) { await state.store.delete(key); return; }
          try { await remember(state, key); }
          catch (error) { await state.store.delete(key); throw error; }
        } catch (error) { discard(copy); if (error?.name !== "AbortError") storageFailure(); }
      })).catch(storageFailure);
    }
    return response;
  }

  async function registerShell() {
    try {
      if (!scope.navigator?.serviceWorker || !/^https?:$/.test(scope.location?.protocol || "")) return null;
      const registration = await scope.navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" });
      void registration.update?.().catch?.(() => {});
      return registration;
    } catch { return null; }
  }

  scope.mendClientCache = Object.freeze({ loadCatalog, probeCatalog, acceptCatalog, publishCatalog, rejectCatalog, fetchGuide,
    registerShell, subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    idle: () => guideSerial, stats });
})();
