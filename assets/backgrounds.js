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

  window.pickMendVariant = (storageKey, options, cadence = 1) => {
    const state = readFallback();
    let previous = state[namespace]?.[storageKey];
    try {
      previous = previous || localStorage.getItem(storageKey);
    } catch {
      // Keep separate font and lighting histories when browser storage is blocked.
    }
    let uses = 0;
    if (cadence > 1) {
      try {
        const cycle = JSON.parse(previous);
        if (cycle && options.includes(cycle.variant)) {
          previous = cycle.variant;
          if (Number.isInteger(cycle.uses) && cycle.uses >= 1 && cycle.uses <= cadence) uses = cycle.uses;
        }
      } catch {
        // A previous plain choice starts a fresh cycle after the upgrade.
      }
    }
    const alternatives = options.filter((option) => option !== previous);
    const choices = alternatives.length ? alternatives : options;
    const chosen = cadence > 1 && options.includes(previous) && uses < cadence
      ? previous : choices[Math.floor(Math.random() * choices.length)];
    const saved = cadence > 1 ? JSON.stringify({ variant: chosen, uses: chosen === previous ? uses + 1 : 1 }) : chosen;
    try {
      localStorage.setItem(storageKey, saved);
      if (state[namespace]?.[storageKey] !== undefined) {
        delete state[namespace][storageKey];
        window.name = JSON.stringify(state);
      }
    } catch {
      state[namespace] = { ...state[namespace], [storageKey]: saved };
      window.name = JSON.stringify(state);
    }
    return chosen;
  };

  const backgrounds = ["approved", "left", "wide", "narrow", "low", "crosslight", "distant"];
  document.documentElement.dataset.background = window.pickMendVariant("please-mend-my-background", backgrounds, 3);
})();
