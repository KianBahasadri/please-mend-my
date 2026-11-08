(() => {
  "use strict";

  const ns = "http://www.w3.org/2000/svg";
  const normalizedWords = /^[a-z0-9]+(?: [a-z0-9]+)*$/;
  const printableASCII = /^[\x20-\x7e]*$/;
  const clean = value => {
    value = String(value);
    if (normalizedWords.test(value)) return value;
    if (printableASCII.test(value)) return value.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
    return value.normalize("NFKD").replace(/\p{M}/gu, "")
      .toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  };
  const part = (id, label, query, aliases = [], normalize = clean) => Object.freeze({
    id, label, query, aliases: Object.freeze([...new Set([query, label, ...aliases].map(normalize))]),
  });
  let definitions = [];
  let aliases = new Map();
  let exactAliases = new Map();
  let prefixArt = new Map();
  let nounWords = new Set();
  const precise = value => {
    value = String(value);
    if (normalizedWords.test(value)) return value;
    if (printableASCII.test(value)) return value.toLowerCase().trim().replace(/\s+/g, " ");
    return value.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase().trim().replace(/\s+/g, " ");
  };
  const artURL = value => typeof value === "string" && /^\/api\/art\/[a-f0-9]{64}\.json$/.test(value) ? value : "";
  const validText = (value, maximum = 160) => typeof value === "string" && Boolean(value.trim()) &&
    value.length <= maximum && !/[\u0000-\u001f]/.test(value);
  const resolve = subject => {
    const key = precise(subject);
    // Model punctuation is literal: SM-G991B and SMG991B are different inputs.
    return exactAliases.get(key) || (/\p{N}/u.test(key) ? null : aliases.get(clean(key))) || null;
  };

  function configure(payload) {
    const next = [], byId = new Map(), normalized = new Map(), exact = new Map();
    const normalizedRanks = new Map(), exactRanks = new Map();
    const prefixes = new Map(), words = new Set();
    // Many nouns share the same lexical parts. Reuse validated immutable parts
    // during this call; neither cache survives configuration or grows on edits.
    const partCache = new Map(), cleanCache = new Map();
    function cleanPart(value) {
      let result = cleanCache.get(value);
      if (result === undefined) { result = clean(value); cleanCache.set(value, result); }
      return result;
    }
    function preparePart(value) {
      if (!value || !validText(value.id) || !Array.isArray(value.aliases)) return null;
      const cached = partCache.get(value.id);
      for (const entry of cached || []) {
        const source = entry.source;
        if (source.label === value.label && source.query === value.query && source.aliases.length === value.aliases.length &&
            source.aliases.every((alias, index) => alias === value.aliases[index])) return entry.part;
      }
      if (!validText(value.label, 100) || !validText(value.query, 120) ||
          value.aliases.some(alias => !validText(alias, 120))) return null;
      const prepared = part(value.id, value.label, value.query, value.aliases, cleanPart);
      if (cached) cached.push({ source: value, part: prepared });
      else partCache.set(value.id, [{ source: value, part: prepared }]);
      return prepared;
    }
    function claim(map, ranks, key, definition, rank) {
      const previous = map.get(key);
      const previousRank = ranks.get(key) ?? Infinity;
      // Releases rank competing senses at build time. Legacy payloads without
      // ranks retain their original order; the browser names no special nouns.
      const preferred = !previous || rank < previousRank || rank === previousRank && rank !== Infinity &&
        (definition.guideCount > previous.guideCount || definition.guideCount === previous.guideCount && definition.id < previous.id);
      if (preferred) { map.set(key, definition); ranks.set(key, rank); }
      return !previous;
    }
    if (payload?.version !== 1 || !Array.isArray(payload.archetypes)) return false;
    for (const item of payload.archetypes) {
      if (!item || !validText(item.id) || byId.has(item.id) || !validText(item.label, 100) ||
          !Number.isInteger(item.guideCount) || item.guideCount <= 0 ||
          !Array.isArray(item.aliases) || item.aliases.some(alias => !validText(alias)) ||
          !Array.isArray(item.parts) || item.parts.length > 24) continue;
      const preparedParts = [], partIDs = new Set();
      let validParts = true;
      for (const value of item.parts) {
        const prepared = preparePart(value);
        if (!prepared || partIDs.has(prepared.id)) { validParts = false; break; }
        partIDs.add(prepared.id); preparedParts.push(prepared);
      }
      if (!validParts) continue;
      const definition = Object.freeze({ id: item.id, label: item.label,
        aliases: Object.freeze([...new Set([item.label, ...item.aliases])]),
        parts: Object.freeze(preparedParts),
        guideCount: item.guideCount, artUrl: artURL(item.artUrl),
      });
      next.push(definition); byId.set(definition.id, definition);
      for (const alias of definition.aliases) {
        const key = clean(alias);
        const rank = item.aliasRanks && Object.hasOwn(item.aliasRanks, key) && Number.isSafeInteger(item.aliasRanks[key])
          ? item.aliasRanks[key] : Infinity;
        claim(exact, exactRanks, precise(alias), definition, rank);
        if (claim(normalized, normalizedRanks, key, definition, rank)) {
          for (const word of key.split(" ")) if (word.length >= 2) words.add(word);
        }
        if (definition.artUrl) {
          for (let count = 3; count <= Math.min(key.length, 24); count++) {
            const prefix = key.slice(0, count);
            const rows = prefixes.get(prefix) || [];
            if (rows.length < 2 && !rows.includes(definition)) rows.push(definition);
            prefixes.set(prefix, rows);
          }
        }
      }
    }
    for (const model of Array.isArray(payload.models) ? payload.models : []) {
      const definition = byId.get(model?.nounId);
      if (!definition || !validText(model.name)) continue;
      const key = precise(model.name);
      if (!exact.has(key)) exact.set(key, definition);
    }
    definitions = next; aliases = normalized; exactAliases = exact;
    prefixArt = prefixes; nounWords = words;
    window.dispatchEvent?.(new Event("mend:specifiersready"));
    return true;
  }

  const artCache = new Map();
  const mounts = new WeakMap();
  const artQueue = [];
  let artFetches = 0;
  const MAX_ART_BYTES = 1_000_000;

  function pumpArt() {
    if (typeof fetch !== "function") return;
    while (artFetches < 2 && artQueue.length) {
      const record = artQueue.shift();
      artFetches += 1;
      fetch(record.url, { credentials: "same-origin", mode: "same-origin", cache: "force-cache" })
        .then(async response => {
          if (!response.ok || Number(response.headers?.get("Content-Length")) > MAX_ART_BYTES) return null;
          const text = await response.text();
          if (text.length > MAX_ART_BYTES) return null;
          const data = JSON.parse(text);
          return data?.version === 1 && data.id === record.id ? data : null;
        })
        .catch(() => null)
        .then(data => {
          record.data = data;
          record.resolve(data);
        })
        .finally(() => { artFetches -= 1; pumpArt(); });
    }
  }

  function loadArt(definition, urgent = false) {
    if (!definition.artUrl || typeof fetch !== "function") return Promise.resolve(null);
    let record = artCache.get(definition.artUrl);
    if (record) {
      if (urgent) {
        const index = artQueue.indexOf(record);
        if (index >= 0) { artQueue.splice(index, 1); artQueue.unshift(record); }
      }
      return record.promise;
    }
    let resolve;
    const promise = new Promise(accept => { resolve = accept; });
    record = { id: definition.id, url: definition.artUrl, data: null, promise, resolve };
    artCache.set(definition.artUrl, record);
    if (artCache.size > 16) artCache.delete(artCache.keys().next().value);
    if (urgent) artQueue.unshift(record); else artQueue.push(record);
    pumpArt();
    return promise;
  }

  function prefetch(subject) {
    const value = clean(subject);
    if (value.length < 3) return;
    const exact = resolve(subject);
    if (exact) { loadArt(exact, true); return; }
    for (const definition of prefixArt.get(value) || []) loadArt(definition);
  }

  window.addEventListener?.("mend:querychange", () => prefetch(window.mendHero?.query?.() || ""));
  window.addEventListener?.("mend:catalogready", () => {
    window.requestIdleCallback?.(() => {
      if (document.visibilityState === "hidden") return;
      for (const definition of definitions.filter(item => item.artUrl).slice(0, 8)) loadArt(definition);
    }, { timeout: 2000 });
  }, { once: true });

  const shapeTags = new Set(["g", "path", "circle", "ellipse", "line", "polyline", "polygon", "rect"]);
  const shapeAttributes = new Set(["d", "cx", "cy", "r", "rx", "ry", "x", "y", "width", "height",
    "x1", "x2", "y1", "y2", "points", "transform", "fill", "stroke", "stroke-width", "stroke-linecap",
    "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray", "fill-rule", "clip-rule", "opacity", "fill-opacity",
    "stroke-opacity", "vector-effect", "data-source-id"]);

  function svgNode(tag, attributes) {
    const node = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    return node;
  }

  function sourcedShape(descriptor, hit = false, inherited = { stroke: "none", width: 1.2 }, budget = { nodes: 0 }) {
    if (++budget.nodes > 1200) throw new Error("Diagram is too complex");
    if (!Array.isArray(descriptor) || !shapeTags.has(descriptor[0])) throw new Error("Invalid diagram shape");
    const [tag, attributes = {}, children = []] = descriptor;
    const stroke = attributes.stroke === undefined ? inherited.stroke : attributes.stroke;
    const requestedWidth = attributes["stroke-width"] === undefined ? inherited.width : Number(attributes["stroke-width"]);
    const width = Number.isFinite(requestedWidth) && requestedWidth > 0 ? requestedWidth : 1.2;
    const node = svgNode(tag, {});
    for (const [key, value] of Object.entries(attributes)) {
      if (!shapeAttributes.has(key) || !["string", "number"].includes(typeof value) || /url\s*\(/i.test(String(value))) {
        throw new Error("Invalid diagram shape attribute");
      }
      if (hit && /^(?:fill|stroke|opacity|vector-effect)/.test(key)) continue;
      node.setAttribute(key, value);
    }
    if (tag !== "g") {
      node.setAttribute("class", hit ? "specifier-hit-shape" : "specifier-art-shape");
      if (!hit && stroke !== "none") {
        node.classList.add("specifier-outline");
        node.style.setProperty("--specifier-source-stroke", String(width));
      }
      if (hit) {
        node.setAttribute("fill", "none");
        node.setAttribute("stroke", "transparent");
        node.setAttribute("stroke-width", "14");
        node.setAttribute("stroke-linecap", "round");
        node.setAttribute("stroke-linejoin", "round");
        node.setAttribute("vector-effect", "non-scaling-stroke");
        node.setAttribute("pointer-events", "stroke");
      }
    }
    if (!Array.isArray(children)) throw new Error("Invalid diagram shape children");
    for (const child of children) node.append(sourcedShape(child, hit, { stroke, width }, budget));
    return node;
  }

  function diagramSVG(definition, art, groups) {
    if (!art || typeof art.viewBox !== "string" || !Array.isArray(art.context) || !art.parts) return null;
    const box = art.viewBox.trim().split(/[\s,]+/).map(Number);
    if (box.length !== 4 || !box.every(Number.isFinite) || box[2] <= 0 || box[3] <= 0) return null;
    try {
      const budget = { nodes: 0 };
      const svg = svgNode("svg", { class: "specifier-svg", viewBox: art.viewBox, role: "group", "aria-label": `${definition.label} parts` });
      if (art.attribution) {
        const metadata = svgNode("metadata", {});
        metadata.textContent = JSON.stringify(art.attribution);
        svg.append(metadata);
      }
      const context = svgNode("g", { class: "specifier-context", "aria-hidden": "true" });
      for (const shape of art.context) context.append(sourcedShape(shape, false, undefined, budget));
      svg.append(context);
      // Preserve the source's assembly order, placing small parts above wheels.
      for (const [id, shapes] of Object.entries(art.parts)) {
        const item = definition.parts.find(candidate => candidate.id === id);
        if (!item) continue;
        if (!Array.isArray(shapes) || !shapes.length) continue;
        const group = svgNode("g", { class: "specifier-part", "data-part": id, tabindex: "0", role: "button", "aria-label": item.label, "aria-pressed": "false" });
        const geometry = svgNode("g", { class: "specifier-geometry", "aria-hidden": "true" });
        const hit = svgNode("g", { class: "specifier-hit", "aria-hidden": "true" });
        for (const shape of shapes) {
          geometry.append(sourcedShape(shape, false, undefined, budget));
          hit.append(sourcedShape(shape, true, undefined, budget));
        }
        group.append(geometry, hit);
        groups.set(id, group);
        svg.append(group);
      }
      return budget.nodes ? svg : null;
    } catch {
      groups.clear();
      return null;
    }
  }

  function matchPart(definition, answer) {
    const text = clean(answer);
    if (!text) return null;
    const padded = ` ${text} `;
    let best = null, length = 0;
    for (const item of definition.parts) {
      for (const alias of item.aliases) {
        if (alias === text) return item;
        if (alias.length > length && padded.includes(` ${alias} `)) { best = item; length = alias.length; }
      }
    }
    return best;
  }

  function mount(container, subject, onSelect) {
    const definition = resolve(subject);
    if (!container) return null;
    const token = {};
    mounts.set(container, token);
    container.replaceChildren();
    container.hidden = !definition || !definition.parts.length;
    if (!definition || !definition.parts.length && !definition.artUrl) return null;
    container.setAttribute("aria-label", `${definition.label} parts and symptoms`);
    const groups = new Map(), options = new Map();
    const cached = definition.artUrl ? artCache.get(definition.artUrl)?.data : null;
    let svg = diagramSVG(definition, cached, groups);
    container.hidden = !definition.parts.length && !svg;
    container.classList.toggle("is-list-only", !svg);
    const list = document.createElement("span");
    list.className = "specifier-list";
    list.setAttribute("role", "group");
    list.setAttribute("aria-label", `${definition.label} options`);
    let answer = "", selected = "", preview = "", disposed = false;

    function paint() {
      svg?.classList.toggle("has-highlight", groups.has(selected) || groups.has(preview));
      for (const item of definition.parts) {
        for (const node of [groups.get(item.id), options.get(item.id)]) {
          if (!node) continue;
          node.classList.toggle("is-selected", selected === item.id);
          node.classList.toggle("is-highlighted", preview === item.id);
          node.setAttribute("aria-pressed", String(selected === item.id));
        }
      }
    }

    function sync(value) {
      if (disposed) return;
      answer = String(value);
      selected = matchPart(definition, answer)?.id || "";
      paint();
    }

    function highlight(id, scroll) {
      if (disposed) return;
      preview = id;
      paint();
      if (scroll) options.get(id)?.scrollIntoView?.({ block: "nearest" });
    }

    function select(item) {
      if (disposed) return;
      const value = selected === item.id && answer.trim() === item.query ? "" : item.query;
      sync(value);
      preview = "";
      paint();
      onSelect(value);
    }

    function bind(node, item, fromSVG) {
      node.addEventListener("pointerenter", () => highlight(item.id, fromSVG));
      node.addEventListener("pointerleave", () => {
        if (document.activeElement !== node && preview === item.id) highlight("", false);
      });
      node.addEventListener("focus", () => highlight(item.id, fromSVG));
      node.addEventListener("blur", () => { if (preview === item.id) highlight("", false); });
      node.addEventListener("click", event => { event.preventDefault(); select(item); });
      if (fromSVG) node.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(item); }
      });
    }

    for (const item of definition.parts) {
      const option = document.createElement("button");
      option.type = "button";
      option.className = "specifier-option";
      option.dataset.part = item.id;
      option.textContent = item.label;
      option.setAttribute("aria-pressed", "false");
      options.set(item.id, option);
      bind(option, item, false);
      if (groups.has(item.id)) bind(groups.get(item.id), item, true);
      list.append(option);
    }
    const controls = document.createElement("span");
    controls.className = "specifier-controls";
    if (svg) controls.append(svg);
    if (definition.parts.length) controls.append(list);
    container.append(controls);
    if (!svg && definition.artUrl) loadArt(definition, true).then(art => {
      if (disposed || mounts.get(container) !== token || !art) return;
      const diagram = diagramSVG(definition, art, groups);
      if (!diagram) return;
      svg = diagram;
      for (const item of definition.parts) if (groups.has(item.id)) bind(groups.get(item.id), item, true);
      // Keep the existing list, its scroll position and native input untouched.
      if (definition.parts.length) controls.insertBefore(svg, list);
      else controls.append(svg);
      container.hidden = false;
      container.classList.remove("is-list-only");
      paint();
      window.dispatchEvent?.(new Event("mend:layout"));
    });
    return {
      sync,
      destroy() {
        disposed = true;
        if (mounts.get(container) !== token) return;
        mounts.delete(container);
        container.replaceChildren();
        container.hidden = true;
        container.classList.remove("is-list-only");
        container.removeAttribute("aria-label");
      },
    };
  }

  window.mendSpecifiers = Object.freeze({ resolve, configure, mount, prefetch,
    isWord: word => nounWords.has(precise(word)),
    get nouns() { return Object.freeze([...aliases.keys()]); },
  });
})();
