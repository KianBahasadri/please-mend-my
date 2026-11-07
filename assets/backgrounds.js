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
      // window.name keeps font history across reloads when storage is blocked.
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
})();
