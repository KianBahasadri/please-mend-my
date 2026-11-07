(() => {
  "use strict";

  const namespace = "please-mend-my";

  function readFallback() {
    try {
      const state = JSON.parse(window.name || "{}");
      if (state && typeof state === "object" && !Array.isArray(state)) return state;
    } catch {
      // Migrate the original font-only fallback when browser storage is blocked.
      if (window.name.startsWith(`${namespace}:`)) {
        return { [namespace]: { "please-mend-my-theme": window.name.slice(namespace.length + 1) } };
      }
    }
    return {};
  }

  window.pickMendVariant = (storageKey, options) => {
    const state = readFallback();
    let previous = state[namespace]?.[storageKey];
    try {
      previous = localStorage.getItem(storageKey) || previous;
    } catch {
      // window.name keeps font and background choices separate across reloads.
    }
    const alternatives = options.filter((option) => option !== previous);
    const choices = alternatives.length ? alternatives : options;
    const chosen = choices[Math.floor(Math.random() * choices.length)];
    try {
      localStorage.setItem(storageKey, chosen);
    } catch {
      state[namespace] = { ...state[namespace], [storageKey]: chosen };
      window.name = JSON.stringify(state);
    }
    return chosen;
  };

  const presets = ["contours", "dunes", "gravity", "terrain", "spotlight", "sidelight", "horizon", "eclipse"];
  const background = window.pickMendVariant("please-mend-my-background", presets);
  document.documentElement.dataset.background = background;

  const point = (x, y) => `${x.toFixed(1)},${y.toFixed(1)}`;
  const path = (points, close = false) => `<path d="M${points.join("L")}${close ? "Z" : ""}"/>`;
  const sample = (count, draw) => Array.from({ length: count }, (_, i) => draw(i));

  const geometry = {
    contours() {
      return sample(17, (ring) => {
        const radius = 125 + ring * 43;
        return path(sample(121, (i) => {
          const angle = i * Math.PI / 60;
          const ripple = 1 + 0.1 * Math.sin(angle * 3 + 0.4) + 0.055 * Math.cos(angle * 5 - 0.6);
          return point(1110 + radius * ripple * Math.cos(angle), 710 + radius * 0.68 * ripple * Math.sin(angle));
        }), true);
      }).join("");
    },
    dunes() {
      return sample(19, (row) => path(sample(61, (i) => {
        const x = -200 + i * 34;
        const y = -80 + row * 67 + 105 * Math.sin(x * 0.003 + row * 0.15) + 33 * Math.cos(x * 0.007 + row * 0.09);
        return point(x, y);
      }))).join("");
    },
    gravity() {
      const warp = (x, y) => {
        const dx = x - 1080;
        const dy = y - 660;
        const distance = Math.hypot(dx, dy);
        const pull = 180 * Math.exp(-distance * distance / 240000);
        return point(x + dx / (distance + 50) * pull * 0.32, y + pull);
      };
      return [
        ...sample(21, (column) => path(sample(41, (i) => warp(-200 + column * 100, -100 + i * 30)))),
        ...sample(17, (row) => path(sample(61, (i) => warp(-200 + i * 34, -100 + row * 75))))
      ].join("");
    },
    terrain() {
      const floor = (x, depth) => point(x, 565 + depth ** 1.8 * 570 + Math.sin(x * 0.004) * 74 * depth + Math.cos(x * 0.008) * 16 * depth);
      return [
        ...sample(18, (row) => path(sample(51, (i) => floor(-400 + i * 48, (row + 1) / 18)))),
        ...sample(25, (column) => path(sample(31, (i) => {
          const depth = i / 30;
          return floor(850 + (-1800 + column * 150) * depth, depth);
        })))
      ].join("");
    },
    horizon() {
      return sample(5, (i) => `<path d="M-150,${765 + i * 37}C300,${570 + i * 33} 1060,${610 + i * 28} 1750,${900 + i * 40}"/>`).join("");
    }
  };

  function render() {
    const layer = document.querySelector(".atmosphere");
    if (!layer) return;
    const lines = geometry[background]?.();
    if (lines) {
      layer.innerHTML = `<svg class="atmosphere-lines" viewBox="0 0 1600 1000" preserveAspectRatio="xMidYMid slice" aria-hidden="true" xmlns="http://www.w3.org/2000/svg">${lines}</svg>`;
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", render, { once: true });
  else render();
})();
