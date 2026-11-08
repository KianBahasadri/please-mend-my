(() => {
  "use strict";

  const first = document.getElementById("response");
  const second = document.getElementById("clarification-response");
  const line = document.getElementById("clarification-line");
  const question = document.getElementById("clarification-question");
  const fixedUnderline = document.getElementById("hero-prefix");
  const firstUnderline = document.getElementById("hero-suffix");
  const secondUnderline = document.getElementById("clarification-underline");
  const specifierContainer = document.getElementById("topic-specifier");
  const specifiers = window.mendSpecifiers;
  const heading = document.querySelector("h1");
  const measure = document.getElementById("measure");
  const theme = document.documentElement.dataset.theme;
  const styles = window.mendHeroStyles;
  const prompt = styles?.prompt?.(theme) ?? (theme === "toolbox" ? "🙏, 🔨 my " : "please, mend my ");
  let promptArt;
  // A small local vocabulary makes the automatic prompt conservative. Explicit
  // Enter/Tab can also clarify an unfamiliar single-word object; phrases do not.
  const broadNouns = new Set(("arm leg hand foot feet knee shoulder elbow wrist ankle finger toe back neck head tooth teeth eye ear " +
    "car truck van motorcycle bicycle bike scooter engine tire wheel boat " +
    "laptop computer desktop macbook phone iphone smartphone tablet ipad screen keyboard mouse printer camera radio speaker headphones watch " +
    "stove oven refrigerator fridge freezer dishwasher washer dryer microwave kettle toaster blender mixer vacuum lamp light clock iron " +
    "sink faucet toilet shower door window roof wall floor chair table desk sofa couch bed furniture " +
    "jacket coat jeans pants shirt dress shoe shoes boot boots bag backpack zipper " +
    "relationship marriage friendship family resume cv code website app software").split(/\s+/));
  for (const name of ["washing machine", "air conditioner", "coffee maker", "game console", "water heater", "mobile phone", "sewing machine"]) broadNouns.add(name);
  const notSubjects = new Set(["broken", "damaged", "hurts", "painful", "fix", "repair", "please", "my", "the", "a", "an", "not"]);
  // Keep object recognition aligned with the worker's conversational search
  // contract. Only the displayed question is cleaned; typed input/query is not.
  const conversational = new Set(["is", "are", "was", "were", "am", "seems", "feels", "has", "have", "does", "do", "did"]);
  const pronouns = new Set(["it", "this", "that", "they", "i", "we"]);
  const determiners = new Set(["my", "our", "your", "the", "a", "an"]);
  const intensifiers = new Set(["fucking", "fuckin", "fucken", "damn", "damned", "goddamn", "goddamned", "frigging", "freaking"]);
  const contractions = new Set(["it's", "that's", "this's", "there's", "i'm", "you're", "we're", "they're"]);
  const symptomPredicates = new Set(["hurts", "hurt", "aches", "ache", "bleeds", "bleed", "burns", "burn", "itches", "itch",
    "tingles", "tingle", "swells", "swell", "leaks", "leak", "rattles", "rattle", "freezes", "freeze", "crashes", "crash"]);
  const negativeContraction = /^(?:isn|aren|wasn|weren|don|doesn|didn|hasn|haven|can|won)'t$/;
  for (const group of [conversational, pronouns, determiners, intensifiers, contractions]) {
    for (const word of group) notSubjects.add(word);
  }
  const state = new Map();
  let open = false;
  let subject = "";
  let suppressed = null;
  let active = first;
  let lastQuery = "";
  let settleTimer = null;
  let edited = first;
  let editedAt = 0;
  let vocabulary = [];
  let specifier = null;
  let lastAwaiting = false;
  const curatedWords = new Set([...broadNouns, ...symptomPredicates, ...conversational,
    ...pronouns, ...determiners, ...contractions, ...(specifiers?.nouns || []),
    ..."broken damaged pain swelling numbness bleeding battery screen chain tire tube brake derailleur wheel handlebar seat pedal fork light leak leaking cracked frozen slow charging carpet".split(" ")]);
  for (const name of [...curatedWords]) for (const word of name.split(/\s+/)) curatedWords.add(word);
  const completionPunctuation = new Set(["?", ".", "!"]);

  function completesSubject(input, text) {
    return completionPunctuation.has(text) && input.selectionStart === input.selectionEnd &&
      input.selectionEnd === input.value.length;
  }

  function cancelAutomatic() {
    if (settleTimer !== null) clearTimeout(settleTimer);
    settleTimer = null;
  }

  function isAwaitingSpecifics() {
    return open && !suffix(second).trim();
  }

  function settleDelay() {
    const value = suffix(edited === second && open ? second : first).trim();
    if (!value) return 0;
    const token = value.split(/\s+/).at(-1).normalize("NFKD").replace(/\p{M}/gu, "")
      .toLowerCase().replace(/^[('"“‘]+|[)'"”’,;:.!?]+$/gu, "").replaceAll("’", "'");
    if (token.length < 2 || /\p{N}/u.test(token)) return 1000;
    if (curatedWords.has(token) || token.endsWith("s") && broadNouns.has(token.slice(0, -1))) return 380;
    let low = 0, high = vocabulary.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (vocabulary[middle] < token) low = middle + 1;
      else high = middle;
    }
    return vocabulary[low] === token ? 380 : 1000;
  }

  function publishSettle(immediate) {
    window.dispatchEvent(new CustomEvent("mend:settle", {
      detail: { query: query(), awaiting: isAwaitingSpecifics(), immediate: Boolean(immediate) },
    }));
  }

  function finishSettle(immediate = false) {
    cancelAutomatic();
    if (document.visibilityState === "hidden" || document.body.classList.contains("reading") ||
        [...state.values()].some(item => item.composing)) return;
    const value = suffix(first);
    if (edited === first && document.activeElement === first && value !== suppressed) {
      const name = bareNoun(value);
      if (name) {
        updateLine(name);
        focus(second, true);
      }
    }
    notify();
    publishSettle(immediate);
  }

  function scheduleSettle(input = edited, preserveTime = false) {
    cancelAutomatic();
    edited = input;
    if (!preserveTime) editedAt = performance.now();
    if (!query()) { publishSettle(true); return; }
    if (document.visibilityState === "hidden" || document.body.classList.contains("reading")) return;
    const remaining = Math.max(0, settleDelay() - (performance.now() - editedAt));
    if (!remaining) finishSettle();
    else settleTimer = setTimeout(() => finishSettle(), remaining);
  }

  function setVocabulary(terms) {
    vocabulary = Array.isArray(terms) ? terms : [];
    if (settleTimer !== null) scheduleSettle(edited, true);
  }

  function suffix(input) {
    return input.value.slice(state.get(input).prefix.length);
  }

  function query() {
    return [suffix(first).trim(), open ? suffix(second).trim() : ""].filter(Boolean).join(" ");
  }

  function syncTitle() {
    const title = first.value.trim();
    if (document.title !== title) document.title = title;
  }

  function notify() {
    const current = query();
    const awaiting = isAwaitingSpecifics();
    if (current === lastQuery && awaiting === lastAwaiting) return;
    lastQuery = current;
    lastAwaiting = awaiting;
    window.dispatchEvent(new Event("mend:querychange"));
  }

  function clampSelection(input) {
    const boundary = state.get(input).prefix.length;
    const start = Math.max(boundary, input.selectionStart);
    const end = Math.max(boundary, input.selectionEnd);
    if (start !== input.selectionStart || end !== input.selectionEnd) input.setSelectionRange(start, end, input.selectionDirection);
  }

  function focus(input = active, end = false) {
    if (input === second && !open) input = first;
    active = input;
    input.focus({ preventScroll: true });
    if (end) input.setSelectionRange(input.value.length, input.value.length);
    clampSelection(input);
  }

  function grow(input) {
    const underline = input === first ? firstUnderline : secondUnderline;
    if (input === first && fixedUnderline.textContent !== state.get(first).prefix) {
      fixedUnderline.textContent = state.get(first).prefix;
    }
    underline.textContent = suffix(input);
    underline.classList.toggle("is-empty", !underline.textContent);
    input.style.height = "0px";
    input.style.height = `${input.scrollHeight + 2}px`;
  }

  function fit() {
    if (!first.clientWidth) return;
    heading.style.fontSize = "";
    const size = parseFloat(getComputedStyle(heading).fontSize);
    measure.textContent = `${prompt}relationship`;
    let sampleWidth = measure.getBoundingClientRect().width;
    if (open) {
      measure.textContent = question.textContent;
      sampleWidth = Math.max(sampleWidth, measure.getBoundingClientRect().width);
    }
    if (sampleWidth > 0) heading.style.fontSize = `${Math.min(size, size * (first.clientWidth - 6) / sampleWidth)}px`;
    grow(first);
    grow(second);
    promptArt?.sync?.();
  }

  function setPrefix(input, prefix, editable = "") {
    const item = state.get(input);
    item.prefix = prefix;
    input.dataset.promptLength = String(prefix.length);
    input.value = prefix + editable;
    item.previous = input.value;
    clampSelection(input);
  }

  function bareNoun(value, explicit = false) {
    const pieces = value.trim().split(/\s+/);
    const labels = pieces.map(piece => piece.replace(/^[('"“‘]+|[)'"”’,;:.!?]+$/gu, "").replaceAll("’", "'"));
    const keys = labels.map(label => label.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase());
    const kept = [];
    let construction = true;
    for (let at = 0; at < pieces.length; at++) {
      const key = keys[at], next = keys[at + 1] || "";
      if (/\p{N}/u.test(key)) { kept.push(pieces[at]); construction = false; continue; }
      if (negativeContraction.test(key)) {
        kept.push("not"); construction = true;
      } else if (intensifiers.has(key) || contractions.has(key)) {
        construction = true;
      } else if (pronouns.has(key) && (conversational.has(next) || negativeContraction.test(next) ||
          kept.length && symptomPredicates.has(next))) {
        construction = true;
      } else if ((conversational.has(key) || construction && determiners.has(key)) && !/\p{N}/u.test(next)) {
        construction = true;
      } else {
        kept.push(/^[\p{L}\p{M}]+$/u.test(key) ? labels[at] : pieces[at]); construction = false;
      }
    }
    const name = kept.join(" ");
    const normalized = name.toLocaleLowerCase("en");
    if (!name || notSubjects.has(normalized)) return "";
    if (broadNouns.has(normalized) || specifiers?.resolve?.(normalized) ||
        normalized.endsWith("s") && broadNouns.has(normalized.slice(0, -1))) return name;
    return explicit && /^[\p{L}][\p{L}\p{M}'’\-]*$/u.test(name) ? name : "";
  }

  function updateLine(name) {
    const wasOpen = open;
    const changedSubject = name.toLocaleLowerCase("en") !== subject.toLocaleLowerCase("en");
    const symptom = changedSubject || !name ? "" : suffix(second);
    subject = name;
    open = Boolean(name);
    if (open) {
      if (changedSubject) setPrefix(second, "", symptom);
      question.textContent = `What's wrong with your ${name}?`;
      second.setAttribute("aria-label", question.textContent);
    } else {
      setPrefix(second, "");
      question.textContent = "";
      if (active === second || document.activeElement === second) focus(first, true);
    }
    if (open !== wasOpen || changedSubject) {
      specifier?.destroy?.();
      specifier = open && specifierContainer ? specifiers?.mount?.(specifierContainer, name, selectSpecific) : null;
      if (specifierContainer && !specifier) specifierContainer.hidden = true;
    }
    specifier?.sync?.(suffix(second));
    second.disabled = !open;
    second.tabIndex = open ? 0 : -1;
    line.inert = !open;
    line.setAttribute("aria-hidden", String(!open));
    line.classList.toggle("is-visible", open);
    first.setAttribute("aria-expanded", String(open));
    if (open !== wasOpen || changedSubject) window.dispatchEvent(new Event("mend:layout"));
  }

  function syncFirst() {
    const value = suffix(first);
    if (suppressed !== null && value !== suppressed) suppressed = null;
    const name = value === suppressed ? "" : bareNoun(value);
    if (open && name && name.toLocaleLowerCase("en") === subject.toLocaleLowerCase("en")) updateLine(name);
    else updateLine("");
  }

  function activate() {
    cancelAutomatic();
    const name = bareNoun(suffix(first), true);
    if (name) {
      suppressed = null;
      updateLine(name);
      focus(second, true);
    } else syncFirst();
    notify();
    publishSettle(true);
    return Boolean(name);
  }

  function selectSpecific(value) {
    if (!open || typeof value !== "string") return;
    // insertText preserves the browser's native undo buffer where supported.
    // It may synchronously emit input; finishSettle below cancels that timer.
    // The optional command is never called from an input handler.
    focus(second);
    second.setSelectionRange(0, second.value.length);
    try { document.execCommand?.("insertText", false, value); } catch { /* Native edit fallback below. */ }
    if (second.value !== value) second.setRangeText(value, 0, second.value.length, "end");
    state.get(second).previous = second.value;
    edited = second;
    grow(second);
    specifier?.sync?.(suffix(second));
    focus(second, true);
    notify();
    finishSettle(true);
  }

  function dissolve() {
    cancelAutomatic();
    suppressed = suffix(first);
    updateLine("");
    focus(first, true);
    edited = first;
    notify();
    scheduleSettle(first);
  }

  function changed(input, event) {
    const item = state.get(input);
    const completed = item.completion === input.value && event?.inputType === "insertText";
    item.completion = null;
    if (!input.value.startsWith(item.prefix)) {
      input.value = item.previous;
      input.setSelectionRange(item.prefix.length, item.prefix.length);
    }
    item.previous = input.value;
    if (input === first) syncTitle();
    grow(input);
    if (input === second) specifier?.sync?.(suffix(second));
    if (input === first && !suffix(first).trim()) updateLine("");
    if (item.composing) { cancelAutomatic(); notify(); return; }
    if (input === first && completed) { edited = first; activate(); return; }
    if (input === first) syncFirst();
    notify();
    scheduleSettle(input);
  }

  function inputEvent(input, inputType) {
    const event = typeof InputEvent === "function" ? new InputEvent("input", { bubbles: true, inputType }) : new Event("input", { bubbles: true });
    input.dispatchEvent(event);
  }

  for (const input of [first, second]) {
    const prefix = input === first ? prompt : "";
    state.set(input, { prefix, previous: prefix, composing: false });
    input.value = prefix;
    input.dataset.promptLength = String(prefix.length);
    input.addEventListener("focus", () => { active = input; clampSelection(input); });
    input.addEventListener("select", () => clampSelection(input));
    input.addEventListener("pointerup", () => clampSelection(input));
    input.addEventListener("keyup", () => clampSelection(input));
    input.addEventListener("compositionstart", () => { cancelAutomatic(); state.get(input).completion = null; state.get(input).composing = true; });
    input.addEventListener("compositionend", () => { state.get(input).composing = false; changed(input); });
    input.addEventListener("input", (event) => changed(input, event));
    input.addEventListener("keydown", (event) => {
      if (event.isComposing || event.keyCode === 229 || state.get(input).composing) return;
      if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === "a") {
        event.preventDefault();
        input.setSelectionRange(state.get(input).prefix.length, input.value.length);
      } else if (input === first && !event.ctrlKey && !event.metaKey && !event.altKey &&
          !event.shiftKey && (event.key === "Enter" || event.key === "Tab")) {
        if (activate() || event.key === "Enter") event.preventDefault();
      } else if (input === second && !event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey &&
          (event.key === "Enter" || event.key === "Tab")) {
        finishSettle(true);
        if (event.key === "Enter") event.preventDefault();
      } else if (input === second && event.key === "Backspace" && !suffix(second) && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        dissolve();
      } else if (input === second && event.key === "Tab" && event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        focus(first);
      }
    });
    input.addEventListener("beforeinput", (event) => {
      clampSelection(input);
      const item = state.get(input);
      const boundary = item.prefix.length;
      const start = input.selectionStart;
      const end = input.selectionEnd;
      // Let the native edit insert punctuation (and retain its undo history)
      // before moving focus. A shortcut must never swallow typed characters.
      item.completion = input === first && event.inputType === "insertText" && completesSubject(input, event.data) &&
        !event.ctrlKey && !event.metaKey && !event.altKey && !event.isComposing && !item.composing &&
        bareNoun(suffix(input), true) ? input.value + event.data : null;
      if (input === first && (event.inputType === "insertLineBreak" || event.inputType === "insertParagraph") &&
          !event.ctrlKey && !event.metaKey && !event.altKey && !event.isComposing && !item.composing) {
        activate();
        event.preventDefault();
      } else if (input === second && (event.inputType === "insertLineBreak" || event.inputType === "insertParagraph") &&
          !event.isComposing && !item.composing) {
        finishSettle(true);
        event.preventDefault();
      } else if (event.inputType.startsWith("delete") && event.inputType.endsWith("Backward") && end <= boundary) {
        event.preventDefault();
        if (input === second && !suffix(second) && !event.isComposing && !item.composing) dissolve();
      } else if ((event.inputType === "deleteSoftLineBackward" || event.inputType === "deleteHardLineBackward") && start === end) {
        event.preventDefault();
        const from = Math.max(boundary, input.value.lastIndexOf("\n", start - 1) + 1);
        input.setRangeText("", from, end, "start");
        inputEvent(input, event.inputType);
      }
    });
    for (const operation of ["copy", "cut"]) {
      input.addEventListener(operation, (event) => {
        clampSelection(input);
        const start = input.selectionStart, end = input.selectionEnd;
        if (!event.clipboardData || start === end) return;
        event.clipboardData.setData("text/plain", input.value.slice(start, end));
        event.preventDefault();
        if (operation === "cut") {
          input.setRangeText("", start, end, "start");
          inputEvent(input, "deleteByCut");
        }
      });
    }
    input.addEventListener("blur", () => {
      queueMicrotask(() => {
        if (document.hasFocus() && document.activeElement === document.body && !document.body.classList.contains("reading")) focus();
      });
    });
  }

  document.addEventListener("selectionchange", () => {
    if (state.has(document.activeElement)) clampSelection(document.activeElement);
  });
  document.addEventListener("pointerdown", (event) => {
    if (document.body.classList.contains("reading") || event.button !== 0 ||
        event.target.closest(".topic-specifier, [data-part], a, button, input, textarea, select, summary, [tabindex], [contenteditable]")) return;
    event.preventDefault();
    focus();
  }, { capture: true });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") cancelAutomatic();
    else if (document.activeElement === first || document.activeElement === second) scheduleSettle(document.activeElement);
  });
  new MutationObserver(() => {
    if (document.body.classList.contains("reading")) cancelAutomatic();
  }).observe(document.body, { attributes: true, attributeFilter: ["class"] });
  fixedUnderline.textContent = prompt;
  promptArt = styles?.mount?.(first.closest(".hero-field"), fixedUnderline, theme);
  measure.textContent = `${prompt}relationship`;
  window.mendHero = { query, focus: () => focus(), isAwaitingSpecifics, settleDelay, setVocabulary,
    settle: () => finishSettle(true) };
  window.addEventListener("resize", fit);
  window.addEventListener("mend:layout", fit);
  document.fonts?.ready.then(fit);
  document.fonts?.addEventListener("loadingdone", fit);
  syncTitle();
  fit();
  focus(first, true);
})();
