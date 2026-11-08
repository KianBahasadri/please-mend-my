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
  const aliases = new Map();
  for (const definition of definitions) {
    Object.freeze(definition.parts);
    Object.freeze(definition.aliases);
    Object.freeze(definition);
    for (const alias of definition.aliases) aliases.set(clean(alias), definition);
  }
  const resolve = subject => aliases.get(clean(subject)) || null;

  // Each assembly owns both its drawing and an ample transparent hit target.
  // The frame remains a noninteractive context behind all eleven assemblies.
  const bicycleGeometry = [
    ["wheels", "M134 157a49 49 0 1 1-98 0 49 49 0 1 1 98 0 M327 157a49 49 0 1 1-98 0 49 49 0 1 1 98 0 M85 108v98 M36 157h98 M50 122l70 70 M50 192l70-70 M278 108v98 M229 157h98 M243 122l70 70 M243 192l70-70"],
    ["tires", "M138 157a53 53 0 1 1-106 0 53 53 0 1 1 106 0 M331 157a53 53 0 1 1-106 0 53 53 0 1 1 106 0"],
    ["fork", "M237 69 246 98 278 157 M246 98l8-3 15 39-8 3 M245 101l14 35"],
    ["chain", "M86 151 165 147a10 10 0 0 1 0 20L86 163a6 6 0 0 1 0-12Z"],
    ["gears", "M93 157a8 8 0 1 1-16 0 8 8 0 1 1 16 0 M92 164l8 15 11-4-5-9 M100 179l-2 12 15-10 M231 48l6 12"],
    ["seat", "M134 78l-5-16 M112 58h34l10 5-9 4h-35l-5-4Z M134 77l8 18"],
    ["handlebars", "M236 70l-9-27h25l10 6-2 13 M227 43l-6-7 M248 43l4-7"],
    ["brakes", "M109 115l-9 11 M105 112l9 8 M253 116l10-7 M258 105l9 8 M251 44l-5 11 M243 48l-14 22-90 12-29 33 M247 56l12 49"],
    ["pedals", "M166 157l23 18 M181 175h18 M166 157l-21-18 M136 139h18"],
    ["bottom-bracket", "M178 157a12 12 0 1 1-24 0 12 12 0 1 1 24 0 M170 157a4 4 0 1 1-8 0 4 4 0 1 1 8 0"],
    ["lights", "M252 78h12v9h-12Z M265 77l7-3 M266 82h9 M265 87l7 3 M231 33a5 5 0 0 1 10 0 M230 34h12 M144 104l4 3 M149 108l3 2"],
  ];

  function svgNode(tag, attributes) {
    const node = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    return node;
  }

  function bikeSVG(definition, groups) {
    const svg = svgNode("svg", { class: "specifier-svg", viewBox: "0 0 360 220", role: "group", "aria-label": "Bicycle parts" });
    svg.append(svgNode("path", { class: "specifier-frame", d: "M85 157 134 77 166 157 85 157 M134 77 237 69 166 157 M237 69l9 29", "aria-hidden": "true" }));
    for (const [id, d] of bicycleGeometry) {
      const item = definition.parts.find(candidate => candidate.id === id);
      const group = svgNode("g", { class: "specifier-part", "data-part": id, tabindex: "0", role: "button", "aria-label": item.label, "aria-pressed": "false" });
      group.append(svgNode("path", { class: "specifier-geometry", d, stroke: "currentColor", fill: "none", "aria-hidden": "true" }));
      group.append(svgNode("path", { class: "specifier-hit", d, stroke: "transparent", "stroke-width": "14", "stroke-linecap": "round", "stroke-linejoin": "round", fill: "none", "pointer-events": "stroke", "aria-hidden": "true" }));
      groups.set(id, group);
      svg.append(group);
    }
    return svg;
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
    container.replaceChildren();
    container.hidden = !definition;
    container.classList.toggle("is-list-only", Boolean(definition && !definition.svg));
    if (!definition) return null;
    container.setAttribute("aria-label", `${definition.label} parts and symptoms`);
    const groups = new Map(), options = new Map();
    const svg = definition.svg ? bikeSVG(definition, groups) : null;
    const list = document.createElement("span");
    list.className = "specifier-list";
    list.setAttribute("role", "group");
    list.setAttribute("aria-label", `${definition.label} options`);
    let answer = "", selected = "", preview = "", disposed = false;

    function paint() {
      svg?.classList.toggle("has-highlight", Boolean(selected || preview));
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
    if (svg) container.append(svg);
    container.append(list);
    return {
      sync,
      destroy() {
        disposed = true;
        container.replaceChildren();
        container.hidden = true;
        container.classList.remove("is-list-only");
        container.removeAttribute("aria-label");
      },
    };
  }

  window.mendSpecifiers = Object.freeze({ resolve, nouns: Object.freeze([...aliases.keys()]), mount });
})();
