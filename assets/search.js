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
  const categoryNames = new Map();
  const details = new Map();
  const prefetchedImages = new Set();
  const queue = [];
  const queued = new Set();
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
  let resultTotal = 0;
  let displayedRequest = 0;
  let rangeSerial = 0;
  let requestedStart = -1;
  let requestedCount = 0;
  let scrollFrame = 0;
  let pendingFocus = null;
  let resultsScrollY = 0;
  const composingInputs = new Set();

  const searchBlocked = () => composingInputs.size > 0 || document.visibilityState === "hidden" ||
    document.body.classList.contains("reading");

  function geometry() {
    const css = getComputedStyle(cards);
    const columns = Number(css.getPropertyValue("--columns")) || 3;
    const gap = parseFloat(css.columnGap) || 13.6;
    const height = parseFloat(css.getPropertyValue("--card-height")) || 208;
    const step = height + gap;
    const logicalHeight = Math.max(0, Math.ceil(resultTotal / columns) * step - gap);
    // Browsers cap element heights. Scale only very long scroll tracks, keeping
    // actual cards full size and every result reachable even on narrow screens.
    const physicalHeight = Math.min(logicalHeight, 8000000);
    const scale = logicalHeight > physicalHeight
      ? (logicalHeight - innerHeight) / Math.max(1, physicalHeight - innerHeight) : 1;
    const top = cards.getBoundingClientRect().top + scrollY;
    const physicalOffset = Math.max(0, Math.min(scrollY - top, Math.max(0, physicalHeight - innerHeight)));
    return { columns, gap, height, step, logicalHeight, physicalHeight, scale, top,
      physicalOffset, logicalOffset: physicalOffset * scale,
      width: (cards.clientWidth - gap * (columns - 1)) / columns };
  }

  function positionCards(layout = geometry()) {
    cards.style.height = `${layout.physicalHeight}px`;
    for (const card of cards.children) {
      const index = Number(card.dataset.resultIndex);
      card.style.width = `${layout.width}px`;
      card.style.left = `${index % layout.columns * (layout.width + layout.gap)}px`;
      card.style.top = `${Math.floor(index / layout.columns) * layout.step - layout.logicalOffset + layout.physicalOffset}px`;
    }
  }

  function prefetchVisible() {
    if (currentGuide !== null || results.hidden) return;
    const visible = new Set();
    for (const card of cards.children) {
      const bounds = card.getBoundingClientRect();
      if (bounds.bottom > 0 && bounds.top < innerHeight) visible.add(card.dataset.guideId);
    }
    for (let index = queue.length - 1; index >= 0; index -= 1) {
      const record = queue[index];
      if (visible.has(record.id) || record.id === currentGuide || record.id === pendingGuide) continue;
      queue.splice(index, 1);
      queued.delete(record.id);
      details.delete(record.id);
      record.reject(new Error("This result is no longer visible."));
    }
    for (const id of visible) loadDetail(id);
  }

  function updateWindow() {
    scrollFrame = 0;
    if (!resultTotal || results.hidden || currentGuide !== null) return;
    const layout = geometry();
    const firstRow = Math.max(0, Math.floor(layout.logicalOffset / layout.step) - 3);
    const start = firstRow * layout.columns;
    const amount = Math.min(60, (Math.ceil(innerHeight / layout.step) + 6) * layout.columns);
    positionCards(layout);
    if (displayedRequest === requestId && (start !== requestedStart || amount !== requestedCount)) {
      requestedStart = start;
      requestedCount = amount;
      rangeSerial += 1;
      worker.postMessage({ type: "range", requestId: displayedRequest, rangeId: rangeSerial, start, count: amount });
    }
    prefetchVisible();
  }

  function scheduleWindow() {
    if (!scrollFrame) scrollFrame = requestAnimationFrame(updateWindow);
  }

  window.addEventListener("scroll", scheduleWindow, { passive: true });
  window.addEventListener("resize", scheduleWindow);
  window.addEventListener("mend:layout", scheduleWindow);

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
    document.body.classList.remove("searching");
    results.hidden = true;
    cards.replaceChildren();
    resultTotal = 0;
    cards.style.height = "0px";
    requestedStart = -1;
  }

  function runSearch(force = false) {
    const text = query();
    if (!text || hero?.isAwaitingSpecifics?.()) {
      invalidateSearch();
      hideResults();
      return;
    }
    if (searchBlocked() || !force && pendingSearch?.query === text) return;
    executeSearch(text);
  }

  function executeSearch(text) {
    requestId += 1;
    pendingFocus = null;
    document.body.classList.add("searching");
    results.hidden = currentGuide !== null;
    inputAt = performance.now();
    if (!ready) {
      results.hidden = true;
      return;
    }
    const layout = geometry();
    pendingSearch = { type: "search", requestId, query: text, count: Math.min(60, (Math.ceil(innerHeight / layout.step) + 6) * layout.columns) };
    if (!searchFrame) searchFrame = requestAnimationFrame(() => {
      searchFrame = 0;
      if (searchBlocked()) { pendingSearch = null; return; }
      worker.postMessage(pendingSearch);
    });
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

  function renderWindow(items, start) {
    const fragment = document.createDocumentFragment();
    items.forEach((row, offset) => {
      const [id, title, categoryId, kind, device] = row;
      if (kind !== "guide" && kind !== "article") return;
      const card = node("a", "repair-card");
      card.href = guideHref(id);
      card.title = title;
      card.dataset.guideId = id;
      card.dataset.resultIndex = start + offset;
      card.setAttribute("role", "listitem");
      card.setAttribute("aria-posinset", start + offset + 1);
      card.setAttribute("aria-setsize", resultTotal);
      const label = kind === "guide" ? "Repair guide" : "Repair article";
      card.append(node("p", "card-meta", `${categoryNames.get(categoryId) || categoryId} · ${label}`), node("h2", "", title));
      if (device && device !== title) card.append(node("p", "card-device", device));
      fragment.append(card);
    });
    const focused = document.activeElement?.closest(".repair-card")?.dataset.guideId;
    cards.replaceChildren(fragment);
    cards.dataset.total = resultTotal;
    cards.dataset.start = start;
    cards.dataset.end = start + items.length;
    positionCards();
    const nextFocus = pendingFocus !== null
      ? cards.querySelector(`[data-result-index="${pendingFocus}"]`)
      : focused ? cards.querySelector(`[data-guide-id="${focused}"]`) : null;
    if (nextFocus) { nextFocus.focus({ preventScroll: true }); pendingFocus = null; }
    prefetchVisible();
  }

  function renderCards(message) {
    if (message.requestId !== requestId) return;
    stats.searchMs.push(message.duration);
    resultTotal = message.total;
    displayedRequest = requestId;
    rangeSerial += 1;
    requestedStart = 0;
    requestedCount = pendingSearch.count;
    count.textContent = message.total ? `${message.total.toLocaleString()} ${message.total === 1 ? "match" : "matches"}` : "";
    renderWindow(message.items, 0);
    updateWindow();
    stats.inputToResultsMs.push(performance.now() - inputAt);
  }

  cards.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const card = event.target.closest(".repair-card");
    if (!card) return;
    const next = Number(card.dataset.resultIndex) + (event.shiftKey ? -1 : 1);
    if (next < 0 || next >= resultTotal) return;
    const mounted = cards.querySelector(`[data-result-index="${next}"]`);
    if (mounted) return;
    event.preventDefault();
    pendingFocus = next;
    const layout = geometry();
    window.scrollTo(0, layout.top + Math.floor(next / layout.columns) * layout.step / layout.scale);
    scheduleWindow();
  });

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

  function renderGuide(detail) {
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
    heading.focus({ preventScroll: true });
  }

  function commitGuide(id, detail, addHistory) {
    if (currentGuide === null) resultsScrollY = scrollY;
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
    if (hero) hero.focus();
    else input.focus({ preventScroll: true });
    hero?.settle?.();
    runSearch(true);
    window.scrollTo(0, resultsScrollY);
    scheduleWindow();
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
    cancelPendingGuide();
    invalidateSearch();
    runSearch();
  };
  if (hero) window.addEventListener("mend:querychange", queryChanged);
  else input.addEventListener("input", queryChanged);
  window.addEventListener("mend:settle", () => runSearch());
  for (const field of [input, document.getElementById("clarification-response")].filter(Boolean)) {
    field.addEventListener("compositionstart", () => { composingInputs.add(field); invalidateSearch(); });
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
        for (const item of data.categories) categoryNames.set(item.id, item.name);
        hero?.setVocabulary?.(data.terms || []);
        runSearch();
      } else if (data.type === "results") renderCards(data);
      else if (data.type === "range" && data.requestId === requestId && data.rangeId === rangeSerial) {
        renderWindow(data.items, data.start);
      }
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
