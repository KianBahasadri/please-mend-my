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
  const clientCache = window.mendClientCache;
  const details = new Map();
  const prefetchedImages = new Set();
  const queue = [];
  const loading = new Set();
  const stats = { indexLoadMs: null, searchMs: [], inputToWorkerReplyMs: [], inputToTitlesMs: [], inputToResultsMs: [],
    guideQueueMs: [], detailTransportMs: [], detailParseMs: [], articleRenderMs: [], cachedOpenMs: [],
    detailRequests: 0, answerReuses: 0 };
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
  let pendingSearch;
  let pendingInputAt;
  let inputEventAt;
  let choices = [];
  let choiceScopes = new Map();
  let possibilitiesKey;
  let renderedDetail;
  let renderedGeneration;
  let renderedScope;
  let previewKey;
  let explicitNavigation = false;
  let catalogETag = "";
  let replacement = null;
  let swapTimer = 0;
  let lastEditAt = -Infinity;
  let refreshProbe = false;
  const attemptedUpdates = new Set();
  const composingInputs = new Set();
  const searchBlocked = () => composingInputs.size > 0 || document.visibilityState === "hidden";
  const timing = (key, duration) => {
    const samples = stats[key];
    samples.push(duration);
    if (samples.length > 128) samples.shift();
  };

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
  }

  function hideResults() {
    cancelPendingGuide();
    currentGuide = null;
    choices = [];
    choiceScopes = new Map();
    renderedDetail = renderedGeneration = renderedScope = undefined;
    possibilitiesKey = undefined;
    previewKey = undefined;
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
      pendingInputAt = undefined;
      hideResults();
      return;
    }
    if (searchBlocked() || !force && pendingSearch?.query === text) return;
    executeSearch(text);
  }

  function executeSearch(text) {
    requestId += 1;
    pruneQueue(new Set());
    if (!document.body.classList.contains("searching")) document.body.classList.add("searching");
    inputAt = pendingInputAt ?? performance.now();
    if (!ready) return;
    // The worker still ranks the best 100. The inline UI needs only the first
    // answer and five alternatives, avoiding card layout/range round trips.
    results.hidden = false;
    pendingSearch = { type: "search", requestId, query: text, count: 6 };
    pendingInputAt = undefined;
    // Posting is independent of the rendering frame. The worker already
    // coalesces pending edits; an animation-frame wait adds visible input lag.
    worker.postMessage(pendingSearch);
  }

  function pruneQueue(wanted) {
    for (let index = queue.length - 1; index >= 0; index--) {
      const record = queue[index];
      if (wanted.has(record.id) || record.id === currentGuide || record.id === pendingGuide) continue;
      queue.splice(index, 1);
      details.delete(record.id);
      record.reject(new Error("This answer is no longer requested."));
    }
    // An obsolete slow prefetch must not occupy all four fetch slots while the
    // newest #1 waits. Keep rendered data; cancel only no-longer-wanted I/O.
    for (const record of loading) {
      if (wanted.has(record.id) || record.id === currentGuide || record.id === pendingGuide || !record.controller) continue;
      details.delete(record.id);
      record.controller.abort();
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
    record = { id, promise, resolve, reject, data: null, state: "queued", queuedAt: performance.now() };
    details.set(id, record);
    if (priority) queue.unshift(record); else queue.push(record);
    pump();
    return record;
  }

  function pump() {
    while (activeFetches < 4 && queue.length) {
      const record = queue.shift();
      record.state = "loading";
      record.controller = typeof AbortController === "function" ? new AbortController() : null;
      loading.add(record);
      activeFetches += 1;
      stats.detailRequests += 1;
      const transportAt = performance.now();
      timing("guideQueueMs", transportAt - record.queuedAt);
      const generation = catalogETag;
      const signal = record.controller?.signal;
      const requestGuide = (reload) => fetch(`/api/guides/${encodeURIComponent(record.id)}`, {
        credentials: "same-origin", mode: "same-origin",
        ...(reload ? { cache: "no-store" } : {}),
        ...(signal ? { signal } : {}),
      });
      // A rejected persistent guide must not freeze the previous article while
      // the current alternatives are already visible. Abort stays abort.
      const response = clientCache?.fetchGuide
        ? Promise.resolve().then(() => clientCache.fetchGuide(record.id, { etag: generation, signal })).catch(error => {
          if (signal?.aborted || error?.name === "AbortError") throw error;
          return requestGuide(true);
        })
        : requestGuide(false);
      response
        .then(async (response) => {
          timing("detailTransportMs", performance.now() - transportAt);
          if (!response.ok) throw new Error(`This repair page could not be loaded (${response.status}).`);
          const parseAt = performance.now();
          const detail = await response.json();
          timing("detailParseMs", performance.now() - parseAt);
          return detail;
        })
        .then((detail) => {
          if (generation && generation !== catalogETag) throw new Error("Catalog changed");
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
        .finally(() => { loading.delete(record); activeFetches -= 1; pump(); });
    }
  }

  function renderPossibilities() {
    const key = JSON.stringify(choices.slice(1, 6).map(row => [row[0], row[1], row[0] === currentGuide]));
    if (key === possibilitiesKey) return;
    possibilitiesKey = key;
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
    timing("searchMs", message.duration);
    timing("inputToWorkerReplyMs", performance.now() - inputAt);
    const seen = new Set();
    const rows = message.items.filter(row => (row[3] === "guide" || row[3] === "article") &&
      !seen.has(row[0]) && seen.add(row[0])).slice(0, 6);
    if (!rows.length) {
      // A nonempty query without a replacement must not erase its current
      // answer. Retire pending payloads and alternatives; whole-query clearing
      // still uses hideResults() immediately.
      cancelPendingGuide();
      choices = []; choiceScopes = new Map();
      renderPossibilities();
      pruneQueue(new Set());
      if (currentGuide === null) {
        results.hidden = true; panel.hidden = true; previewKey = undefined; body.replaceChildren();
      }
      return;
    }
    pruneQueue(new Set(rows.slice(0, 5).map(row => row[0])));
    const scopes = new Map((Array.isArray(message.inheritance) ? message.inheritance : [])
      .filter(scope => scope && typeof scope.id === "string" && typeof scope.label === "string" &&
        scope.label.trim() && scope.label.length <= 160 && Number.isInteger(scope.distance) && scope.distance > 0)
      .map(scope => [scope.id, scope]));
    // Ranked titles are already in memory. Show the alternatives immediately,
    // while retaining the current article until the new detail is available.
    choices = rows;
    choiceScopes = scopes;
    renderPossibilities();
    if (currentGuide === null && !details.get(rows[0][0])?.data) {
      const scope = scopes.get(rows[0][0]);
      const key = JSON.stringify([rows[0][0], rows[0][1], scope?.label || ""]);
      if (key !== previewKey) {
        const heading = node("h1", "guide-title", rows[0][1]);
        heading.tabIndex = -1;
        body.replaceChildren(heading);
        if (scope) body.append(node("p", "guide-scope", `General guidance for ${scope.label}`));
        previewKey = key;
      }
      panel.hidden = false;
      results.hidden = false;
    }
    timing("inputToTitlesMs", performance.now() - inputAt);
    showGuide(rows[0][0], false, { automatic: true, rows, scopes, searchRequest: message.requestId });
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

  function renderGuide(detail, focusHeading = false, scope) {
    body.removeAttribute("aria-busy");
    const fragment = document.createDocumentFragment();
    const heading = node("h1", "guide-title", detail.title || "Repair guide");
    heading.tabIndex = -1;
    fragment.append(heading);
    if (scope) fragment.append(node("p", "guide-scope", `General guidance for ${scope.label}`));
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
    const scope = (options.scopes || choiceScopes).get(id);
    const sameAnswer = currentGuide === id && renderedDetail === detail &&
      renderedGeneration === catalogETag && renderedScope === (scope?.label || "");
    currentGuide = id;
    previewKey = undefined;
    pendingGuide = null;
    navigationError.hidden = true;
    if (!sameAnswer) {
      if (document.body.classList.contains("reading")) document.body.classList.remove("reading");
      if (!document.body.classList.contains("searching")) document.body.classList.add("searching");
      panel.hidden = false;
      results.hidden = false;
    }
    if (options.rows) choices = options.rows;
    if (options.scopes) choiceScopes = options.scopes;
    if (addHistory) history.pushState({ guide: id }, "", guideHref(id));
    if (!sameAnswer) {
      const renderAt = performance.now();
      renderGuide(detail, !options.automatic, scope);
      timing("articleRenderMs", performance.now() - renderAt);
      renderedDetail = detail;
      renderedGeneration = catalogETag;
      renderedScope = scope?.label || "";
    } else {
      stats.answerReuses += 1;
      if (!options.automatic) body.querySelector(".guide-title")?.focus({ preventScroll: true });
    }
    renderPossibilities();
    if (options.automatic) timing("inputToResultsMs", performance.now() - inputAt);
    if (!sameAnswer) window.dispatchEvent(new Event("mend:layout"));
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
      timing("cachedOpenMs", performance.now() - started);
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
    invalidateSearch();
    if (addHistory) history.pushState({}, "", location.pathname);
    if (hero) hero.focus(); else input.focus({ preventScroll: true });
    hero?.settle?.();
    // settle() can synchronously publish this query. Reuse that dispatch;
    // older pages without the hero still search once through this fallback.
    runSearch();
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
    lastEditAt = performance.now();
    pendingInputAt = inputEventAt ?? lastEditAt;
    inputEventAt = undefined;
    if (!explicitNavigation && pendingSearch?.query === query()) {
      pendingInputAt = undefined;
      scheduleSwap();
      return;
    }
    if (explicitNavigation) {
      explicitNavigation = false;
      history.replaceState?.({}, "", location.pathname);
    }
    cancelPendingGuide();
    invalidateSearch();
    runSearch();
    scheduleSwap();
  };
  if (hero) window.addEventListener("mend:querychange", queryChanged);
  else input.addEventListener("input", queryChanged);
  window.addEventListener("mend:settle", () => runSearch());
  for (const field of [input, document.getElementById("clarification-response")].filter(Boolean)) {
    // Capture precedes the hero's native input handler and its layout work.
    field.addEventListener("input", () => { inputEventAt = performance.now(); }, { capture: true });
    field.addEventListener("compositionstart", () => {
      lastEditAt = performance.now(); composingInputs.add(field); cancelPendingGuide(); invalidateSearch();
    });
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
    if (document.visibilityState !== "hidden") scheduleSwap();
  });
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

  function configureWorker(data) {
    ready = true;
    setStatus("");
    possibilitiesKey = undefined;
    count.textContent = "";
    latestSource = data.source;
    catalogETag = typeof data.catalogETag === "string" ? data.catalogETag : "";
    stats.indexLoadMs = data.loadDuration;
    stats.indexCacheSource = data.cacheSource || "network";
    stats.indexReadMs = data.cacheTimings?.readMs ?? null;
    stats.indexParseMs = data.cacheTimings?.parseMs ?? null;
    stats.indexPrepareMs = data.prepareDuration ?? null;
    let archetypes = data.archetypes;
    const encoded = typeof data.archetypesJSON === "string" ? data.archetypesJSON
      : typeof archetypes === "string" ? archetypes : "";
    if (encoded) {
      try { archetypes = JSON.parse(encoded); } catch {}
    }
    if (!archetypes || typeof archetypes !== "object") archetypes = { version: 1, archetypes: [], models: [] };
    window.mendSpecifiers?.configure?.(archetypes);
    hero?.setVocabulary?.(data.terms || []);
    runSearch();
    window.dispatchEvent(new Event("mend:catalogready"));
  }

  function discardReplacement(record) {
    if (replacement !== record) return;
    record.worker.terminate?.();
    clientCache.rejectCatalog?.({ etag: record.etag, candidateKey: record.key }).catch(() => {});
    replacement = null;
    if (swapTimer) clearTimeout(swapTimer);
    swapTimer = 0;
  }

  function clearGuideGeneration() {
    cancelPendingGuide();
    for (const record of queue.splice(0)) record.reject(new Error("Catalog changed"));
    for (const record of loading) record.controller?.abort();
    details.clear(); prefetchedImages.clear();
  }

  async function swapReplacement(record) {
    if (!record || replacement !== record || record.accepting || !record.data) return;
    if (composingInputs.size || document.visibilityState === "hidden") return;
    record.accepting = true;
    try {
      if (!record.accepted) {
        record.accepted = await clientCache.acceptCatalog({ etag: record.etag, candidateKey: record.key, deferGuides: true });
        if (!record.accepted) { discardReplacement(record); return; }
      }
      if (replacement !== record) return;
      // Accepting Cache Storage is asynchronous. An IME composition or a new
      // keystroke can begin meanwhile, so recheck the idle boundary afterwards.
      if (composingInputs.size || document.visibilityState === "hidden" || performance.now() - lastEditAt < 380) return;
      if (!record.published && clientCache.publishCatalog) {
        record.published = await clientCache.publishCatalog(record.etag);
        if (!record.published) { discardReplacement(record); return; }
      }
      if (replacement !== record) return;
      if (composingInputs.size || document.visibilityState === "hidden" || performance.now() - lastEditAt < 380) return;
      const previous = worker;
      catalogETag = record.etag;
      clearGuideGeneration();
      invalidateSearch();
      worker = record.worker; replacement = null;
      previous.terminate?.();
      configureWorker(record.data);
    } catch { discardReplacement(record); }
    finally { record.accepting = false; if (replacement === record) scheduleSwap(); }
  }

  function scheduleSwap() {
    if (!replacement?.data || composingInputs.size || document.visibilityState === "hidden") return;
    if (swapTimer) clearTimeout(swapTimer);
    const delay = Math.max(0, 380 - (performance.now() - lastEditAt));
    if (typeof setTimeout === "function") swapTimer = setTimeout(() => {
      swapTimer = 0; swapReplacement(replacement);
    }, delay);
    else if (!delay) swapReplacement(replacement);
  }

  function startReplacement(update) {
    if (!clientCache || !update?.candidateKey || !update.etag || update.etag === catalogETag || attemptedUpdates.has(update.etag)) return;
    attemptedUpdates.add(update.etag);
    if (replacement) discardReplacement(replacement);
    const source = new Worker(`/assets/search-worker.js?candidateKey=${encodeURIComponent(update.candidateKey)}`);
    const record = { worker: source, etag: update.etag, key: update.candidateKey, data: null, accepting: false, accepted: false };
    replacement = record;
    connectWorker(source, record);
  }

  function connectWorker(source, record = null) {
    source.onmessage = ({ data }) => {
      if (record && replacement === record) {
        if (data.type === "ready") {
          if (data.catalogETag !== record.etag || data.candidateKey !== record.key) { discardReplacement(record); return; }
          record.data = data; scheduleSwap();
        }
        else if (data.type === "error") discardReplacement(record);
        return;
      }
      if (source !== worker) return;
      if (data.type === "ready") configureWorker(data);
      else if (data.type === "results") renderAnswers(data);
      else if (data.type === "catalog-update") startReplacement(data);
      else if (data.type === "error") {
        setStatus(data.message);
        count.textContent = "The repair library could not be loaded. Refresh to try again.";
      }
    };
    source.onerror = () => {
      if (record && replacement === record) discardReplacement(record);
      else if (source === worker) setStatus("The repair index could not start. Refresh to try again.");
    };
  }

  function refreshCatalog(nextETag = "") {
    if (!clientCache?.probeCatalog || !ready || !catalogETag || refreshProbe || replacement ||
        nextETag && attemptedUpdates.has(nextETag)) return;
    refreshProbe = true;
    Promise.resolve().then(() => clientCache.probeCatalog(catalogETag))
      .then(update => { if (update) startReplacement(update); })
      .catch(() => {}).finally(() => { refreshProbe = false; });
  }

  if (location.protocol === "file:") {
    setStatus("Open the website through the Go server to search the repair library.");
  } else if (typeof Worker === "undefined") {
    setStatus("This browser does not support local repair search. Please use a current browser.");
  } else {
    worker = new Worker("/assets/search-worker.js");
    connectWorker(worker);
    clientCache?.registerShell?.().catch(() => {});
    clientCache?.subscribe?.(event => {
      if (event?.type === "catalog-changed") refreshCatalog(event.etag);
    });
    // The initial worker can start entirely offline. Reconnection revalidates
    // that resident release without delaying typing or reloading the page.
    window.addEventListener("online", () => refreshCatalog());
  }

  const initialGuide = new URL(location.href).searchParams.get("guide");
  if (initialGuide && location.protocol !== "file:") showGuide(initialGuide, false);
})();
