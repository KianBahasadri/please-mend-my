(() => {
  "use strict";

  const originals = ["pixel", "editorial", "comic", "poster", "typewriter", "handwritten", "toolbox"];
  const repairs = ["stitched", "taped", "tooltype", "blueprint", "cutpaper"];
  const terminals = ["ascii", "rainbow", "matrix"];
  const decorated = [...repairs, "ascii", "rainbow"];
  const themes = Object.freeze([...originals, ...repairs, ...terminals]);
  const prompt = theme => theme === "toolbox" ? "🙏, 🔧 my " : "please, mend my ";
  const svgNS = "http://www.w3.org/2000/svg";
  // Five-column terminal banners. Generated CSS text is inert decoration, so
  // the actual prompt stays canonical and floater bursts never expand 400 bits.
  const asciiGlyphs = {
    p: ["#### ", "#   #", "#### ", "#    ", "#    "],
    l: ["#    ", "#    ", "#    ", "#    ", "#####"],
    e: ["#####", "#    ", "#### ", "#    ", "#####"],
    a: [" ### ", "#   #", "#####", "#   #", "#   #"],
    s: [" ####", "#    ", " ### ", "    #", "#### "],
    m: ["#   #", "## ##", "# # #", "#   #", "#   #"],
    n: ["#   #", "##  #", "# # #", "#  ##", "#   #"],
    d: ["#### ", "#   #", "#   #", "#   #", "#### "],
    y: ["#   #", " # # ", "  #  ", "  #  ", "  #  "],
    ",": ["     ", "     ", "     ", "  ## ", "  #  "],
    " ": ["     ", "     ", "     ", "     ", "     "],
  };

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

  function hammer() {
    return drawing("repair-tool repair-hammer", [
      ["path", {
        d: "M0 3h10V0h-8Z M10 2h22c6 0 11 4 11 12s-5 12-11 12H18v-8h12c3 0 5-2 5-4s-2-4-5-4H10Z M32 2c8 0 13 6 12 16-1-3-5-6-10-6Z",
        "fill-rule": "evenodd",
        fill: "currentColor"
      }],
      ["path", {
        d: "M7 10h11v70H7Z M6 52h13v28H6Z",
        fill: "currentColor"
      }],
      ["path", {
        class: "repair-tool-hole",
        d: "M5.5 56h14v2.5h-14Z M5.5 61h14v2.5h-14Z M5.5 66h14v2.5h-14Z M5.5 71h14v2.5h-14Z"
      }],
      ["circle", {
        class: "repair-tool-hole",
        cx: "12.5",
        cy: "76",
        r: "2"
      }],
    ], "0 0 44 82");
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

  function clamp() {
    return drawing("repair-tool repair-clamp", [
      ["path", {
        d: "M4 0h30c3 0 5 2 5 5v7H15c-3 0-3 4-3 4h26v8H12s0 12 3 12h24v8c0 3-2 4-5 4H4C1 48 0 46 0 43V5C0 2 1 0 4 0Z",
        "fill-rule": "evenodd",
        fill: "currentColor"
      }],
      ["path", {
        class: "repair-tool-hole",
        d: "M16 19h20v2H16Z M36 16.5h2.5v7H36Z"
      }],
    ], "0 0 40 48");
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

  function ruler() {
    return drawing("repair-tool repair-ruler", [
      ["path", {
        d: "M0 0h13v48H0Z M11 0h5l19 44-5 4Z M30 48l-5-4 19-44h5Z M47 0h13v48H47Z",
        fill: "currentColor"
      }],
      ["circle", { cx: "6.5", cy: "4", r: "3", class: "repair-tool-hole" }],
      ["circle", { cx: "6.5", cy: "4", r: "1.5", fill: "currentColor" }],
      ["circle", { cx: "30", cy: "44", r: "3", class: "repair-tool-hole" }],
      ["circle", { cx: "30", cy: "44", r: "1.5", fill: "currentColor" }],
      ["circle", { cx: "53.5", cy: "4", r: "3", class: "repair-tool-hole" }],
      ["circle", { cx: "53.5", cy: "4", r: "1.5", fill: "currentColor" }],
      ["path", {
        class: "repair-tool-hole",
        d: "M0 12h4v1.5H0Z M0 20h6v1.5H0Z M0 28h4v1.5H0Z M0 36h6v1.5H0Z M56 12h4v1.5h-4Z M54 20h6v1.5h-6Z M56 28h4v1.5h-4Z M54 36h6v1.5h-6Z"
      }],
    ], "0 0 60 48");
  }

  function level() {
    return drawing("repair-tool repair-level", [
      ["path", {
        d: "M26 0h14v64H26Z M26 16H12C5 16 0 21 0 28v24c0 7 5 12 12 12h14v-9H12c-4 0-6-2-6-6V31c0-4 2-6 6-6h14Z",
        "fill-rule": "evenodd",
        fill: "currentColor"
      }],
      ["rect", {
        x: "7",
        y: "36",
        width: "18",
        height: "8",
        rx: "4",
        class: "repair-tool-hole"
      }],
      ["circle", {
        cx: "16",
        cy: "40",
        r: "2.5",
        fill: "currentColor"
      }],
      ["path", {
        d: "M12 36v8 M20 36v8",
        stroke: "currentColor",
        "stroke-width": "0.8"
      }],
    ], "0 0 40 64");
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

    if (theme === "tooltype" || theme === "cutpaper" || theme === "ascii" || theme === "rainbow") {
      const toolMap = {
        0: hammer,
        1: wrench,
        2: clamp,
        5: clamp,
        6: nail,
        8: ruler,
        9: clamp,
        11: level,
      };
      for (let at = 0; at < text.length; at++) {
        const glyph = add(at, at + 1, "repair-letter");
        if (text[at] === " ") glyph.classList.add("repair-space");
        if (theme === "ascii") glyph.dataset.ascii = asciiGlyphs[text[at]].join("\n");
        if (theme === "rainbow") glyph.style.setProperty("--rainbow-ink", `var(--rainbow-${at % 7})`);
        if (theme === "tooltype" && toolMap[at]) {
          glyph.classList.add("repair-replaced-letter");
          const original = span("repair-original-letter", text[at]);
          glyph.textContent = "";
          glyph.append(original, toolMap[at]());
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
      if (theme === "blueprint") {
        const dimension = span("repair-dimension");
        mend.append(dimension);
      }
    }
    for (const piece of pieces) position?.(piece);
    return art;
  }

  function decorateFloater(node, theme) {
    if (!decorated.includes(theme)) return;
    const art = build(theme);
    art.classList.add("hero-prompt-art--inline");
    node.replaceChildren(art);
  }

  function mount(field, prefixNode, theme) {
    if (!decorated.includes(theme) || !field || !prefixNode ||
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
