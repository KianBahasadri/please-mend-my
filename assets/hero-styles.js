(() => {
  "use strict";

  const originals = ["pixel", "editorial", "comic", "poster", "typewriter", "handwritten", "toolbox"];
  const repairs = ["stitched", "taped", "tooltype", "spareparts", "blueprint", "cutpaper"];
  const themes = Object.freeze([...originals, ...repairs]);
  const prompt = theme => theme === "toolbox" ? "🙏, 🔧 my " : "please, mend my ";
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
      ["path", {
        class: "repair-thread",
        d: "M 5 74 L 10 82 M 10 74 L 5 82 M 15 74 L 20 82 M 20 74 L 15 82 M 25 74 L 30 82 M 30 74 L 25 82 M 35 74 L 40 82 M 40 74 L 35 82 M 45 74 L 50 82 M 50 74 L 45 82 M 55 74 L 60 82 M 60 74 L 55 82 M 65 74 L 70 82 M 70 74 L 65 82 M 75 74 L 80 82 M 80 74 L 75 82 M 85 74 L 90 82 M 90 74 L 85 82 M 95 74 L 100 82 M 100 74 L 95 82"
      }],
      ["path", {
        class: "repair-thread",
        d: "M 100 82 Q 105 88 108 83 Q 111 77 114 84"
      }],
      ["path", {
        class: "repair-needle",
        d: "M 113.47 84.95 L 101.77 63.96 Q 100.16 60.25 102.79 63.33 L 116.53 83.05 Q 118.13 86.17 117.65 88.24 Q 115.58 87.76 113.47 84.95 Z"
      }],
      ["path", {
        class: "repair-needle-eye",
        d: "M 102.81 64.49 L 104.93 67.89"
      }],
      ["path", {
        class: "repair-needle-glint",
        d: "M 105.65 69.8 L 112.54 80.82"
      }],
    ]);
    svg.setAttribute("preserveAspectRatio", "none");
    return svg;
  }

  function wrench() {
    return drawing("repair-tool repair-wrench", [
      ["path", {
        d: "M2 4c0-3 3-5 7-5h14c4 0 7 2 7 5v18l-7-4c-3-1.5-6-1.5-9 0l-7 4V4Z M10 6h12v8H10Z",
        "fill-rule": "evenodd",
        fill: "currentColor"
      }],
      ["path", {
        d: "M4.5 19h23v56H4.5Z",
        fill: "currentColor"
      }],
      ["path", {
        class: "repair-tool-hole",
        d: "M9.5 24h13v46h-13Z"
      }],
      ["path", {
        d: "M2 74c0-4 3-7 7-7h14c4 0 7 3 7 7v22c0 4-3 6-7 6H9c-4 0-7-2-7-6V74Z M16 74l7 5v10l-7 5-7-5V79Z",
        "fill-rule": "evenodd",
        fill: "currentColor"
      }],
    ], "0 0 32 100");
  }

  function nail() {
    return drawing("repair-tool repair-nail", [
      ["path", {
        d: "M0 0h22v5H0Z",
        fill: "currentColor"
      }],
      ["path", {
        class: "repair-tool-hole",
        d: "M7 1h8v1.5H7Z"
      }],
      ["path", {
        d: "M4 5h14c0 7-1.5 14-4.5 21C11 32 7.5 39 3 44c2-7 5.5-14 8-21 1.5-6 2.5-12 2-18H4Z",
        fill: "currentColor"
      }],
    ], "0 0 24 45");
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
