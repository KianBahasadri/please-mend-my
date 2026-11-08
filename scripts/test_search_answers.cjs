const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { makeDOM } = require('./hero_test_dom.cjs');
const settle = async () => { for (let pass = 0; pass < 24; pass++) await Promise.resolve(); };

function answerUI({ initialGuide = '', deferred = [], abortable = false, cache } = {}) {
  const dom = makeDOM();
  const ids = ['catalog-status', 'search-results', 'result-cards', 'result-count',
    'guide-panel', 'guide-body', 'guide-nav-error'];
  const elements = Object.fromEntries(ids.map(id => [id, dom.add('div', id)]));
  for (const id of ['search-results', 'guide-panel', 'guide-nav-error']) elements[id].hidden = true;
  const origin = 'http://127.0.0.1:8080';
  const location = { href: origin + '/' + (initialGuide ? '?guide=' + initialGuide : ''), origin, pathname: '/', protocol: 'http:' };
  const posts = [], requests = [], history = [], controls = new Map(), aborted = [], workers = [];
  let query = '', awaiting = false, worker;
  for (const id of deferred) {
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    controls.set(id, { pending, release });
  }
  class Worker {
    constructor(url) { worker = this; this.url = url; this.posts = []; workers.push(this); }
    postMessage(message) { const copy = structuredClone(message); posts.push(copy); this.posts.push(copy); }
    emit(message) { this.onmessage({ data: message }); }
    terminate() { this.terminated = true; }
  }
  const navigate = (kind, state, href) => { history.push({ kind, state, href }); location.href = new URL(href, origin).href; };
  Object.assign(dom.window, { mendHero: { query: () => query, isAwaitingSpecifics: () => awaiting,
    focus: () => dom.response.focus(), settle() {} }, ...(cache ? { mendClientCache: cache } : {}) });
  Object.assign(dom.context, { location, URL, Worker, Image: class {}, scrollY: 0, innerHeight: 800,
    ...(abortable ? { AbortController } : {}),
    history: { pushState(state, title, href) { navigate('push', state, href); },
      replaceState(state, title, href) { navigate('replace', state, href); } },
    fetch: async (url, options) => {
      assert.equal(options.mode, 'same-origin');
      const id = decodeURIComponent(url.slice('/api/guides/'.length));
      requests.push(id);
      const result = controls.has(id) ? await Promise.race([controls.get(id).pending,
        ...(options.signal ? [new Promise((resolve, reject) => options.signal.addEventListener('abort', () => {
          aborted.push(id); reject(new Error('Aborted obsolete prefetch'));
        }, { once: true }))] : []),
      ]) : {};
      return { ok: !result.error, status: result.error ? 503 : 200, json: async () => ({
        id, title: 'Answer ' + id, html: '<p>Legacy ' + id + '</p>', images: [], ...result,
      }) };
    },
  });
  dom.response.focus();
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/search.js'), 'utf8'), vm.createContext(dom.context));
  const searches = () => posts.filter(item => item.type === 'search');
  return Object.assign(dom, { elements, location, history, requests, posts, searches, aborted, workers,
    ready(extra = {}) { workers[0].emit({ type: 'ready', categories: [], source: {}, loadDuration: 1, ...extra }); dom.flushFrames(); },
    change(value, waiting = false) { query = value; dom.response.value = value; awaiting = waiting; dom.response.focus(); dom.window.dispatchEvent(new dom.context.Event('mend:querychange')); dom.flushFrames(); },
    result(ids, requestId = searches().at(-1).requestId, inheritance = []) { worker.emit({ type: 'results', requestId, duration: 1, total: ids.length,
      items: ids.map(id => [id, 'Guide ' + id, 'technology', 'guide', '']), inheritance }); },
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

test('inherited answers identify their general scope without rewriting the source title or taking focus', async () => {
  const ui = answerUI(); ui.ready(); ui.change('Dell Inspiron 1500 screen');
  ui.result(['general', 'exact'], undefined, [{ id: 'general', label: 'Laptop', distance: 1 }]); await settle();
  assert.equal(ui.title(), 'Answer general');
  assert.equal(ui.elements['guide-body'].querySelector('.guide-scope').textContent, 'General guidance for Laptop');
  assert.equal(ui.document.activeElement, ui.response);
  ui.click('exact');
  assert.equal(ui.elements['guide-body'].querySelector('.guide-scope'), null);
});

test('a newer top answer cancels obsolete active prefetches instead of waiting for every old slot', async () => {
  const old = ['two', 'three', 'four', 'five'];
  const ui = answerUI({ deferred: old, abortable: true }); ui.ready(); ui.change('first query');
  ui.result(['one', ...old]); await settle();
  assert.equal(ui.title(), 'Answer one');
  assert.ok(old.every(id => ui.requests.includes(id)), 'four old prefetches occupy every slot');
  ui.change('next query'); ui.result(['new']); await settle();
  assert.deepEqual(ui.aborted.sort(), [...old].sort());
  assert.ok(ui.requests.includes('new'), 'new #1 starts without resolving any old response');
  assert.equal(ui.title(), 'Answer new');
  assert.equal(ui.document.activeElement, ui.response);
});

test('a prepared replacement waits through typing and IME, then replays the latest query without blanking its answer', async () => {
  const accepted = [], cache = { async acceptCatalog(value) { accepted.push(value); return true; } };
  const ui = answerUI({ cache }); ui.ready({ catalogETag: 'old', cacheSource: 'cache', cacheTimings: { readMs: 2, parseMs: 4 }, prepareDuration: 9 });
  ui.change('old query'); ui.result(['one']); await settle();
  ui.workers[0].emit({ type: 'catalog-update', etag: 'new', candidateKey: 'new-key' });
  assert.equal(ui.workers.length, 2);
  const replacement = ui.workers[1];
  assert.match(replacement.url, /candidateKey=new-key/);
  ui.change('latest query');
  assert.equal(ui.workers[0].posts.at(-1).query, 'latest query', 'active searches continue in the old worker during replacement preparation');
  replacement.emit({ type: 'ready', catalogETag: 'new', candidateKey: 'new-key', categories: [], source: {}, terms: [], loadDuration: 10 });
  ui.event(ui.response, 'compositionstart'); ui.advance(1000); await settle();
  assert.equal(accepted.length, 0, 'composition blocks the swap');
  assert.equal(ui.workers[0].terminated, undefined);
  ui.event(ui.response, 'compositionend'); ui.advance(379); await settle();
  assert.equal(accepted.length, 0);
  ui.advance(1); await settle(); ui.flushFrames();
  assert.deepEqual(JSON.parse(JSON.stringify(accepted)), [{ etag: 'new', candidateKey: 'new-key' }]);
  assert.equal(ui.workers[0].terminated, true);
  assert.equal(replacement.posts.at(-1).query, 'latest query');
  assert.equal(ui.title(), 'Answer one', 'the current answer remains until new result payload is ready');
  ui.result(['one']); await settle();
  assert.equal(ui.requests.filter(id => id === 'one').length, 2, 'guide memory cache is invalidated at a catalog generation change');
  assert.equal(ui.document.activeElement, ui.response);
});

test('an invalid or unavailable replacement keeps the old worker and visible answer active', async () => {
  for (const mode of ['schema', 'missing-cache']) {
    const ui = answerUI({ cache: { async acceptCatalog() { return false; } } });
    ui.ready({ catalogETag: 'old' }); ui.change('current query'); ui.result(['one']); await settle();
    ui.workers[0].emit({ type: 'catalog-update', etag: 'new', candidateKey: 'key' });
    const proposed = ui.workers[1];
    if (mode === 'schema') proposed.emit({ type: 'error', message: 'Invalid candidate index' });
    else { proposed.emit({ type: 'ready', catalogETag: 'new', candidateKey: 'key' }); ui.advance(380); await settle(); }
    assert.equal(proposed.terminated, true, mode);
    assert.equal(ui.workers[0].terminated, undefined, mode);
    assert.equal(ui.title(), 'Answer one', mode);
    assert.equal(ui.elements['catalog-status'].textContent, '', mode);
    ui.change('next query');
    assert.equal(ui.workers[0].posts.at(-1).query, 'next query', mode);
  }
});

test('inline answers do not restore the removed back-to-search control', () => {
  assert.doesNotMatch(fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8'), /id="close-guide"|Back to search/);
  assert.doesNotMatch(fs.readFileSync(path.join(__dirname, '../assets/search.js'), 'utf8'), /getElementById\("close-guide"\)/);
});

test('reconnecting an offline cached session coalesces freshness probes and upgrades without losing its answer', async () => {
  let release, probes = 0;
  const pending = new Promise(resolve => { release = resolve; });
  const cache = {
    probeCatalog(etag) { probes++; assert.equal(etag, 'old'); return pending; },
    async acceptCatalog() { return true; },
  };
  const ui = answerUI({ cache });
  ui.ready({ catalogETag: 'old', cacheSource: 'cache' });
  ui.change('offline query'); ui.result(['old-answer']); await settle();
  assert.equal(probes, 0, 'a cached offline boot does not need a main-page network request');
  for (let n = 0; n < 3; n++) ui.window.dispatchEvent(new ui.context.Event('online'));
  await settle();
  assert.equal(probes, 1, 'repeated online events share the in-flight freshness probe');
  ui.change('latest query');
  assert.equal(ui.workers[0].posts.at(-1).query, 'latest query');
  assert.equal(ui.title(), 'Answer old-answer', 'search and the displayed answer remain usable during revalidation');
  release({ etag: 'new', candidateKey: 'reconnected-key' }); await settle();
  assert.equal(ui.workers.length, 2);
  ui.window.dispatchEvent(new ui.context.Event('online')); await settle();
  assert.equal(probes, 1, 'preparing the replacement also coalesces reconnect events');
  ui.workers[1].emit({ type: 'ready', catalogETag: 'new', candidateKey: 'reconnected-key',
    categories: [], source: {}, terms: [], loadDuration: 1 });
  ui.advance(380); await settle(); ui.flushFrames();
  assert.equal(ui.workers[0].terminated, true);
  assert.equal(ui.workers[1].posts.at(-1).query, 'latest query');
  assert.equal(ui.title(), 'Answer old-answer');
  ui.result(['new-answer']); await settle();
  assert.equal(ui.title(), 'Answer new-answer');
  assert.equal(ui.document.activeElement.id, 'response');
  assert.equal(probes, 1, 'typing never starts network freshness checks');
});

test('a nonempty no-match query retains the displayed answer and fences out a prior pending payload', async () => {
  const ui = answerUI({ deferred: ['late'] }); ui.ready();
  ui.change('first query'); ui.result(['first', 'other']); await settle();
  assert.equal(ui.title(), 'Answer first');
  ui.change('pending query'); ui.result(['late']);
  ui.change('unmatched query'); ui.result([]); await settle();
  assert.equal(ui.title(), 'Answer first');
  assert.equal(ui.elements['guide-panel'].hidden, false);
  assert.equal(ui.elements['search-results'].hidden, false);
  assert.equal(ui.elements['result-cards'].children.length, 0);
  assert.equal(ui.elements['result-count'].hidden, true);
  ui.release('late'); await settle();
  assert.equal(ui.title(), 'Answer first', 'late content from the prior query cannot replace a no-match view');
  assert.equal(ui.document.activeElement.id, 'response');
  ui.change('');
  assert.equal(ui.title(), '');
  assert.equal(ui.elements['guide-panel'].hidden, true);
  assert.equal(ui.elements['search-results'].hidden, true);
  const empty = answerUI(); empty.ready(); empty.change('unmatched'); empty.result([]); await settle();
  assert.equal(empty.title(), '', 'an initial no-match result remains blank');
  assert.equal(empty.elements['guide-panel'].hidden, true);
});
