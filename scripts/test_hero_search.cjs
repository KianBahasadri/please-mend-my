const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { makeDOM } = require('./hero_test_dom.cjs');

// Execute the actual hero, specifier registry and search controller together.
// Synthetic DOM events/clock cover coordination, not CSS/native font paint.
async function searchUI({ heroAvailable = true, initialQuery = '', terms = [] } = {}) {
  const dom = makeDOM();
  const ids = ['catalog-status', 'search-results', 'result-cards', 'result-count',
    'guide-panel', 'guide-body', 'guide-nav-error', 'close-guide'];
  const elements = Object.fromEntries(ids.map(id => [id, dom.add(id === 'close-guide' ? 'button' : 'div', id)]));
  elements['search-results'].hidden = true;
  elements['guide-panel'].hidden = true;
  elements['guide-nav-error'].hidden = true;
  elements.response = dom.response;
  elements['clarification-response'] = dom.clarification;
  const posted = [], requests = [], workers = [], settled = [];
  class Worker {
    constructor(url) { assert.equal(url, '/assets/search-worker.js'); workers.push(this); }
    postMessage(message) { posted.push(structuredClone(message)); }
    emit(message) { this.onmessage({ data: message }); }
  }
  const location = { href: 'http://127.0.0.1:8080/', origin: 'http://127.0.0.1:8080', pathname: '/', protocol: 'http:' };
  Object.assign(dom.context, { location, URL, Worker, scrollY: 0, innerHeight: 800,
    history: { pushState(state, title, href) { location.href = new URL(href, location.href).href; } },
    fetch: async (url, options) => {
      requests.push(url);
      assert.equal(url, '/api/guides/example');
      assert.equal(options.mode, 'same-origin');
      return { ok: true, json: async () => ({ id: 'example', title: 'Example guide', html: '<p>Guide content</p>', images: [] }) };
    },
  });
  const context = vm.createContext(dom.context);
  const run = file => vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/' + file), 'utf8'), context);
  run('bicycle-art.js');
  run('specifiers.js');
  if (heroAvailable) run('hero.js');
  else { dom.response.value = 'please, mend my '; dom.response.dataset.promptLength = '15'; }
  run('search.js');
  dom.window.addEventListener('mend:settle', event => settled.push(structuredClone(event.detail)));
  await Promise.resolve();
  dom.flushFrames();
  const type = (input, value, inputType = 'insertText') => {
    input.focus();
    input.value = input.value.slice(0, Number(input.dataset.promptLength || 0)) + value;
    dom.event(input, 'input', { inputType, bubbles: true });
  };
  const ui = Object.assign(dom, { elements, posted, requests, workers, settled,
    ready(words = terms) { workers[0].emit({ type: 'ready', categories: [], source: {}, terms: words, loadDuration: 1 }); dom.flushFrames(); },
    change(value) { type(dom.response, value); },
    answer(value) { type(dom.clarification, value); },
    key(input, key, options = {}) { return dom.event(input, 'keydown', { key, bubbles: true, ...options }); },
    searches() { return posted.filter(message => message.type === 'search'); },
    option(id) { return dom.specifierContainer.querySelector(`button[data-part="${id}"]`); },
    part(id) { return dom.specifierContainer.querySelector(`g[data-part="${id}"]`); },
    clickPart(id, svg = false) {
      const target = svg ? ui.part(id) : ui.option(id);
      assert.ok(target, id);
      const down = dom.event(target, 'pointerdown', { bubbles: true });
      assert.equal(down.defaultPrevented, false, 'specifier pointerdown must reach its real target');
      dom.event(target, 'click', { bubbles: true });
      dom.flushFrames();
    },
    results(requestId, rows = []) { workers[0].emit({ type: 'results', requestId, total: rows.length, items: rows, duration: 1 }); },
    clickGuide() {
      const link = dom.add('a', 'example-link');
      link.href = 'http://127.0.0.1:8080/?guide=example';
      dom.event(link, 'click', { bubbles: true });
    },
  });
  if (initialQuery) ui.change(initialQuery);
  return ui;
}

test('bike results start immediately while its question keeps the 380ms pause and full-width prompt', async () => {
  const ui = await searchUI(); ui.ready();
  const width = ui.response.clientWidth;
  ui.change('bike'); ui.flushFrames();
  assert.equal(ui.searches().at(-1).query, 'bike');
  assert.equal(ui.question.textContent, '');
  assert.equal(ui.elements['search-results'].hidden, false);
  const stale = ui.searches().at(-1).requestId;
  ui.advance(379);
  assert.equal(ui.question.textContent, '');
  assert.equal(ui.elements['search-results'].hidden, false);
  assert.equal(ui.searches().length, 1);
  ui.advance(1);
  assert.equal(ui.question.textContent, "What's wrong with your bike?");
  assert.equal(ui.specifierContainer.hidden, false);
  assert.equal(ui.window.mendHero.isAwaitingSpecifics(), true);
  assert.equal(ui.document.activeElement, ui.clarification);
  assert.equal(ui.elements['search-results'].hidden, true);
  assert.equal(ui.searches().length, 1, 'opening an unanswered question cancels results without another query');
  ui.results(stale, [['example', 'Bicycle repair', 'technology', 'guide', 'Bicycle']]);
  assert.equal(ui.elements['search-results'].hidden, true, 'a pre-question response cannot reopen blank specifics');
  assert.equal(ui.elements['result-cards'].children.length, 0);
  assert.equal(ui.response.clientWidth, width, 'underneath specifier does not change first-line width');
  assert.equal(ui.workspace.parentNode.contains(ui.question), true);
  assert.equal(ui.workspace.contains(ui.question), false, 'question stays outside the split workspace');
});

test('SVG and list selections write the native answer and reveal cards immediately; clear hides again', async () => {
  const ui = await searchUI(); ui.ready(); ui.change('bike'); ui.advance(380);
  ui.clickPart('chain', true);
  assert.equal(ui.clarification.value, 'chain');
  assert.equal(ui.clarification.selectionStart, 5);
  assert.equal(ui.document.activeElement, ui.clarification);
  assert.equal(ui.elements['search-results'].hidden, false);
  assert.equal(ui.searches().at(-1).query, 'bike chain');
  assert.equal(ui.option('chain').classList.contains('is-selected'), true);
  assert.equal(ui.part('chain').classList.contains('is-selected'), true);
  const count = ui.searches().length;
  ui.clickPart('chain');
  assert.equal(ui.clarification.value, '');
  assert.equal(ui.window.mendHero.isAwaitingSpecifics(), true);
  assert.equal(ui.elements['search-results'].hidden, true);
  assert.equal(ui.searches().length, count);
  assert.equal(ui.option('chain').classList.contains('is-selected'), false);
  ui.clickPart('pedals');
  assert.equal(ui.clarification.value, 'pedal');
  assert.equal(ui.searches().at(-1).query, 'bike pedal');
  const stale = ui.searches().at(-1).requestId;
  ui.answer('');
  assert.equal(ui.elements['search-results'].hidden, true);
  ui.results(stale);
  assert.equal(ui.elements['search-results'].hidden, true, 'a stale response cannot reopen cleared specifics');
  assert.equal(ui.option('pedals').classList.contains('is-selected'), false);
});

test('two-way hover/focus and manual answer matches synchronize exact SVG/list parts live', async () => {
  const ui = await searchUI(); ui.ready(); ui.change('road bike'); ui.key(ui.response, 'Enter');
  const group = ui.part('chain'), option = ui.option('chain');
  ui.event(group, 'pointerenter');
  assert.equal(option.classList.contains('is-highlighted'), true);
  assert.equal(option.scrolledIntoView.block, 'nearest');
  ui.event(group, 'pointerleave');
  const brakes = ui.option('brakes'); brakes.focus();
  assert.equal(ui.part('brakes').classList.contains('is-highlighted'), true);
  ui.answer('master link broken');
  assert.equal(group.classList.contains('is-selected'), true);
  assert.equal(option.classList.contains('is-selected'), true);
  ui.flushFrames();
  assert.equal(ui.elements['search-results'].hidden, false, 'typing an answer reveals results without a settle delay');
  assert.equal(ui.searches().at(-1).query, 'road bike master link broken');
  ui.advance(1000);
  assert.equal(ui.searches().length, 1, 'the later clarification settle does not repeat the search');
});

test('real words, unknown tokens, models and one-letter prefixes all search without a timer', async () => {
  for (const [query, delay] of [['broken laptop', 380], ['broken carpet', 380], ['broken flubbergorp', 1000],
    ['headphones WH-1000XM4', 1000], ['broken z', 1000], ['broken zygomatic', 380]]) {
    const ui = await searchUI(); ui.ready(['zygomatic']); ui.change(query);
    assert.equal(ui.window.mendHero.settleDelay(), delay, query);
    ui.flushFrames();
    assert.equal(ui.searches().at(-1).query, query);
    ui.advance(delay);
    assert.equal(ui.searches().length, 1, 'clarification timing never delays or repeats a phrase search');
    assert.equal(ui.requests.length, 0, 'dictionary lookup and typed search need no API request');
  }
});

test('each successive edit searches in its next frame and rapid edits coalesce to the latest query', async () => {
  const ui = await searchUI(); ui.ready();
  for (const text of ['b', 'br', 'broken', 'broken l', 'broken laptop', 'broken WH-1000XM4']) {
    const prior = ui.searches().length;
    ui.change(text); ui.flushFrames();
    assert.equal(ui.searches().length, prior + 1);
    assert.equal(ui.searches().at(-1).query, text);
  }
  const prior = ui.searches().length;
  ui.change('broken phone'); ui.change('broken phonex'); ui.change('broken phonexy');
  assert.equal(ui.searches().length, prior, 'the rendering frame still coalesces same-frame edits');
  ui.flushFrames();
  assert.equal(ui.searches().length, prior + 1);
  assert.equal(ui.searches().at(-1).query, 'broken phonexy');
  ui.advance(2000);
  assert.equal(ui.searches().length, prior + 1);
  assert.equal(ui.requests.length, 0);
});

test('car to carpet and bike to bikes continue typing without a premature clarification', async () => {
  const ui = await searchUI(); ui.ready();
  ui.change('car'); ui.advance(200); ui.change('carpet'); ui.advance(379);
  assert.equal(ui.question.textContent, '');
  ui.advance(1); assert.equal(ui.question.textContent, '');
  assert.equal(ui.searches().at(-1).query, 'carpet');
  ui.change('bike'); ui.advance(200); ui.change('bikes'); ui.advance(379);
  assert.equal(ui.question.textContent, '');
  ui.advance(1);
  assert.equal(ui.question.textContent, "What's wrong with your bikes?");
  assert.equal(ui.elements['search-results'].hidden, true);
});

test('startup searches the latest edit immediately without waiting for clarification timing', async () => {
  const ui = await searchUI(); ui.change('broken zygomatic'); ui.advance(100);
  ui.ready(['zygomatic']);
  assert.equal(ui.searches().at(-1).query, 'broken zygomatic');
  ui.advance(1000); assert.equal(ui.searches().length, 1);
  const pending = await searchUI(); pending.change('broken laptop'); pending.change('broken phone');
  pending.flushFrames();
  assert.equal(pending.searches().length, 0);
  pending.ready();
  assert.equal(pending.searches().at(-1).query, 'broken phone', 'index readiness immediately searches the current text');
  const noun = await searchUI(); noun.change('bike'); noun.advance(100); noun.ready();
  assert.equal(noun.searches().at(-1).query, 'bike');
  noun.advance(279); assert.equal(noun.question.textContent, '');
  noun.advance(1); assert.equal(noun.question.textContent, "What's wrong with your bike?");
  assert.equal(noun.elements['search-results'].hidden, true);
});

test('catalog recovery clears the stale error and preserves the current hero state', async () => {
  for (const text of ['', 'bike', 'broken laptop']) {
    const ui = await searchUI();
    if (text) { ui.change(text); ui.advance(380); }
    const value = ui.response.value;
    const focused = ui.document.activeElement;
    ui.workers[0].emit({ type: 'error', message: 'The repair library is unavailable (503).' });
    assert.equal(ui.elements['catalog-status'].hidden, false);
    assert.match(ui.elements['catalog-status'].textContent, /503/);
    ui.ready();
    assert.equal(ui.elements['catalog-status'].hidden, true, text);
    assert.equal(ui.elements['catalog-status'].textContent, '', text);
    assert.equal(ui.elements['result-count'].textContent, '', 'failed startup notice must disappear too');
    assert.equal(ui.response.value, value, 'recovery preserves the native field');
    assert.equal(ui.document.activeElement, focused, 'recovery preserves focus');
    if (text === 'broken laptop') {
      assert.equal(ui.searches().at(-1).query, text, 'the settled query resumes when the catalog recovers');
    } else {
      assert.equal(ui.searches().length, 0, 'empty input and unanswered specifics stay blank');
      assert.equal(ui.elements['search-results'].hidden, true);
    }
  }
});

test('full-phrase Enter/Tab and noun punctuation settle immediately without rewriting native text', async () => {
  for (const key of ['Enter', 'Tab']) {
    const ui = await searchUI(); ui.ready(); ui.change('broken laptop');
    ui.key(ui.response, key); ui.flushFrames();
    assert.equal(ui.searches().at(-1).query, 'broken laptop');
    assert.equal(ui.question.textContent, '');
  }
  const ui = await searchUI(); ui.ready(); ui.change('bike');
  const before = ui.event(ui.response, 'beforeinput', { inputType: 'insertText', data: '?', bubbles: true });
  assert.equal(before.defaultPrevented, false);
  ui.response.setRangeText('?', ui.response.value.length, ui.response.value.length, 'end');
  ui.event(ui.response, 'input', { inputType: 'insertText', data: '?', bubbles: true });
  assert.equal(ui.response.value, 'please, mend my bike?');
  assert.equal(ui.question.textContent, "What's wrong with your bike?");
  assert.equal(ui.elements['search-results'].hidden, true);
  ui.answer('chain'); ui.key(ui.clarification, 'Enter'); ui.flushFrames();
  assert.equal(ui.searches().at(-1).query, 'bike? chain');
});

test('empty answer Backspace dissolves and immediately searches the suppressed noun without reopening', async () => {
  const ui = await searchUI(); ui.ready(); ui.change('bike'); ui.advance(380);
  ui.key(ui.clarification, 'Backspace');
  assert.equal(ui.question.textContent, '');
  assert.equal(ui.specifierContainer.hidden, true);
  assert.equal(ui.document.activeElement, ui.response);
  ui.flushFrames(); assert.equal(ui.searches().at(-1).query, 'bike');
  ui.advance(1000); assert.equal(ui.searches().length, 1);
  assert.equal(ui.question.textContent, '');
  ui.change('');
  assert.equal(ui.elements['search-results'].hidden, true);
  assert.equal(ui.elements['result-cards'].children.length, 0);
  ui.advance(2000); assert.equal(ui.searches().length, 1);
});

test('pending edits retain displayed results but reject old replies and never steal focused cards', async () => {
  const ui = await searchUI(); ui.ready(); ui.change('broken laptop'); ui.advance(380);
  const previous = ui.searches().at(-1).requestId;
  assert.equal(ui.elements['search-results'].hidden, false);
  ui.change('broken screen');
  assert.equal(ui.elements['search-results'].hidden, false);
  ui.results(previous, [['old', 'Old result', 'technology', 'guide', 'Laptop']]);
  assert.equal(ui.elements['result-cards'].children.length, 0, 'old request must not render into a new query');
  const card = ui.add('a', 'focused-card'); card.focus();
  ui.advance(380);
  assert.equal(ui.document.activeElement, card);
  assert.equal(ui.searches().at(-1).query, 'broken screen');
  ui.change('bike'); card.focus(); ui.advance(380);
  assert.equal(ui.question.textContent, '', 'a focused card must not be hijacked by a delayed noun prompt');
  assert.equal(ui.searches().at(-1).query, 'bike');
});

test('composition searches only committed text immediately and hidden/reading views cancel pending work', async () => {
  const ui = await searchUI(); ui.ready();
  ui.event(ui.response, 'compositionstart'); ui.change('broken laptop'); ui.advance(2000);
  assert.equal(ui.searches().length, 0);
  ui.event(ui.response, 'compositionend'); ui.flushFrames();
  assert.equal(ui.searches().at(-1).query, 'broken laptop');
  ui.advance(1000); assert.equal(ui.searches().length, 1);
  ui.change('bike'); ui.document.visibilityState = 'hidden'; ui.event(ui.document, 'visibilitychange');
  ui.advance(1000); assert.equal(ui.searches().length, 1);
  ui.document.visibilityState = 'visible'; ui.event(ui.document, 'visibilitychange');
  ui.document.body.classList.add('reading'); await Promise.resolve(); ui.advance(1000);
  assert.equal(ui.question.textContent, '');
  assert.equal(ui.searches().length, 1);
});

test('index readiness during composition waits for same-value commit in either field', async () => {
  const first = await searchUI();
  first.event(first.response, 'compositionstart'); first.change('broken laptop'); first.ready();
  first.flushFrames();
  assert.equal(first.searches().length, 0);
  first.event(first.response, 'compositionend'); first.flushFrames();
  assert.equal(first.searches().at(-1).query, 'broken laptop');
  assert.equal(first.searches().length, 1);
  const answer = await searchUI(); answer.change('bike'); answer.key(answer.response, 'Enter');
  answer.event(answer.clarification, 'compositionstart'); answer.answer('chain'); answer.ready();
  assert.equal(answer.searches().length, 0);
  answer.event(answer.clarification, 'compositionend'); answer.flushFrames();
  assert.equal(answer.searches().at(-1).query, 'bike chain');
  assert.equal(answer.searches().length, 1);
});

test('return from an open guide searches and restores the native answer caret immediately', async () => {
  const ui = await searchUI(); ui.ready(); ui.change('bike'); ui.advance(380); ui.clickPart('chain');
  ui.clickGuide(); for (let pass = 0; pass < 12; pass++) await Promise.resolve();
  assert.equal(ui.document.body.classList.contains('reading'), true);
  assert.equal(ui.elements['guide-panel'].hidden, false);
  const prior = ui.searches().length;
  ui.event(ui.elements['close-guide'], 'click'); ui.flushFrames();
  assert.equal(ui.document.body.classList.contains('reading'), false);
  assert.equal(ui.document.activeElement, ui.clarification);
  assert.equal(ui.searches().length, prior + 1);
  assert.equal(ui.searches().at(-1).query, 'bike chain');
  assert.deepEqual(ui.requests, ['/api/guides/example']);
});

test('list-only nouns narrow cleanly and unsupported nouns never get empty SVG boxes', async () => {
  const ui = await searchUI(); ui.ready(); ui.change('laptop'); ui.advance(380);
  assert.equal(ui.specifierContainer.classList.contains('is-list-only'), true);
  assert.equal(ui.specifierContainer.querySelector('svg'), null);
  ui.clickPart('battery'); assert.equal(ui.searches().at(-1).query, 'laptop battery');
  ui.change('relationship'); ui.advance(380);
  assert.equal(ui.specifierContainer.hidden, true);
  assert.equal(ui.question.textContent, "What's wrong with your relationship?");
  assert.equal(ui.elements['search-results'].hidden, true);
});

test('a cached older page without the hero controller searches the editable suffix immediately', async () => {
  const ui = await searchUI({ heroAvailable: false }); ui.ready();
  ui.change('broken arm'); ui.flushFrames();
  assert.equal(ui.searches().at(-1).query, 'broken arm');
  ui.advance(2000); assert.equal(ui.searches().length, 1);
  assert.equal(ui.requests.length, 0);
});


test('a stale cached worker cannot create topic or unknown-kind cards in the UI', async () => {
  const ui = await searchUI(); ui.ready(); ui.change('broken laptop'); ui.advance(380);
  ui.results(ui.searches().at(-1).requestId, [
    ['topic', 'Laptop topic', 'technology', 'topic', 'Laptop'],
    ['example', 'Laptop repair', 'technology', 'guide', 'Laptop'],
    ['unknown', 'Unknown kind', 'technology', 'device', 'Laptop'],
    ['example', 'Laptop article', 'technology', 'article', 'Laptop'],
  ]);
  const cards = ui.elements['result-cards'].querySelectorAll('.repair-card');
  assert.equal(cards.length, 2);
  assert.deepEqual(cards.map(card => card.dataset.resultIndex), [1, 3], 'original worker positions stay intact');
  assert.ok(cards.every(card => card.dataset.guideId === 'example'));
  for (let pass = 0; pass < 12; pass++) await Promise.resolve();
  assert.deepEqual(ui.requests, ['/api/guides/example'], 'only actionable visible cards are prefetched');
});


test('part selection uses native insertText undo when available and safely falls back on failure', async () => {
  for (const mode of ['native', 'false-after-edit', 'false', 'throws', 'unsupported']) {
    const ui = await searchUI(); ui.ready(); ui.change('bike'); ui.advance(380);
    let commands = 0, fallbackEdits = 0;
    const undo = [];
    const nativeRangeText = ui.clarification.setRangeText.bind(ui.clarification);
    ui.clarification.setRangeText = (...args) => { fallbackEdits++; return nativeRangeText(...args); };
    if (mode !== 'unsupported') ui.document.execCommand = (command, showUI, text) => {
      commands++;
      assert.equal(command, 'insertText'); assert.equal(showUI, false);
      assert.equal(ui.document.activeElement, ui.clarification);
      assert.equal(ui.clarification.selectionStart, 0);
      assert.equal(ui.clarification.selectionEnd, ui.clarification.value.length);
      if (mode === 'throws') throw new Error('Unsupported editing command');
      if (mode === 'false') return false;
      undo.push(ui.clarification.value);
      // Model one successful browser-native edit and its synchronous input.
      ui.clarification.value = text;
      ui.event(ui.clarification, 'input', { inputType: 'insertText', data: text, bubbles: true });
      return mode === 'native';
    };
    ui.clickPart('chain');
    assert.equal(ui.clarification.value, 'chain', mode);
    assert.equal(ui.clarification.selectionStart, 5);
    assert.equal(ui.clarification.selectionEnd, 5);
    assert.equal(ui.searches().length, 1, 'native input plus callback publishes one immediate search');
    assert.equal(ui.searches()[0].query, 'bike chain');
    assert.equal(commands, mode === 'unsupported' ? 0 : 1);
    assert.equal(fallbackEdits, ['native', 'false-after-edit'].includes(mode) ? 0 : 1, mode);
    if (undo.length) {
      ui.clarification.value = undo.pop();
      ui.event(ui.clarification, 'input', { inputType: 'historyUndo', bubbles: true });
      assert.equal(ui.clarification.value, '');
      assert.equal(ui.window.mendHero.query(), 'bike');
      assert.equal(ui.elements['search-results'].hidden, true);
      assert.equal(ui.part('chain').classList.contains('is-selected'), false);
    }
  }
});
