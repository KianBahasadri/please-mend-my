const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { makeDOM } = require('./hero_test_dom.cjs');
const settle = async () => { for (let pass = 0; pass < 24; pass++) await Promise.resolve(); };

function answerUI({ initialGuide = '', deferred = [] } = {}) {
  const dom = makeDOM();
  const ids = ['catalog-status', 'search-results', 'result-cards', 'result-count',
    'guide-panel', 'guide-body', 'guide-nav-error', 'close-guide'];
  const elements = Object.fromEntries(ids.map(id => [id, dom.add('div', id)]));
  for (const id of ['search-results', 'guide-panel', 'guide-nav-error']) elements[id].hidden = true;
  const origin = 'http://127.0.0.1:8080';
  const location = { href: origin + '/' + (initialGuide ? '?guide=' + initialGuide : ''), origin, pathname: '/', protocol: 'http:' };
  const posts = [], requests = [], history = [], controls = new Map();
  let query = '', awaiting = false, worker;
  for (const id of deferred) {
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    controls.set(id, { pending, release });
  }
  class Worker {
    constructor() { worker = this; }
    postMessage(message) { posts.push(structuredClone(message)); }
    emit(message) { this.onmessage({ data: message }); }
  }
  const navigate = (kind, state, href) => { history.push({ kind, state, href }); location.href = new URL(href, origin).href; };
  Object.assign(dom.window, { mendHero: { query: () => query, isAwaitingSpecifics: () => awaiting,
    focus: () => dom.response.focus(), settle() {} } });
  Object.assign(dom.context, { location, URL, Worker, Image: class {}, scrollY: 0, innerHeight: 800,
    history: { pushState(state, title, href) { navigate('push', state, href); },
      replaceState(state, title, href) { navigate('replace', state, href); } },
    fetch: async (url, options) => {
      assert.equal(options.mode, 'same-origin');
      const id = decodeURIComponent(url.slice('/api/guides/'.length));
      requests.push(id);
      const result = controls.has(id) ? await controls.get(id).pending : {};
      return { ok: !result.error, status: result.error ? 503 : 200, json: async () => ({
        id, title: 'Answer ' + id, html: '<p>Legacy ' + id + '</p>', images: [], ...result,
      }) };
    },
  });
  dom.response.focus();
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/search.js'), 'utf8'), vm.createContext(dom.context));
  const searches = () => posts.filter(item => item.type === 'search');
  return Object.assign(dom, { elements, location, history, requests, posts, searches,
    ready() { worker.emit({ type: 'ready', categories: [], source: {}, loadDuration: 1 }); dom.flushFrames(); },
    change(value, waiting = false) { query = value; dom.response.value = value; awaiting = waiting; dom.response.focus(); dom.window.dispatchEvent(new dom.context.Event('mend:querychange')); dom.flushFrames(); },
    result(ids, requestId = searches().at(-1).requestId) { worker.emit({ type: 'results', requestId, duration: 1, total: ids.length,
      items: ids.map(id => [id, 'Guide ' + id, 'technology', 'guide', '']) }); },
    release(id, detail = {}) { controls.get(id).release(detail); },
    title() { return elements['guide-body'].querySelector('.guide-title')?.textContent || ''; },
    click(id) {
      const link = dom.add('a', 'link-' + id); link.href = origin + '/?guide=' + id;
      dom.event(link, 'click', { bubbles: true });
    },
    pop(id) { location.href = origin + '/' + (id ? '?guide=' + id : ''); dom.window.dispatchEvent(new dom.context.Event('popstate')); dom.flushFrames(); },
  });
}

test('top answer renders inline without moving selection, prefetches 2–5 and offers 2–6', async () => {
  const ui = answerUI(); ui.ready(); ui.change('broken laptop');
  ui.response.setSelectionRange(3, 7, 'backward');
  ui.result(['one', 'two', 'three', 'four', 'five', 'six']); await settle();
  assert.equal(ui.title(), 'Answer one');
  assert.equal(ui.elements['guide-panel'].hidden, false);
  assert.equal(ui.document.activeElement, ui.response);
  assert.deepEqual([ui.response.selectionStart, ui.response.selectionEnd, ui.response.selectionDirection], [3, 7, 'backward']);
  assert.deepEqual(ui.requests, ['one', 'two', 'three', 'four', 'five']);
  assert.deepEqual(ui.elements['result-cards'].children.map(link => link.dataset.guideId), ['two', 'three', 'four', 'five', 'six']);
  assert.equal(ui.elements['result-count'].textContent, 'Other possibilities');
  assert.equal(ui.history.length, 0, 'typing does not push a history entry per answer');
  assert.equal(ui.document.body.classList.contains('reading'), false);
  assert.equal(ui.posts.some(message => message.type === 'range'), false);
  assert.equal(ui.searches()[0].count, 6);
});

test('retain the prior answer until the newest #1 payload resolves and reject older payloads', async () => {
  const ui = answerUI({ deferred: ['second', 'third'] }); ui.ready(); ui.change('old phrase');
  ui.result(['first']); await settle();
  ui.change('second phrase'); ui.result(['second']); await settle();
  assert.equal(ui.title(), 'Answer first');
  ui.change('third phrase'); ui.result(['third']);
  ui.release('second'); await settle();
  assert.equal(ui.title(), 'Answer first', 'an old payload cannot commit during a newer query');
  ui.release('third'); await settle();
  assert.equal(ui.title(), 'Answer third');
  assert.equal(ui.document.activeElement, ui.response);
});

test('clearing the overall query hides immediately and pending payloads cannot restore it', async () => {
  const ui = answerUI({ deferred: ['late'] }); ui.ready(); ui.change('initial phrase');
  ui.result(['first']); await settle();
  ui.change('later phrase'); ui.result(['late']);
  ui.change('');
  assert.equal(ui.elements['search-results'].hidden, true);
  assert.equal(ui.elements['guide-panel'].hidden, true);
  assert.equal(ui.title(), '');
  ui.release('late'); await settle();
  assert.equal(ui.elements['guide-panel'].hidden, true);
  assert.equal(ui.title(), '');
});

test('blank specifics search the noun immediately and retain its prior answer until noun payload is ready', async () => {
  const ui = answerUI({ deferred: ['noun', 'old-symptom'] }); ui.ready();
  ui.change('bike chain'); ui.result(['chain']); await settle();
  ui.change('bike broken'); ui.result(['old-symptom']);
  ui.change('bike', true);
  assert.equal(ui.searches().at(-1).query, 'bike');
  assert.equal(ui.title(), 'Answer chain', 'clearing only the second line keeps the current answer');
  ui.result(['noun']);
  ui.release('old-symptom'); await settle();
  assert.equal(ui.title(), 'Answer chain', 'stale symptom content cannot replace the noun search');
  ui.release('noun'); await settle();
  assert.equal(ui.title(), 'Answer noun');
  assert.equal(ui.elements['guide-panel'].hidden, false);
  assert.equal(ui.document.activeElement, ui.response);
});


test('runner-up switches synchronously when prefetched and direct guide history remains usable', async () => {
  const ui = answerUI(); ui.ready(); ui.change('broken laptop'); ui.result(['one', 'two', 'three']); await settle();
  ui.click('two');
  assert.equal(ui.title(), 'Answer two');
  assert.equal(ui.requests.filter(id => id === 'two').length, 1);
  assert.match(ui.location.href, /\?guide=two$/);
  assert.equal(ui.history.at(-1).state.guide, 'two');
  ui.pop('one');
  assert.equal(ui.title(), 'Answer one');
  ui.change('new phrase');
  assert.equal(ui.location.href, ui.location.origin + '/');
  assert.equal(ui.searches().at(-1).query, 'new phrase', 'typing after navigation resumes local search');
  assert.equal(ui.document.activeElement, ui.response);
});

test('direct links survive index readiness with empty hero and no automatic search overwrite', async () => {
  const ui = answerUI({ initialGuide: 'deep' }); await settle();
  assert.equal(ui.title(), 'Answer deep');
  ui.ready(); await settle();
  assert.equal(ui.title(), 'Answer deep');
  assert.equal(ui.elements['search-results'].hidden, false);
  assert.equal(ui.elements['guide-panel'].hidden, false);
  assert.equal(ui.searches().length, 0);
  assert.equal(ui.history.length, 0);
});

test('failed automatic replacement preserves its prior answer without busy UI or status text', async () => {
  const ui = answerUI({ deferred: ['bad'] }); ui.ready(); ui.change('first phrase'); ui.result(['good']); await settle();
  ui.change('next phrase'); ui.result(['bad']); ui.release('bad', { error: true }); await settle();
  assert.equal(ui.title(), 'Answer good');
  assert.equal(ui.elements['catalog-status'].textContent, '');
  assert.equal(ui.elements['guide-nav-error'].hidden, true);
  assert.equal(ui.elements['guide-body'].hasAttribute('aria-busy'), false);
});
