(() => {
  "use strict";

  const ns = "http://www.w3.org/2000/svg";
  const clean = value => String(value).normalize("NFKD").replace(/\p{M}/gu, "")
    .toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
  const part = (id, label, query, aliases = []) => Object.freeze({
    id, label, query, aliases: Object.freeze([...new Set([query, label, ...aliases].map(clean))]),
  });
  const definitions = [
    {
      id: "bicycle", label: "Bicycle", svg: true,
      aliases: ["bike", "bikes", "bicycle", "bicycles", "ebike", "e-bike", "electric bike", "road bike", "mountain bike"],
      parts: [
        part("chain", "Chain", "chain", ["chains", "master link", "chain link"]),
        part("tires", "Tires & Tubes", "tire", ["tires", "tyre", "tyres", "tube", "tubes", "flat", "flat tire"]),
        part("brakes", "Brakes & Cables", "brake", ["brakes", "brake cable", "brake cables", "brake pads"]),
        part("gears", "Gears & Derailleur", "derailleur", ["gear", "gears", "shifting", "shift", "shifter"]),
        part("wheels", "Wheels & Spokes", "wheel", ["wheels", "spoke", "spokes", "rim", "rims"]),
        part("handlebars", "Handlebars & Stem", "handlebar", ["handlebars", "stem", "grips"]),
        part("seat", "Seat & Seatpost", "seat", ["saddle", "seatpost", "seat post"]),
        part("pedals", "Pedals & Cranks", "pedal", ["pedals", "crank", "cranks", "crank arm"]),
        part("bottom-bracket", "Bottom Bracket", "bottom bracket", ["bottom brackets"]),
        part("fork", "Fork & Suspension", "fork", ["forks", "suspension"]),
        part("lights", "Lights, Bell & Rust", "light", ["lights", "bell", "rust"]),
      ],
    },
    {
      id: "arm", label: "Arm", aliases: ["arm", "arms"],
      parts: [part("pain", "Pain", "pain", ["hurts", "aches"]), part("swelling", "Swelling", "swelling", ["swollen"]),
        part("injury", "Injury or fracture", "injury", ["fracture", "broken"]), part("numbness", "Numbness", "numbness", ["tingling"]),
        part("elbow", "Elbow", "elbow"), part("wrist", "Wrist", "wrist"), part("shoulder", "Shoulder", "shoulder")],
    },
    {
      id: "laptop", label: "Laptop", aliases: ["laptop", "laptops", "computer", "macbook", "notebook"],
      parts: [part("battery", "Battery & charging", "battery", ["charging", "charger"]), part("screen", "Screen", "screen", ["display"]),
        part("keyboard", "Keyboard", "keyboard", ["keys"]), part("trackpad", "Trackpad", "trackpad", ["touchpad"]),
        part("wifi", "Wi-Fi", "wifi", ["wi fi", "wireless"]), part("fan", "Fan & overheating", "fan", ["overheating", "hot"]),
        part("power", "Power & startup", "power", ["startup", "boot"]), part("audio", "Sound", "audio", ["sound", "speaker"])],
    },
    {
      id: "phone", label: "Phone", aliases: ["phone", "phones", "smartphone", "iphone", "mobile phone"],
      parts: [part("screen", "Screen", "screen", ["display"]), part("battery", "Battery", "battery"),
        part("charging", "Charging port", "charging port", ["charger", "charging"]), part("camera", "Camera", "camera"),
        part("speaker", "Speaker & microphone", "speaker", ["microphone", "audio", "sound"]),
        part("buttons", "Buttons", "button", ["buttons", "power button"]), part("water", "Water damage", "water damage", ["water", "wet"])],
    },
    {
      id: "car", label: "Car", aliases: ["car", "cars", "truck", "vehicle"],
      parts: [part("battery", "Battery", "battery"), part("tires", "Tires", "tire", ["tires", "tyre", "flat"]),
        part("brakes", "Brakes", "brake", ["brakes"]), part("engine", "Engine", "engine"), part("oil", "Oil & leaks", "oil", ["leak", "leaks"]),
        part("lights", "Lights", "light", ["lights", "headlight"]), part("starter", "Starting", "starter", ["starting", "start"]),
        part("wipers", "Windshield wipers", "wiper", ["wipers"])],
    },
    {
      id: "stove", label: "Stove", aliases: ["stove", "stoves", "oven", "range", "cooktop"],
      parts: [part("burner", "Burners", "burner", ["burners"]), part("igniter", "Igniter", "igniter", ["ignition"]),
        part("element", "Heating element", "heating element", ["element", "not heating"]), part("knob", "Knobs & controls", "knob", ["knobs", "controls"]),
        part("door", "Oven door", "door", ["hinge"]), part("thermostat", "Temperature", "thermostat", ["temperature"])],
    },
    {
      id: "toilet", label: "Toilet", aliases: ["toilet", "toilets"],
      parts: [part("clog", "Clogged drain", "clog", ["clogged", "blocked"]), part("flapper", "Flapper & running water", "flapper", ["running"]),
        part("fill-valve", "Fill valve", "fill valve"), part("leak", "Leaks", "leak", ["leaks", "leaking"]),
        part("handle", "Flush handle", "handle", ["flush", "flushing"]), part("seat", "Seat", "seat")],
    },
    {
      id: "refrigerator", label: "Refrigerator", aliases: ["refrigerator", "refrigerators", "fridge", "freezer"],
      parts: [part("cooling", "Not cooling", "not cooling", ["warm", "cooling"]), part("seal", "Door seal", "door seal", ["seal", "gasket"]),
        part("ice", "Ice maker", "ice maker", ["ice"]), part("leak", "Leaks", "leak", ["leaks", "leaking"]),
        part("fan", "Fan & noise", "fan", ["noise", "noisy"]), part("thermostat", "Thermostat", "thermostat", ["temperature"])],
    },
    {
      id: "jeans", label: "Jeans", aliases: ["jeans", "pants", "trousers", "denim"],
      parts: [part("hole", "Holes & tears", "hole", ["holes", "tear", "tears", "ripped"]), part("zipper", "Zipper", "zipper", ["zip"]),
        part("button", "Button", "button", ["buttons"]), part("seam", "Seams", "seam", ["seams"]),
        part("hem", "Hem", "hem", ["hemming"]), part("pocket", "Pockets", "pocket", ["pockets"])],
    },
  ];
  const pilot = window.mendSpecifierPilot;
  const artURL = (id, value) => typeof value === "string" &&
    new RegExp(`^/assets/art/${id}-[a-f0-9]{64}\\.json$`).test(value) ? value : "";
  if (pilot?.version === 1 && Array.isArray(pilot.archetypes)) {
    for (const item of pilot.archetypes) {
      if (!item || typeof item.id !== "string" || !/^[a-z][a-z0-9-]{0,48}$/.test(item.id) || item.id === "bicycle") continue;
      const index = definitions.findIndex(definition => definition.id === item.id);
      if (typeof item.label !== "string" || !item.label.trim() || item.label.length > 100 ||
          !Array.isArray(item.parts) || !item.parts.length || item.parts.length > 24 ||
          !item.parts.every(value => value && /^[a-z][a-z0-9-]*$/.test(value.id) &&
            typeof value.label === "string" && value.label.length <= 100 &&
            typeof value.query === "string" && value.query.trim() && value.query.length <= 120 &&
            Array.isArray(value.aliases) && value.aliases.every(alias => typeof alias === "string" && alias.length <= 120)) ||
          new Set(item.parts.map(value => value.id)).size !== item.parts.length ||
          !Array.isArray(item.aliases) || !item.aliases.every(alias => typeof alias === "string" && alias.length <= 120)) continue;
      const base = definitions[index] || { id: item.id, label: item.label, aliases: [] };
      const definition = { ...base,
        aliases: [...new Set([...base.aliases, ...item.aliases])],
        parts: item.parts.map(value => part(value.id, value.label, value.query, value.aliases)),
        artUrl: artURL(base.id, item.artUrl),
      };
      if (index < 0) definitions.push(definition); else definitions[index] = definition;
    }
  }
  const aliases = new Map();
  for (const definition of definitions) {
    Object.freeze(definition.parts);
    Object.freeze(definition.aliases);
    Object.freeze(definition);
    for (const alias of definition.aliases) aliases.set(clean(alias), definition);
  }
  const resolve = subject => aliases.get(clean(subject)) || null;

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
    const exact = resolve(value);
    if (exact) { loadArt(exact, true); return; }
    const matches = new Set();
    for (const [alias, definition] of aliases) {
      if (alias.startsWith(value) && definition.artUrl) matches.add(definition);
      if (matches.size === 2) break;
    }
    for (const definition of matches) loadArt(definition);
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
    if (!Array.isArray(descriptor) || !shapeTags.has(descriptor[0])) throw new Error("Invalid bicycle shape");
    const [tag, attributes = {}, children = []] = descriptor;
    const stroke = attributes.stroke === undefined ? inherited.stroke : attributes.stroke;
    const requestedWidth = attributes["stroke-width"] === undefined ? inherited.width : Number(attributes["stroke-width"]);
    const width = Number.isFinite(requestedWidth) && requestedWidth > 0 ? requestedWidth : 1.2;
    const node = svgNode(tag, {});
    for (const [key, value] of Object.entries(attributes)) {
      if (!shapeAttributes.has(key) || !["string", "number"].includes(typeof value) || /url\s*\(/i.test(String(value))) {
        throw new Error("Invalid bicycle shape attribute");
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
    if (!Array.isArray(children)) throw new Error("Invalid bicycle shape children");
    for (const child of children) node.append(sourcedShape(child, hit, { stroke, width }, budget));
    return node;
  }

  function diagramSVG(definition, art, groups) {
    if (!art || typeof art.viewBox !== "string" || !Array.isArray(art.context) || !art.parts) return null;
    const box = art.viewBox.trim().split(/[\s,]+/).map(Number);
    if (box.length !== 4 || !box.every(Number.isFinite) || box[2] <= 0 || box[3] <= 0) return null;
    if (definition.id === "bicycle" && !definition.parts.every(item => Array.isArray(art.parts[item.id]) && art.parts[item.id].length)) return null;
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
      return groups.size ? svg : null;
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
    container.hidden = !definition;
    if (!definition) return null;
    container.setAttribute("aria-label", `${definition.label} parts and symptoms`);
    const groups = new Map(), options = new Map();
    const cached = definition.artUrl ? artCache.get(definition.artUrl)?.data : null;
    let svg = diagramSVG(definition, definition.id === "bicycle" ? window.mendBicycleArt : cached, groups);
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
    controls.append(list);
    container.append(controls);
    if (!svg && definition.artUrl) loadArt(definition, true).then(art => {
      if (disposed || mounts.get(container) !== token || !art) return;
      const diagram = diagramSVG(definition, art, groups);
      if (!diagram) return;
      svg = diagram;
      for (const item of definition.parts) if (groups.has(item.id)) bind(groups.get(item.id), item, true);
      // Keep the existing list, its scroll position and native input untouched.
      controls.insertBefore(svg, list);
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

  window.mendSpecifiers = Object.freeze({ resolve, nouns: Object.freeze([...aliases.keys()]), mount, prefetch });
})();
