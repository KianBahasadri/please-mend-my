(() => {
  "use strict";

  // No canvas, observers or animation loop for any other hero style.
  if (document.documentElement.dataset.theme !== "matrix") return;
  const canvas = document.createElement("canvas");
  const context = canvas.getContext?.("2d", { alpha: true });
  if (!context || !window.requestAnimationFrame) return;
  canvas.className = "matrix-rain";
  canvas.setAttribute("aria-hidden", "true");
  canvas.inert = true;
  document.body.append(canvas);

  const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const light = window.matchMedia("(prefers-color-scheme: light)");
  const forced = window.matchMedia("(forced-colors: active)");
  const glyphs = [..."01ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉ"];
  const maxPixels = 1_500_000;
  const maxColumns = 48;
  const tail = 7;
  const interval = 1000 / 60;
  let width = 0, height = 0, columns = [], frame = 0, resizeFrame = 0;
  let last = null, visible = true, colors = [], quality = maxColumns;

  function clear() { context.clearRect(0, 0, width, height); }

  function palette() {
    const ink = light.matches ? "12,100,52" : "110,245,160";
    colors = Array.from({ length: tail }, (_, index) =>
      `rgba(${ink},${(0.86 * (1 - index / tail) ** 1.6).toFixed(3)})`);
  }

  function resize() {
    resizeFrame = 0;
    width = Math.max(1, Math.min(8192, window.innerWidth));
    height = Math.max(1, Math.min(8192, window.innerHeight));
    const scale = Math.min(2, window.devicePixelRatio || 1, Math.sqrt(maxPixels / (width * height)));
    canvas.width = Math.max(1, Math.floor(width * scale));
    canvas.height = Math.max(1, Math.floor(height * scale));
    context.setTransform(scale, 0, 0, scale, 0, 0);
    context.font = '14px "DejaVu Sans Mono", "Courier New", monospace';
    context.textBaseline = "top";
    const count = Math.max(1, Math.min(maxColumns, Math.floor(width / 26)));
    const spacing = width / count;
    columns = Array.from({ length: count }, (_, index) => ({
      x: Math.round((index + 0.5) * spacing), y: Math.random() * (height + tail * 18),
      speed: 34 + Math.random() * 52, seed: Math.floor(Math.random() * glyphs.length),
    }));
    last = null;
    quality = maxColumns;
    sync();
  }

  function allowed() {
    return visible && !motion.matches && !forced.matches &&
      document.visibilityState !== "hidden" && !document.body.classList.contains("reading");
  }

  function tick(now) {
    frame = 0;
    if (!allowed()) { clear(); return; }
    const elapsed = last === null ? 0 : now - last;
    // High-refresh displays must not multiply the canvas work. A delayed frame
    // advances once; it never runs an expensive catch-up loop.
    if (window.navigator?.scheduling?.isInputPending?.()) {
      frame = window.requestAnimationFrame(tick);
      return;
    }
    if (last === null || elapsed >= interval - 0.25) {
      const started = performance.now();
      const step = last === null ? 0 : Math.min(elapsed, 50) / 1000;
      last = now;
      clear();
      for (const column of columns) {
        column.y += column.speed * step;
        if (column.y > height + tail * 18) column.y = -18;
      }
      // At most 336 tiny glyphs and one clear per frame, independent of DPR or
      // catalog size. Never read layout, text values or input state in this loop.
      let drawn = 0;
      draw: for (let trail = tail - 1; trail >= 0; trail--) {
        context.fillStyle = colors[trail];
        for (let at = 0; at < Math.min(columns.length, quality); at++) {
          // Yield decoration after ~2ms on a slow device. The next frame uses
          // fewer columns; input always has priority over completing the rain.
          if ((drawn & 7) === 0 && performance.now() - started > 2) {
            quality = Math.max(8, quality - 8);
            break draw;
          }
          const column = columns[at];
          const y = column.y - trail * 18;
          if (y < -18 || y > height) continue;
          const character = (column.seed + trail * 3 + Math.floor(column.y / 18)) % glyphs.length;
          context.fillText(glyphs[(character + glyphs.length) % glyphs.length], column.x, y);
          drawn++;
        }
      }
    }
    frame = window.requestAnimationFrame(tick);
  }

  function sync() {
    if (allowed()) {
      if (!frame) { last = null; frame = window.requestAnimationFrame(tick); }
    } else {
      if (frame) window.cancelAnimationFrame(frame);
      frame = 0;
      last = null;
      clear();
    }
  }

  function queueResize() {
    if (!resizeFrame) resizeFrame = window.requestAnimationFrame(resize);
  }

  function changePalette() { palette(); clear(); sync(); }

  window.addEventListener("resize", queueResize, { passive: true });
  document.addEventListener("visibilitychange", sync);
  motion.addEventListener("change", sync);
  forced.addEventListener("change", sync);
  light.addEventListener("change", changePalette);
  const observer = new MutationObserver(sync);
  observer.observe(document.body, { attributes: true, attributeFilter: ["class"] });
  const hero = document.querySelector(".hero-area");
  const intersection = hero && typeof IntersectionObserver === "function" ?
    new IntersectionObserver(entries => { visible = entries[0]?.isIntersecting !== false; sync(); }) : null;
  if (intersection) intersection.observe(hero);

  // bfcache restoration resumes normally; a real page departure tears down work.
  window.addEventListener("pagehide", () => {
    visible = false;
    sync();
    if (resizeFrame) window.cancelAnimationFrame(resizeFrame);
    resizeFrame = 0;
  });
  window.addEventListener("pageshow", () => { visible = true; queueResize(); });
  palette();
  resize();
})();
