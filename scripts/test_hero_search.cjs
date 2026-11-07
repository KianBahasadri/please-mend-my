const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { performance } = require('node:perf_hooks');

// Run the actual search UI with the hero controller's public contract. This
// isolates query/focus wiring from native textbox protection and worker ranking,
// which have their own tests; no browser or catalog corpus is needed.
function searchUI({ heroAvailable = true, initialQuery = '' } = {}) {
  let document;
  class Element extends EventTarget {
    constructor(tag = 'div') {
      super();
      this.tagName = tag.toUpperCase();
      this.dataset = {};
      this.style = {};
      this.children = [];
      this.hidden = false;
      this.clientWidth = 1000;
      const classes = new Set();
      this.classList = {
        add: value => classes.add(value),
        remove: value => classes.delete(value),
        contains: value => classes.has(value),
        toggle: (value, enabled) => enabled ? classes.add(value) : classes.delete(value),
      };
    }
    append(...items) {
      for (const item of items) {
        if (item.tagName === 'FRAGMENT') this.children.push(...item.children);
        else this.children.push(item);
      }
    }
    replaceChildren(...items) { this.children = []; this.append(...items); }
    setAttribute() {}
    removeAttribute() {}
    querySelector() { return null; }
    querySelectorAll() { return []; }
    closest(selector) {
      if (selector === 'a' && this.tagName === 'A') return this;
      if (selector === '.repair-card' && this.className === 'repair-card') return this;
      return null;
    }
    getBoundingClientRect() { return { top: 150, bottom: 358 }; }
    focus() { document.activeElement = this; }
  }

  const ids = ['response', 'clarification-response', 'catalog-status', 'search-results',
    'result-cards', 'result-count', 'guide-panel', 'guide-body', 'guide-nav-error', 'close-guide'];
  const elements = Object.fromEntries(ids.map(id => [id, new Element(id.includes('response') ? 'textarea' : 'div')]));
  elements.response.dataset.promptLength = '15';
  elements.response.value = 'please, mend my ';
  elements['search-results'].hidden = true;
  elements['guide-panel'].hidden = true;
  elements['guide-nav-error'].hidden = true;
  document = Object.assign(new EventTarget(), {
    body: new Element('body'), activeElement: elements.response,
    getElementById: id => elements[id],
    createElement: tag => new Element(tag),
    createDocumentFragment: () => new Element('fragment'),
    createTextNode: text => Object.assign(new Element('#text'), { textContent: text }),
  });
  const window = new EventTarget();
  let query = initialQuery;
  let heroFocuses = 0;
  if (heroAvailable) window.mendHero = {
    query: () => query,
    focus() { heroFocuses++; elements['clarification-response'].focus(); },
  };
  const frames = new Map();
  let frameId = 0;
  const posted = [], requests = [], workers = [];
  class Worker {
    constructor(url) { assert.equal(url, '/assets/search-worker.js'); workers.push(this); }
    postMessage(message) { posted.push(structuredClone(message)); }
    emit(message) { this.onmessage({ data: message }); }
  }
  const location = { href: 'http://127.0.0.1:8080/', origin: 'http://127.0.0.1:8080', pathname: '/', protocol: 'http:' };
  const context = vm.createContext({ window, document, location, Worker, Event, URL, performance,
    innerHeight: 900, scrollY: 0,
    history: { pushState(state, title, href) { location.href = new URL(href, location.href).href; } },
    getComputedStyle: () => ({ columnGap: '13.6px', getPropertyValue: name => name === '--columns' ? '3' : '208' }),
    requestAnimationFrame: fn => { const id = ++frameId; frames.set(id, fn); return id; },
    cancelAnimationFrame: id => frames.delete(id),
    fetch: async (url, options) => {
      requests.push(url);
      assert.equal(url, '/api/guides/example');
      assert.equal(options.mode, 'same-origin');
      return { ok: true, json: async () => ({ id: 'example', title: 'Example guide', html: '<p>Guide content</p>', images: [] }) };
    },
  });
  window.scrollTo = () => {};
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/search.js'), 'utf8'), context);
  const flush = () => { const batch = [...frames.values()]; frames.clear(); for (const fn of batch) fn(); };
  return {
    document, window, elements, posted, requests, workers, flush,
    get heroFocuses() { return heroFocuses; },
    ready() { workers[0].emit({ type: 'ready', categories: [], source: {}, loadDuration: 1 }); },
    change(value) { query = value; window.dispatchEvent(new Event('mend:querychange')); },
    clickGuide() {
      const link = new Element('a');
      link.href = 'http://127.0.0.1:8080/?guide=example';
      const event = new Event('click', { cancelable: true });
      Object.defineProperties(event, { target: { value: link }, button: { value: 0 } });
      document.dispatchEvent(event);
    },
  };
}

test('hero noun and symptom changes coalesce into one suffix-only worker search', () => {
  const ui = searchUI();
  ui.ready();
  ui.change('car');
  ui.change('car won');
  ui.change("car won't start");
  ui.elements.response.dispatchEvent(new Event('input'));
  ui.flush();
  const searches = ui.posted.filter(message => message.type === 'search');
  assert.equal(searches.length, 1);
  assert.equal(searches[0].query, "car won't start");
  assert.ok(searches[0].count <= 60);
  assert.equal(ui.requests.length, 0, 'typing must not make a server request');
});

test('the latest combined query survives worker startup and full phrases remain untouched', () => {
  const ui = searchUI({ initialQuery: 'arm' });
  ui.change('arm hurts');
  ui.flush();
  assert.equal(ui.posted.length, 0, 'wait for the resident index before searching');
  ui.change('broken arm');
  ui.ready();
  ui.flush();
  assert.equal(ui.posted.at(-1).query, 'broken arm');
  assert.equal(ui.requests.length, 0);
});

test('dismissing clarification replaces its symptom and clearing both inputs cancels pending search', () => {
  const ui = searchUI();
  ui.ready();
  ui.change('laptop screen cracked');
  ui.flush();
  ui.change('laptop');
  ui.flush();
  assert.deepEqual(ui.posted.filter(message => message.type === 'search').map(message => message.query),
    ['laptop screen cracked', 'laptop']);
  ui.change('laptop battery');
  ui.change('');
  ui.flush();
  assert.equal(ui.posted.filter(message => message.type === 'search').length, 2);
  assert.equal(ui.elements['search-results'].hidden, true);
  assert.equal(ui.elements['result-cards'].children.length, 0);
  assert.equal(ui.document.body.classList.contains('searching'), false);
});

test('returning from a guide restores the hero controller focus and combined query', async () => {
  const ui = searchUI({ initialQuery: 'car brakes squeak' });
  ui.ready();
  ui.flush();
  ui.clickGuide();
  for (let pass = 0; pass < 10; pass++) await Promise.resolve();
  assert.equal(ui.document.body.classList.contains('reading'), true);
  assert.equal(ui.elements['guide-panel'].hidden, false);
  assert.equal(ui.heroFocuses, 0, 'opening a guide must keep its heading focus');
  ui.elements['close-guide'].dispatchEvent(new Event('click'));
  ui.flush();
  assert.equal(ui.heroFocuses, 1);
  assert.equal(ui.document.activeElement, ui.elements['clarification-response']);
  assert.equal(ui.document.body.classList.contains('reading'), false);
  assert.equal(ui.posted.filter(message => message.type === 'search').at(-1).query, 'car brakes squeak');
  assert.deepEqual(ui.requests, ['/api/guides/example']);
});

test('a cached older page without the hero controller still searches the editable first suffix', () => {
  const ui = searchUI({ heroAvailable: false });
  ui.ready();
  ui.elements.response.value = 'please, mend my broken arm';
  ui.elements.response.dispatchEvent(new Event('input'));
  ui.flush();
  assert.equal(ui.posted.at(-1).query, 'broken arm');
  assert.equal(ui.requests.length, 0);
});
