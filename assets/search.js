(() => {
  "use strict";

  const input = document.getElementById("response");
  const status = document.getElementById("catalog-status");
  const results = document.getElementById("search-results");
  const cards = document.getElementById("result-cards");
  const count = document.getElementById("result-count");
  const panel = document.getElementById("guide-panel");
  const body = document.getElementById("guide-body");
  const navigationError = document.getElementById("guide-nav-error");
  const hero = window.mendHero;
  const details = new Map();
  const prefetchedImages = new Set();
  const queue = [];
  const stats = { indexLoadMs: null, searchMs: [], inputToResultsMs: [], cachedOpenMs: [], detailRequests: 0 };
  window.mendSearchStats = stats;
  let worker;
  let ready = false;
  let requestId = 0;
  let inputAt = 0;
  let activeFetches = 0;
  let currentGuide = null;
  let pendingGuide = null;
  let viewVersion = 0;
  let latestSource;
  let searchFrame = 0;
  let pendingSearch;
  let choices = [];
  let explicitNavigation = false;
  const composingInputs = new Set();
  const searchBlocked = () => composingInputs.size > 0 || document.visibilityState === "hidden";

  const query = () => hero ? hero.query() : input.value.slice(Number(input.dataset.promptLength)).trim();
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const guideHref = (id) => `/?guide=${encodeURIComponent(id)}`;
  const safeUrl = (value) => {
    if (typeof value !== "string" || !value.trim()) return "";
    try {
      const url = new URL(value, location.href);
      return ["http:", "https:"].includes(url.protocol) ? url.href : "";
    } catch { return ""; }
  };

  function setStatus(message) {
    status.hidden = !message;
    status.textContent = message || "";
  }

  function invalidateSearch() {
    requestId += 1;
    pendingSearch = null;
    if (searchFrame) cancelAnimationFrame(searchFrame);
    searchFrame = 0;
  }

  function hideResults() {
    cancelPendingGuide();
    currentGuide = null;
    choices = [];
    document.body.classList.remove("searching");
    results.hidden = true;
    panel.hidden = true;
    body.replaceChildren();
    cards.replaceChildren();
    count.textContent = "";
    count.hidden = true;
    pruneQueue(new Set());
  }

  function runSearch(force = false) {
    if (explicitNavigation && !force) return;
    const text = query();
    if (!text) {
      invalidateSearch();
      hideResults();
      return;
    }
    if (searchBlocked() || !force && pendingSearch?.query === text) return;
    executeSearch(text);
  }

  function executeSearch(text) {
    requestId += 1;
    document.body.classList.add("searching");
    inputAt = performance.now();
    if (!ready) return;
    // The worker still ranks the best 100. The inline UI needs only the first
    // answer and five alternatives, avoiding card layout/range round trips.
    results.hidden = false;
    pendingSearch = { type: "search", requestId, query: text, count: 6 };
    if (!searchFrame) searchFrame = requestAnimationFrame(() => {
      searchFrame = 0;
      if (searchBlocked()) { pendingSearch = null; return; }
      worker.postMessage(pendingSearch);
    });
  }

  function pruneQueue(wanted) {
    for (let index = queue.length - 1; index >= 0; index--) {
      const record = queue[index];
      if (wanted.has(record.id) || record.id === currentGuide || record.id === pendingGuide) continue;
      queue.splice(index, 1);
      details.delete(record.id);
      record.reject(new Error("This answer is no longer requested."));
    }
  }

  function cacheImages(detail) {
    for (const source of (Array.isArray(detail.images) ? detail.images : []).slice(0, 1)) {
      const url = safeUrl(source);
      if (!url || new URL(url).origin !== location.origin || prefetchedImages.has(url)) continue;
      prefetchedImages.add(url);
      const image = new Image();
      image.decoding = "async";
      image.src = url;
    }
  }

  function loadDetail(id, priority = false) {
    let record = details.get(id);
    if (record?.state === "error") { details.delete(id); record = null; }
    if (record) {
      if (priority && record.state === "queued") {
        const index = queue.indexOf(record);
        if (index > 0) { queue.splice(index, 1); queue.unshift(record); }
      }
      return record;
    }
    let resolve, reject;
    const promise = new Promise((accept, fail) => { resolve = accept; reject = fail; });
    // Background prefetch failures are surfaced only if that item is opened.
    promise.catch(() => {});
    record = { id, promise, resolve, reject, data: null, state: "queued" };
    details.set(id, record);
    if (priority) queue.unshift(record); else queue.push(record);
    pump();
    return record;
  }

  function pump() {
    while (activeFetches < 4 && queue.length) {
      const record = queue.shift();
      record.state = "loading";
      activeFetches += 1;
      stats.detailRequests += 1;
      fetch(`/api/guides/${encodeURIComponent(record.id)}`, { credentials: "same-origin", mode: "same-origin" })
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

  function renderPossibilities() {
    const fragment = document.createDocumentFragment();
    for (const row of choices.slice(1, 6)) {
      const link = node("a", "answer-possibility", row[1]);
      link.href = guideHref(row[0]);
      link.dataset.guideId = row[0];
      link.setAttribute("role", "listitem");
      if (row[0] === currentGuide) link.setAttribute("aria-current", "true");
      fragment.append(link);
    }
    cards.replaceChildren(fragment);
    count.textContent = choices.length > 1 ? "Other possibilities" : "";
    count.hidden = choices.length <= 1;
  }

  function renderAnswers(message) {
    if (message.requestId !== requestId || explicitNavigation || !query()) return;
    stats.searchMs.push(message.duration);
    const seen = new Set();
    const rows = message.items.filter(row => (row[3] === "guide" || row[3] === "article") &&
      !seen.has(row[0]) && seen.add(row[0])).slice(0, 6);
    if (!rows.length) { hideResults(); return; }
    pruneQueue(new Set(rows.slice(0, 5).map(row => row[0])));
    showGuide(rows[0][0], false, { automatic: true, rows, searchRequest: message.requestId });
    for (const row of rows.slice(1, 5)) loadDetail(row[0]);
  }

  const strings = (value) => Array.isArray(value)
    ? value.filter((item) => typeof item === "string" && item.trim()) : [];
  const localMedia = (value) => {
    const url = safeUrl(value);
    return url && new URL(url).origin === location.origin && new URL(url).pathname.startsWith("/media/") ? url : "";
  };

  function renderProvenance(detail) {
    const metadata = node("div", "guide-source");
    const attribution = node("span", "guide-attribution");
    const sourceUrl = safeUrl(detail.sourceUrl);
    if (sourceUrl) {
      const sourceName = typeof detail.sourceName === "string" && detail.sourceName
        ? detail.sourceName : (new URL(sourceUrl).hostname.endsWith("ifixit.com") ? "iFixit" : "source");
      const link = node("a", "", `Original on ${sourceName}`);
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
    const credit = (typeof detail.attribution === "string" ? detail.attribution
      : typeof latestSource?.attribution === "string" ? latestSource.attribution : "")
      .replace(/;?\s*reformatted and sanitized for this offline catalog\./gi, ".")
      .replace(/\s*CC BY-NC-SA 3\.0\.$/, "").trim();
    if (credit) attribution.append(document.createTextNode(` · ${credit}`));
    metadata.append(attribution);
    const references = Array.isArray(detail.references) ? detail.references : [];
    const links = node("span", "guide-references");
    links.setAttribute("aria-label", "Further references");
    const seen = new Set();
    for (const reference of references) {
      if (!reference || typeof reference.title !== "string" || !reference.title.trim()) continue;
      const url = safeUrl(reference.url);
      if (!url || seen.has(url)) continue;
      seen.add(url);
      const link = node("a", "", reference.title);
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      links.append(link);
    }
    if (links.children.length) metadata.append(links);
    return metadata;
  }

  function renderLegacyGuide(detail) {
    const content = node("div", "guide-content");
    // Only the legacy ingestion path supplies sanitized HTML. Structured model
    // output always uses the text-only renderer below, even if it is malformed.
    content.innerHTML = typeof detail.html === "string" ? detail.html : "";
    for (const image of content.querySelectorAll("img")) {
      image.decoding = "async";
      image.loading = "lazy";
    }
    return content;
  }

  function renderNotice(level, text) {
    const severity = ["note", "warning", "danger"].includes(level) ? level : "note";
    const notice = node("aside", `guide-notice guide-notice-${severity}`);
    notice.setAttribute("role", "note");
    notice.append(node("strong", "", { note: "Note", warning: "Warning", danger: "Danger" }[severity]),
      node("p", "", text));
    return notice;
  }

  function renderStructuredGuide(detail) {
    const content = node("div", "guide-content guide-structured");
    const safety = Array.isArray(detail.safety) ? detail.safety : [];
    if (safety.some((item) => item && typeof item.text === "string" && item.text.trim())) {
      const section = node("section", "guide-safety");
      section.append(node("h2", "", "Before you start"));
      for (const item of safety) {
        if (item && typeof item.text === "string" && item.text.trim()) section.append(renderNotice(item.level, item.text));
      }
      content.append(section);
    }
    const supplies = node("div", "guide-supplies");
    for (const [key, title] of [["tools", "Tools"], ["materials", "Materials"]]) {
      const items = strings(detail[key]);
      if (!items.length) continue;
      const section = node("section");
      const list = node("ul");
      for (const item of items) list.append(node("li", "", item));
      section.append(node("h2", "", title), list);
      supplies.append(section);
    }
    if (supplies.children.length) content.append(supplies);
    const symptoms = strings(detail.symptoms);
    if (symptoms.length) {
      const section = node("section", "guide-symptoms");
      const list = node("ul");
      for (const symptom of symptoms) list.append(node("li", "", symptom));
      section.append(node("h2", "", "Symptoms"), list);
      content.append(section);
    }
    const steps = Array.isArray(detail.steps) ? detail.steps : [];
    const list = node("ol", "guide-steps");
    for (const step of steps) {
      if (!step || typeof step !== "object" || typeof step.title !== "string" || typeof step.body !== "string") continue;
      const number = list.children.length + 1;
      const item = node("li", "guide-step");
      const heading = node("h3", "guide-step-title");
      const label = node("label", "guide-step-check");
      const checkbox = node("input");
      checkbox.type = "checkbox";
      checkbox.setAttribute("aria-label", `Mark step ${number} complete: ${step.title}`);
      checkbox.addEventListener("change", () => item.classList.toggle("is-complete", checkbox.checked));
      label.append(checkbox, node("span", "", `${number}. ${step.title}`));
      heading.append(label);
      item.append(heading);
      if (step.body) item.append(node("p", "guide-step-body", step.body));
      for (const warning of strings(step.warnings)) item.append(renderNotice("warning", warning));
      for (const source of strings(step.images)) {
        const url = localMedia(source);
        if (!url) continue;
        const image = node("img");
        image.src = url;
        image.alt = step.title;
        image.decoding = "async";
        image.loading = "lazy";
        item.append(image);
      }
      if (typeof step.code === "string" && step.code.trim()) {
        const block = node("div", "guide-code");
        const copy = node("button", "text-button guide-copy", "Copy");
        copy.type = "button";
        copy.setAttribute("aria-label", `Copy code for step ${number}`);
        copy.addEventListener("click", async () => {
          try {
            await navigator.clipboard.writeText(step.code);
            copy.textContent = "Copied";
          } catch { copy.textContent = "Copy unavailable"; }
        });
        const pre = node("pre");
        pre.append(node("code", "", step.code));
        block.append(copy, pre);
        item.append(block);
      }
      list.append(item);
    }
    if (list.children.length) {
      const section = node("section");
      section.append(node("h2", "", "Steps"), list);
      content.append(section);
    }
    return content;
  }

  function renderGuide(detail, focusHeading = false) {
    body.removeAttribute("aria-busy");
    const fragment = document.createDocumentFragment();
    const heading = node("h1", "guide-title", detail.title || "Repair guide");
    heading.tabIndex = -1;
    fragment.append(heading);
    if (typeof detail.summary === "string" && detail.summary) fragment.append(node("p", "guide-summary", detail.summary));
    fragment.append(renderProvenance(detail));
    const coverUrl = safeUrl(detail.images?.[0]);
    if (coverUrl && new URL(coverUrl).origin === location.origin) {
      const cover = node("img", "guide-cover");
      cover.src = coverUrl;
      cover.alt = detail.title || "";
      cover.decoding = "async";
      fragment.append(cover);
    }
    fragment.append(detail.version === 2 || Array.isArray(detail.steps)
      ? renderStructuredGuide(detail) : renderLegacyGuide(detail));
    if (Array.isArray(detail.related) && detail.related.length) {
      const related = node("section", "guide-related");
      related.append(node("h2", "", "Related repairs"));
      const list = node("ul");
      for (const item of detail.related.slice(0, 12)) {
        if (!item || typeof item.id !== "string" || typeof item.title !== "string") continue;
        const link = node("a", "", item.title);
        link.href = guideHref(item.id);
        link.dataset.guideId = item.id;
        const li = node("li"); li.append(link); list.append(li);
      }
      related.append(list); fragment.append(related);
    }
    body.replaceChildren(fragment);
    if (focusHeading) heading.focus({ preventScroll: true });
  }

  function commitGuide(id, detail, addHistory, options) {
    currentGuide = id;
    pendingGuide = null;
    navigationError.hidden = true;
    document.body.classList.remove("reading");
    document.body.classList.add("searching");
    panel.hidden = false;
    results.hidden = false;
    document.getElementById("close-guide").hidden = !explicitNavigation;
    if (options.rows) choices = options.rows;
    if (addHistory) history.pushState({ guide: id }, "", guideHref(id));
    renderGuide(detail, !options.automatic);
    renderPossibilities();
    if (options.automatic) stats.inputToResultsMs.push(performance.now() - inputAt);
    window.dispatchEvent(new Event("mend:layout"));
  }

  function cancelPendingGuide() {
    viewVersion += 1;
    pendingGuide = null;
    navigationError.hidden = true;
  }

  function showGuide(id, addHistory = true, options = {}) {
    if (typeof id !== "string" || !id) return;
    const started = performance.now();
    const version = ++viewVersion;
    if (!options.automatic) {
      explicitNavigation = true;
      invalidateSearch();
    }
    pendingGuide = id;
    navigationError.hidden = true;
    const record = loadDetail(id, true);
    const valid = () => version === viewVersion && (!options.automatic ||
      options.searchRequest === requestId && !explicitNavigation && query() &&
      !searchBlocked());
    if (record.data) {
      if (valid()) commitGuide(id, record.data, addHistory, options);
      stats.cachedOpenMs.push(performance.now() - started);
      return;
    }
    // Keep the prior answer and alternatives until the replacement is ready.
    // Changing/clearing input invalidates this selection before any old fetch
    // resolves; automatic rendering never focuses or scrolls the page.
    record.promise.then((detail) => {
      if (valid()) commitGuide(id, detail, addHistory, options);
    }).catch((error) => {
      if (!valid()) return;
      pendingGuide = null;
      // Automatic prefetch errors stay quiet; explicit navigation offers retry.
      if (options.automatic) return;
      const message = node("span", "", error.message);
      const retry = node("button", "text-button", "Try again");
      retry.type = "button";
      retry.addEventListener("click", () => { details.delete(id); showGuide(id, addHistory, options); });
      navigationError.replaceChildren(message, document.createTextNode(" "), retry);
      navigationError.hidden = false;
    });
  }

  function closeGuide(addHistory = true) {
    explicitNavigation = false;
    cancelPendingGuide();
    document.getElementById("close-guide").hidden = true;
    if (addHistory) history.pushState({}, "", location.pathname);
    if (hero) hero.focus(); else input.focus({ preventScroll: true });
    hero?.settle?.();
    runSearch(true);
  }

  document.addEventListener("click", (event) => {
    const link = event.target.closest("a");
    if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin || !url.searchParams.has("guide")) return;
    event.preventDefault();
    showGuide(url.searchParams.get("guide"));
  });

  const queryChanged = () => {
    if (explicitNavigation) {
      explicitNavigation = false;
      history.replaceState?.({}, "", location.pathname);
      document.getElementById("close-guide").hidden = true;
    }
    cancelPendingGuide();
    invalidateSearch();
    runSearch();
  };
  if (hero) window.addEventListener("mend:querychange", queryChanged);
  else input.addEventListener("input", queryChanged);
  window.addEventListener("mend:settle", () => runSearch());
  for (const field of [input, document.getElementById("clarification-response")].filter(Boolean)) {
    field.addEventListener("compositionstart", () => { composingInputs.add(field); cancelPendingGuide(); invalidateSearch(); });
    field.addEventListener("compositionend", () => {
      composingInputs.delete(field);
      // The committed value can equal the last preedit value, so the hero may
      // correctly emit no querychange. Search that committed value now anyway.
      queryChanged();
    });
  }
  document.addEventListener("visibilitychange", () => {
    invalidateSearch();
    if (document.visibilityState !== "hidden") runSearch();
  });
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
        setStatus("");
        count.textContent = "";
        latestSource = data.source;
        stats.indexLoadMs = data.loadDuration;
        // Cached workers may still send the earlier object representation.
        let archetypes = data.archetypes;
        const encoded = typeof data.archetypesJSON === "string" ? data.archetypesJSON
          : typeof archetypes === "string" ? archetypes : "";
        if (encoded) {
          try { archetypes = JSON.parse(encoded); }
          catch { /* Keep an available legacy object, or use an empty registry. */ }
        }
        if (!archetypes || typeof archetypes !== "object") archetypes = { version: 1, archetypes: [], models: [] };
        window.mendSpecifiers?.configure?.(archetypes);
        hero?.setVocabulary?.(data.terms || []);
        runSearch();
        window.dispatchEvent(new Event("mend:catalogready"));
      } else if (data.type === "results") renderAnswers(data);
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
