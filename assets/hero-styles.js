(() => {
  "use strict";

  const originals = ["pixel", "editorial", "comic", "poster", "typewriter", "handwritten", "toolbox"];
  const repairs = ["stitched", "taped", "tooltype", "spareparts", "blueprint", "cutpaper"];
  const themes = Object.freeze([...originals, ...repairs]);
  const prompt = theme => theme === "toolbox" ? "🙏, 🔨 my " : "please, mend my ";
  const svgNS = "http://www.w3.org/2000/svg";

  function span(className, text = "") {
    const node = document.createElement("span");
    node.className = className;
    node.textContent = text;
    return node;
  }

  function drawing(className, paths, viewBox = "0 0 100 100") {
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("class", className);
    svg.setAttribute("viewBox", viewBox);
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    for (const [tag, attributes] of paths) {
      const node = document.createElementNS(svgNS, tag);
      for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
      svg.append(node);
    }
    return svg;
  }

  function stitches() {
    const svg = drawing("repair-stitches", [
      ["path", { class: "repair-gap", d: "M0 50 13 53 28 48 40 52 55 48 69 52 83 48 100 51" }],
      ["path", { class: "repair-thread", d: "M15 39 24 62 M24 39 15 62 M46 38 55 61 M55 38 46 61 M77 38 86 61 M86 38 77 61" }],
      ["path", { class: "repair-thread-shadow", d: "M16 39 25 62 M47 38 56 61 M78 38 87 61" }],
    ]);
    svg.setAttribute("preserveAspectRatio", "none");
    return svg;
  }

  function wrench() {
    return drawing("repair-tool repair-wrench", [
      ["path", { d: "M42 7a23 23 0 0 0-12 39l-2 40a9 9 0 0 0 18 0l-2-40A23 23 0 0 0 51 7l-1 20-13 7-11-8 2-19a23 23 0 0 0 14 0Z", fill: "currentColor" }],
      ["circle", { cx: "37", cy: "85", r: "3.4", class: "repair-tool-hole" }],
    ], "0 0 70 100");
  }

  function nail() {
    return drawing("repair-tool repair-nail", [
      ["path", { d: "M18 17 55 27 M37 23 27 53 41 65 19 92", fill: "none", stroke: "currentColor", "stroke-width": "9", "stroke-linecap": "square", "stroke-linejoin": "round" }],
    ], "0 0 70 100");
  }

  function mechanicalPart() {
    return drawing("repair-spare-part", [
      ["path", { d: "M30 14 49 14 45 86 26 86Z", class: "repair-metal" }],
      ["path", { d: "M32 20 43 20 M29 80 40 80", class: "repair-metal-edge" }],
      ["circle", { cx: "38", cy: "28", r: "3", class: "repair-rivet" }],
      ["circle", { cx: "35", cy: "72", r: "3", class: "repair-rivet" }],
      ["path", { d: "M36 28h4 M33 72h4", class: "repair-rivet-slot" }],
    ]);
  }

  function build(theme, position) {
    const art = span("hero-prompt-art");
    art.dataset.repair = theme;
    art.setAttribute("aria-hidden", "true");
    art.inert = true;
    const text = prompt(theme);
    const pieces = [];
    const add = (start, end, name) => {
      const node = span(`repair-piece ${name}`, text.slice(start, end));
      pieces.push({ node, start, end });
      art.append(node);
      return node;
    };

    if (theme === "tooltype" || theme === "cutpaper") {
      for (let at = 0; at < text.length; at++) {
        const glyph = add(at, at + 1, `repair-letter repair-letter-${at}`);
        if (text[at] === " ") glyph.classList.add("repair-space");
        if (theme === "tooltype" && (at === 1 || at === 6)) {
          glyph.classList.add("repair-replaced-letter");
          const original = span("repair-original-letter", text[at]);
          glyph.textContent = "";
          glyph.append(original, at === 1 ? wrench() : nail());
        }
        if (theme === "cutpaper" && text[at] !== " ") {
          // The measured box keeps the native font's advance. The clipped letter
          // inside it may use another already-local font without moving the caret.
          glyph.textContent = "";
          const cutting = span("repair-cutout");
          cutting.append(span("repair-cut-letter", text[at]));
          glyph.append(cutting);
        }
      }
    } else {
      add(0, 8, "repair-please");
      const mend = add(8, 12, "repair-mend");
      add(12, text.length, "repair-my");
      if (theme === "stitched") mend.append(stitches());
      if (theme === "spareparts") mend.append(mechanicalPart());
      if (theme === "blueprint") {
        const dimension = span("repair-dimension");
        dimension.append(span("repair-dimension-label", ""));
        mend.append(dimension);
      }
    }
    for (const piece of pieces) position?.(piece);
    return art;
  }

  function decorateFloater(node, theme) {
    if (!repairs.includes(theme)) return;
    const art = build(theme);
    art.classList.add("hero-prompt-art--inline");
    node.replaceChildren(art);
  }

  function mount(field, prefixNode, theme) {
    if (!repairs.includes(theme) || !field || !prefixNode ||
        typeof document.createRange !== "function" ||
        typeof document.createElementNS !== "function" ||
        !window.CSS?.supports?.("clip-path", "polygon(0 0, 100% 0, 100% 100%)")) return null;
    const input = field.querySelector("textarea");
    if (!input) return null;
    let key = "", artwork = null;

    function reset() {
      field.classList.remove("has-prompt-art");
      artwork?.remove();
      artwork = null;
      key = "";
    }

    function sync() {
      const source = prefixNode.firstChild;
      if (!source || source.nodeType !== 3 || source.textContent !== prompt(theme)) { reset(); return; }
      try {
        const style = getComputedStyle(input);
        if (getComputedStyle(field).getPropertyValue("--mend-art-ready").trim() !== "1") { reset(); return; }
        const size = parseFloat(style.fontSize);
        const line = parseFloat(style.lineHeight);
        const origin = field.getBoundingClientRect();
        const range = document.createRange();
        range.selectNodeContents(prefixNode);
        const bounds = range.getBoundingClientRect();
        const width = bounds.width;
        if (![size, line, width, origin.left, bounds.left].every(Number.isFinite) ||
            size <= 0 || line <= 0 || width <= 2 || width >= input.clientWidth ||
            bounds.height > line * 1.5) { reset(); return; }
        const nextKey = [width, bounds.height, line, style.font, style.fontFamily,
          style.fontWeight, style.fontSize, style.letterSpacing].join("|");
        if (key === nextKey && artwork) return;
        const positions = [];
        const candidate = build(theme, piece => {
          range.setStart(source, piece.start);
          range.setEnd(source, piece.end);
          const rect = range.getBoundingClientRect();
          const left = rect.left - origin.left;
          if (!Number.isFinite(left) || !Number.isFinite(rect.width) || rect.width <= 0) throw new Error("Invalid prefix bounds");
          positions.push({ node: piece.node, left, width: rect.width });
        });
        // Read all glyph bounds before writing DOM/styles, avoiding layout churn.
        for (const { node, left, width: pieceWidth } of positions) {
          node.style.left = `${left}px`;
          node.style.width = `${pieceWidth}px`;
        }
        field.style.setProperty("--prompt-cut", `${Math.max(0, width - 2)}px`);
        field.style.setProperty("--prompt-line-height", `${line}px`);
        candidate.style.width = `${width}px`;
        artwork?.remove();
        field.append(candidate);
        artwork = candidate;
        key = nextKey;
        field.classList.add("has-prompt-art");
      } catch {
        // Decoration must never interfere with the native input if measurement
        // or an optional browser paint API is unavailable.
        reset();
      }
    }
    return { sync };
  }

  window.mendHeroStyles = Object.freeze({ themes, prompt, mount, decorateFloater });
})();
