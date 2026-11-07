(() => {
  "use strict";

  const input = document.getElementById("response");
  const status = document.getElementById("catalog-status");
  const results = document.getElementById("search-results");
  const cards = document.getElementById("result-cards");
  const count = document.getElementById("result-count");
  const empty = document.getElementById("no-results");
  const more = document.getElementById("more-results");
  const panel = document.getElementById("guide-panel");
  const body = document.getElementById("guide-body");
  const navigationError = document.getElementById("guide-nav-error");
  const promptLength = Number(input.dataset.promptLength);
  const categoryNames = new Map();
  const details = new Map();
  const prefetchedImages = new Set();
  const queue = [];
  const queued = new Set();
  const stats = { indexLoadMs: null, searchMs: [], inputToResultsMs: [], cachedOpenMs: [], detailRequests: 0 };
  window.mendSearchStats = stats;
  let worker;
  let ready = false;
  let total = 0;
  let limit = 12;
  let requestId = 0;
  let inputAt = 0;
  let activeFetches = 0;
  let currentGuide = null;
  let pendingGuide = null;
  let viewVersion = 0;
  let latestSource;
  let searchFrame = 0;
  let pendingSearch;

  const query = () => input.value.slice(promptLength).trim();
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const guideHref = (id) => `/?guide=${encodeURIComponent(id)}`;
  const safeUrl = (value) => {
    try {
      const url = new URL(value, location.href);
      return ["http:", "https:"].includes(url.protocol) ? url.href : "";
    } catch { return ""; }
  };

  function setStatus(message) {
    status.hidden = !message;
    status.textContent = message || "";
  }

  function runSearch() {
    requestId += 1;
    const text = query();
    const active = Boolean(text);
    document.body.classList.toggle("searching", active);
    results.hidden = !active || currentGuide !== null;
    if (!active) {
      cards.replaceChildren();
      if (searchFrame) cancelAnimationFrame(searchFrame);
      searchFrame = 0;
      return;
    }
    inputAt = performance.now();
    if (!ready) {
      results.hidden = true;
      return;
    }
    pendingSearch = { type: "search", requestId, query: text, limit };
    if (!searchFrame) searchFrame = requestAnimationFrame(() => {
      searchFrame = 0;
      worker.postMessage(pendingSearch);
    });
  }

  function cacheImages(detail) {
    for (const source of (detail.images || []).slice(0, 1)) {
      const url = safeUrl(source);
      if (!url || new URL(url).origin !== location.origin || prefetchedImages.has(url)) continue;
      prefetchedImages.add(url);
      const image = new Image();
      image.decoding = "async";
      image.src = url;
    }
  }

  function loadDetail(id) {
    let record = details.get(id);
    if (record) return record;
    let resolve, reject;
    const promise = new Promise((accept, fail) => { resolve = accept; reject = fail; });
    // Background prefetch failures are surfaced only if that item is opened.
    promise.catch(() => {});
    record = { id, promise, resolve, reject, data: null, state: "queued" };
    details.set(id, record);
    queued.add(id);
    queue.push(record);
    pump();
    return record;
  }

  function pump() {
    while (activeFetches < 4 && queue.length) {
      const record = queue.shift();
      queued.delete(record.id);
      record.state = "loading";
      activeFetches += 1;
      stats.detailRequests += 1;
      fetch(`/api/guides/${encodeURIComponent(record.id)}`, { credentials: "same-origin" })
        .then((response) => {
          if (!response.ok) throw new Error(`This repair page could not be loaded (${response.status}).`);
          return response.json();
        })
        .then((detail) => {
          record.data = detail;
          record.state = "ready";
          cacheImages(detail);
          record.resolve(detail);
          // Keep navigation fast without retaining an unbounded browsing session.
          if (details.size > 96) {
            for (const [id, entry] of details) {
              if (id !== currentGuide && id !== pendingGuide && entry.state === "ready") {
                details.delete(id);
                break;
              }
            }
          }
        })
        .catch((error) => { record.state = "error"; record.reject(error); })
        .finally(() => { activeFetches -= 1; pump(); });
    }
  }

  function renderCards(message) {
    if (message.requestId !== requestId) return;
    stats.searchMs.push(message.duration);
    const fragment = document.createDocumentFragment();
    for (const row of message.items) {
      const [id, title, categoryId, kind, device] = row;
      const card = node("a", "repair-card");
      card.href = guideHref(id);
      card.dataset.guideId = id;
      const label = kind === "guide" ? "Repair guide" : kind === "topic" ? "Device & topic" : "Repair article";
      card.append(node("p", "card-meta", `${categoryNames.get(categoryId) || categoryId} · ${label}`), node("h2", "", title));
      if (device && device !== title) card.append(node("p", "card-device", device));
      fragment.append(card);
    }
    cards.replaceChildren(fragment);
    count.textContent = `${message.total.toLocaleString()} ${message.total === 1 ? "match" : "matches"}${message.total > message.items.length ? ` · showing ${message.items.length}` : ""}`;
    empty.hidden = message.total !== 0;
    more.hidden = message.items.length >= message.total;
    const visible = new Set(message.items.map((row) => row[0]));
    // Do not let rapid typing accumulate work for cards no longer on screen.
    for (let index = queue.length - 1; index >= 0; index -= 1) {
      const record = queue[index];
      if (visible.has(record.id) || record.id === currentGuide || record.id === pendingGuide) continue;
      queue.splice(index, 1);
      queued.delete(record.id);
      details.delete(record.id);
      record.reject(new Error("This result is no longer visible."));
    }
    // Content starts loading immediately for every rendered card, without hover.
    for (const row of message.items) loadDetail(row[0]);
    stats.inputToResultsMs.push(performance.now() - inputAt);
  }

  function renderGuide(detail) {
    body.removeAttribute("aria-busy");
    const fragment = document.createDocumentFragment();
    const heading = node("h1", "guide-title", detail.title || "Repair guide");
    heading.tabIndex = -1;
    fragment.append(heading);
    if (detail.summary) fragment.append(node("p", "guide-summary", detail.summary));
    const attribution = node("p", "guide-source");
    const sourceUrl = safeUrl(detail.sourceUrl);
    if (sourceUrl) {
      const link = node("a", "", "Original on iFixit");
      link.href = sourceUrl;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      attribution.append(link);
    }
    const license = detail.license || latestSource?.license;
    if (license?.name) {
      attribution.append(document.createTextNode(" · "));
      const licenseUrl = safeUrl(license.url);
      const label = node(licenseUrl ? "a" : "span", "", license.name);
      if (licenseUrl) { label.href = licenseUrl; label.target = "_blank"; label.rel = "noopener noreferrer"; }
      attribution.append(label);
    }
    if (detail.attribution || latestSource?.attribution) attribution.append(document.createTextNode(` · ${detail.attribution || latestSource.attribution}`));
    fragment.append(attribution);
    const coverUrl = safeUrl(detail.images?.[0]);
    if (coverUrl && new URL(coverUrl).origin === location.origin) {
      const cover = node("img", "guide-cover");
      cover.src = coverUrl;
      cover.alt = detail.title || "";
      cover.decoding = "async";
      fragment.append(cover);
    }
    const content = node("div", "guide-content");
    // HTML is sanitized by ingestion; no remote markup is accepted from search input.
    content.innerHTML = typeof detail.html === "string" ? detail.html : "";
    for (const image of content.querySelectorAll("img")) {
      image.decoding = "async";
      image.loading = "lazy";
    }
    fragment.append(content);
    if (detail.related?.length) {
      const related = node("section", "guide-related");
      related.append(node("h2", "", "Related repairs"));
      const list = node("ul");
      for (const item of detail.related.slice(0, 12)) {
        const link = node("a", "", item.title);
        link.href = guideHref(item.id);
        link.dataset.guideId = item.id;
        const li = node("li"); li.append(link); list.append(li);
      }
      related.append(list); fragment.append(related);
    }
    body.replaceChildren(fragment);
    heading.focus({ preventScroll: true });
  }

  function commitGuide(id, detail, addHistory) {
    currentGuide = id;
    pendingGuide = null;
    navigationError.hidden = true;
    document.body.classList.add("reading");
    panel.hidden = false;
    results.hidden = true;
    if (addHistory) history.pushState({ guide: id }, "", guideHref(id));
    window.scrollTo(0, 0);
    renderGuide(detail);
  }

  function cancelPendingGuide() {
    if (pendingGuide !== null) viewVersion += 1;
    pendingGuide = null;
    navigationError.hidden = true;
  }

  function showGuide(id, addHistory = true) {
    const started = performance.now();
    const version = ++viewVersion;
    pendingGuide = id;
    navigationError.hidden = true;
    const record = loadDetail(id);
    if (record.data) {
      commitGuide(id, record.data, addHistory);
      stats.cachedOpenMs.push(performance.now() - started);
      return;
    }
    // Prioritize a clicked page over other cards still waiting for a fetch slot.
    if (queued.has(id)) {
      const index = queue.indexOf(record);
      if (index > 0) { queue.splice(index, 1); queue.unshift(record); }
    }
    // Keep the current results or guide intact until the requested content is ready.
    record.promise.then((detail) => {
      if (version === viewVersion) commitGuide(id, detail, addHistory);
    }).catch((error) => {
      if (version !== viewVersion) return;
      pendingGuide = null;
      const message = node("span", "", error.message);
      const retry = node("button", "text-button", "Try again");
      retry.type = "button";
      retry.addEventListener("click", () => { details.delete(id); showGuide(id, addHistory); });
      navigationError.replaceChildren(message, document.createTextNode(" "), retry);
      navigationError.hidden = false;
    });
  }

  function closeGuide(addHistory = true) {
    currentGuide = null;
    pendingGuide = null;
    viewVersion += 1;
    navigationError.hidden = true;
    document.body.classList.remove("reading");
    panel.hidden = true;
    body.removeAttribute("aria-busy");
    window.dispatchEvent(new Event("mend:layout"));
    if (addHistory) history.pushState({}, "", location.pathname);
    runSearch();
    input.focus({ preventScroll: true });
  }

  document.addEventListener("click", (event) => {
    const link = event.target.closest("a");
    if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin || !url.searchParams.has("guide")) return;
    event.preventDefault();
    showGuide(url.searchParams.get("guide"));
  });

  input.addEventListener("input", () => { cancelPendingGuide(); limit = 12; runSearch(); });
  document.getElementById("clear-search").addEventListener("click", () => {
    input.value = input.value.slice(0, promptLength);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
  });
  more.addEventListener("click", () => { limit += 12; runSearch(); });
  document.getElementById("close-guide").addEventListener("click", () => closeGuide());
  window.addEventListener("popstate", () => {
    const id = new URL(location.href).searchParams.get("guide");
    if (id) showGuide(id, false); else closeGuide(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (pendingGuide !== null) cancelPendingGuide();
      else if (currentGuide !== null) closeGuide();
    }
  });

  if (location.protocol === "file:") {
    setStatus("Open the website through the Go server to search the repair library.");
  } else if (typeof Worker === "undefined") {
    setStatus("This browser does not support local repair search. Please use a current browser.");
  } else {
    worker = new Worker("/assets/search-worker.js");
    worker.onmessage = ({ data }) => {
      if (data.type === "ready") {
        ready = true;
        total = data.total;
        latestSource = data.source;
        stats.indexLoadMs = data.loadDuration;
        for (const item of data.categories) categoryNames.set(item.id, item.name);
        runSearch();
      } else if (data.type === "results") renderCards(data);
      else if (data.type === "error") {
        setStatus(data.message);
        count.textContent = "The repair library could not be loaded. Refresh to try again.";
      }
    };
    worker.onerror = () => setStatus("The repair index could not start. Refresh to try again.");
  }

  const initialGuide = new URL(location.href).searchParams.get("guide");
  if (initialGuide && location.protocol !== "file:") showGuide(initialGuide, false);
})();
