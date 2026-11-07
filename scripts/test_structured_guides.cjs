const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { performance } = require('node:perf_hooks');

const ID = '0123456789abcdef0123';
const source = fs.readFileSync(path.join(__dirname, '../assets/search.js'), 'utf8');
const origin = 'http://127.0.0.1:8080';
const settle = async () => { for (let n = 0; n < 12; n++) await Promise.resolve(); };

// This exercises the real search/navigation/rendering script with a small DOM
// event model. It does not open a browser or assert layout/paint timings.
function guideUI(detail, { deferred = false, clipboardFailure = false } = {}) {
  let document;
  const htmlWrites = [];
  class Element extends EventTarget {
    constructor(tag = 'div') {
      super();
      this.tagName = tag.toUpperCase();
      this.dataset = {};
      this.style = {};
      this.childNodes = [];
      this.attributes = {};
      this.className = '';
      this.hidden = false;
      this.clientWidth = 1000;
      this.classList = {
        contains: value => this.className.split(/\s+/).includes(value),
        add: value => { if (!this.classList.contains(value)) this.className += ` ${value}`; },
        remove: value => { this.className = this.className.split(/\s+/).filter(v => v !== value).join(' '); },
        toggle: (value, enabled) => enabled ? this.classList.add(value) : this.classList.remove(value),
      };
    }
    get children() { return this.childNodes.filter(child => child.tagName !== '#TEXT'); }
    set textContent(value) { this.childNodes = []; this._text = String(value); }
    get textContent() { return (this._text || '') + this.childNodes.map(child => child.textContent).join(''); }
    set innerHTML(value) { this.childNodes = []; this._html = value; htmlWrites.push(value); }
    get innerHTML() { return this._html || ''; }
    append(...items) {
      for (const item of items) {
        if (item.tagName === 'FRAGMENT') { this.append(...item.childNodes); continue; }
        item.parentElement = this;
        this.childNodes.push(item);
      }
    }
    replaceChildren(...items) { this.childNodes = []; this._text = ''; this.append(...items); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    removeAttribute(name) { delete this.attributes[name]; }
    matches(selector) {
      if (selector.startsWith('.')) return this.classList.contains(selector.slice(1));
      const data = selector.match(/^\[data-([a-z-]+)="([^"]*)"\]$/);
      if (data) return String(this.dataset[data[1].replace(/-([a-z])/g, (_, char) => char.toUpperCase())]) === data[2];
      return this.tagName === selector.toUpperCase();
    }
    querySelectorAll(selector) {
      const matches = [];
      for (const child of this.children) {
        if (child.matches(selector)) matches.push(child);
        matches.push(...child.querySelectorAll(selector));
      }
      return matches;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
    getBoundingClientRect() { return { top: 150, bottom: 358 }; }
    focus() { document.activeElement = this; }
  }

  const ids = ['response', 'catalog-status', 'search-results', 'result-cards', 'result-count',
    'guide-panel', 'guide-body', 'guide-nav-error', 'close-guide'];
  const elements = Object.fromEntries(ids.map(id => [id, new Element(id === 'response' ? 'textarea' : 'div')]));
  elements['search-results'].hidden = true;
  elements['guide-panel'].hidden = true;
  elements['guide-nav-error'].hidden = true;
  document = Object.assign(new EventTarget(), {
    body: new Element('body'), activeElement: elements.response,
    getElementById: id => elements[id], createElement: tag => new Element(tag),
    createDocumentFragment: () => new Element('fragment'),
    createTextNode: text => Object.assign(new Element('#text'), { textContent: text }),
  });
  let query = 'laptop';
  const window = Object.assign(new EventTarget(), {
    scrollTo() {}, mendHero: { query: () => query, focus: () => elements.response.focus() },
  });
  const frames = new Map();
  let frameId = 0;
  const posted = [], requests = [], workers = [], copied = [], images = [];
  class Worker {
    constructor() { workers.push(this); }
    postMessage(message) { posted.push(structuredClone(message)); }
    emit(message) { this.onmessage({ data: message }); }
  }
  class Image { constructor() { images.push(this); } }
  let release;
  const pending = deferred ? new Promise(resolve => { release = resolve; }) : Promise.resolve();
  const location = { href: `${origin}/`, origin, pathname: '/', protocol: 'http:' };
  const context = vm.createContext({ window, document, location, Worker, Image, Event, URL, performance,
    innerHeight: 900, scrollY: 0,
    history: { pushState(state, title, href) { location.href = new URL(href, location.href).href; } },
    navigator: { clipboard: { async writeText(text) { if (clipboardFailure) throw Error('Denied'); copied.push(text); } } },
    getComputedStyle: () => ({ columnGap: '13.6px', getPropertyValue: name => name === '--columns' ? '3' : '208' }),
    requestAnimationFrame: fn => { const id = ++frameId; frames.set(id, fn); return id; },
    cancelAnimationFrame: id => frames.delete(id),
    fetch: async (url, options) => {
      assert.equal(url, `/api/guides/${ID}`);
      assert.equal(options.mode, 'same-origin');
      requests.push(url);
      await pending;
      return { ok: true, json: async () => detail };
    },
  });
  vm.runInContext(source, context);
  const flush = () => { const batch = [...frames.values()]; frames.clear(); for (const fn of batch) fn(); };
  const click = (target) => {
    const event = new Event('click', { cancelable: true });
    Object.defineProperties(event, { target: { value: target }, button: { value: 0 } });
    document.dispatchEvent(event);
  };
  return {
    document, elements, window, requests, posted, copied, images, htmlWrites, release,
    ready() { workers[0].emit({ type: 'ready', categories: [], source: {}, loadDuration: 1 }); flush(); },
    prefetch() {
      const request = posted.findLast(message => message.type === 'search');
      workers[0].emit({ type: 'results', requestId: request.requestId, duration: 1, total: 1,
        items: [[ID, detail.title || 'Guide', 'technology', 'guide', '']] });
    },
    open() { const link = new Element('a'); link.href = `${origin}/?guide=${ID}`; click(link); },
    change(value) { query = value; window.dispatchEvent(new Event('mend:querychange')); flush(); },
    find(selector) { return elements['guide-body'].querySelector(selector); },
    all(selector) { return elements['guide-body'].querySelectorAll(selector); },
  };
}

function structured(overrides = {}) {
  return {
    version: 2, id: ID, title: 'Fix laptop audio', summary: 'Check the sound service.',
    sourceName: 'Original manual', sourceUrl: 'https://example.org/manual',
    attribution: 'Original authors', license: { name: 'CC BY-SA 4.0', url: 'https://creativecommons.org/licenses/by-sa/4.0/' },
    tools: ['Terminal'], materials: ['None required'], symptoms: ['No sound'],
    safety: [{ level: 'warning', text: 'Save your work first.' }],
    steps: [{ title: 'Inspect the service', body: 'Read its current state.\nKeep the output.', warnings: ['Do not delete configuration.'],
      images: ['/media/abc.png'], code: 'systemctl --user status pipewire\nwpctl status' }],
    images: [], related: [], references: [{ title: 'Audio reference', url: 'https://example.org/audio' }], ...overrides,
  };
}

test('validated v2 renders text sections, local checklist and exact code-copy behavior', async () => {
  const ui = guideUI(structured());
  ui.ready(); ui.open(); await settle();
  assert.equal(ui.find('.guide-title').textContent, 'Fix laptop audio');
  assert.equal(ui.find('.guide-summary').textContent, 'Check the sound service.');
  assert.ok(ui.find('.guide-structured'));
  assert.deepEqual(ui.all('h2').map(node => node.textContent), ['Before you start', 'Tools', 'Materials', 'Symptoms', 'Steps']);
  assert.equal(ui.all('.guide-notice-warning').length, 2);
  assert.equal(ui.find('.guide-step-body').textContent, 'Read its current state.\nKeep the output.');
  assert.equal(ui.find('img').src, `${origin}/media/abc.png`);
  assert.equal(ui.find('img').loading, 'lazy');
  const checkbox = ui.find('input');
  assert.equal(checkbox.type, 'checkbox');
  assert.match(checkbox.attributes['aria-label'], /Mark step 1 complete/);
  checkbox.checked = true; checkbox.dispatchEvent(new Event('change'));
  assert.equal(ui.find('.guide-step').classList.contains('is-complete'), true);
  checkbox.checked = false; checkbox.dispatchEvent(new Event('change'));
  assert.equal(ui.find('.guide-step').classList.contains('is-complete'), false);
  ui.find('.guide-copy').dispatchEvent(new Event('click')); await settle();
  assert.deepEqual(ui.copied, ['systemctl --user status pipewire\nwpctl status']);
  assert.equal(ui.find('.guide-copy').textContent, 'Copied');
  assert.equal(ui.document.activeElement, ui.find('.guide-title'));
  assert.deepEqual(ui.htmlWrites, [], 'model output must never reach innerHTML');
  assert.equal(ui.requests.length, 1, 'checklists and copy must not issue network requests');
});

test('legacy guides retain their sanitized HTML path and source credits', async () => {
  const html = '<h2>Original steps</h2><p>Preserved ingestion HTML.</p>';
  const ui = guideUI({ id: ID, title: 'Legacy guide', html, sourceUrl: 'https://www.ifixit.com/Guide/example',
    attribution: 'Original author; reformatted and sanitized for this offline catalog.', images: [] });
  ui.ready(); ui.open(); await settle();
  assert.equal(ui.find('.guide-content').innerHTML, html);
  assert.deepEqual(ui.htmlWrites, [html]);
  assert.equal(ui.find('.guide-structured'), null);
  assert.match(ui.find('.guide-attribution').textContent, /Original on iFixit/);
  assert.match(ui.find('.guide-attribution').textContent, /Original author/);
  assert.doesNotMatch(ui.find('.guide-attribution').textContent, /reformatted and sanitized/);
});

test('references share the provenance row and allow only safe deduplicated links', async () => {
  const ui = guideUI(structured({ references: [
    { title: 'Safe reference', url: 'https://example.org/reference' },
    { title: 'Duplicate', url: 'https://example.org/reference' },
    { title: 'Script', url: 'javascript:alert(1)' }, { title: 'Data', url: 'data:text/html,unsafe' },
    { title: 'Missing' }, { url: 'https://example.org/no-title' }, null,
  ] }));
  ui.ready(); ui.open(); await settle();
  const references = ui.find('.guide-references');
  assert.equal(references.parentElement, ui.find('.guide-source'));
  assert.equal(references.children.length, 1);
  assert.equal(references.children[0].textContent, 'Safe reference');
  assert.equal(references.children[0].href, 'https://example.org/reference');
  for (const link of ui.all('a')) {
    assert.equal(link.target, '_blank');
    assert.equal(link.rel, 'noopener noreferrer');
  }
});

test('structured strings are plain text and external step images are excluded', async () => {
  const text = '<img src=x onerror="window.attacked=1">';
  const ui = guideUI(structured({ title: text, summary: text, tools: [text],
    steps: [{ title: text, body: text, warnings: [text], code: text,
      images: ['https://other.example/image.jpg', 'javascript:alert(1)', '/assets/hero.js', '/media/local.png'] }],
    html: '<script>window.attacked=1</script>', references: [{ title: text, url: 'https://example.org/reference' }],
  }));
  ui.ready(); ui.open(); await settle();
  assert.equal(ui.find('.guide-title').textContent, text);
  assert.equal(ui.find('.guide-step-body').textContent, text);
  assert.equal(ui.find('code').textContent, text);
  assert.equal(ui.all('img').length, 1);
  assert.equal(ui.find('img').src, `${origin}/media/local.png`);
  assert.deepEqual(ui.htmlWrites, []);
  assert.equal(ui.window.attacked, undefined);
});

test('malformed v2 remains text-only and empty procedures do not fabricate steps', async () => {
  for (const steps of [null, {}, [null, 'wrong', { title: 'Missing body' }], []]) {
    const ui = guideUI(structured({ steps, tools: {}, materials: [null], safety: [null, {}], symptoms: null,
      images: null, related: [null], html: '<script>bad()</script>' }));
    ui.ready(); ui.open(); await settle();
    assert.ok(ui.find('.guide-structured'));
    assert.equal(ui.find('.guide-steps'), null);
    assert.deepEqual(ui.htmlWrites, []);
    assert.equal(ui.elements['guide-nav-error'].hidden, true);
  }
});

test('a steps array dispatches to structured rendering without an explicit version', async () => {
  const detail = structured(); delete detail.version;
  const ui = guideUI(detail);
  ui.ready(); ui.open(); await settle();
  assert.ok(ui.find('.guide-structured'));
  assert.equal(ui.all('.guide-step').length, 1);
  assert.deepEqual(ui.htmlWrites, []);
});

test('prefetched v2 and legacy cards open synchronously without fetching twice', async () => {
  for (const detail of [structured(), { id: ID, title: 'Legacy', html: '<p>Ready</p>', images: [] }]) {
    const ui = guideUI(detail);
    ui.ready(); ui.prefetch(); await settle();
    assert.equal(ui.requests.length, 1);
    assert.equal(ui.elements['guide-panel'].hidden, true);
    ui.open();
    assert.equal(ui.elements['guide-panel'].hidden, false, 'already-prefetched content commits during the click');
    assert.equal(ui.find('.guide-title').textContent, detail.title);
    assert.equal(ui.requests.length, 1);
    assert.equal(ui.window.mendSearchStats.cachedOpenMs.length, 1);
  }
});

test('pending guide content retains search cards and emits no loading status', async () => {
  const ui = guideUI(structured(), { deferred: true });
  ui.ready(); ui.prefetch(); ui.open(); await settle();
  assert.equal(ui.elements['guide-panel'].hidden, true);
  assert.equal(ui.elements['search-results'].hidden, false);
  assert.equal(ui.elements['result-cards'].children.length, 1);
  assert.equal(ui.elements['catalog-status'].textContent, '');
  assert.equal(ui.elements['guide-body'].textContent, '');
  ui.release(); await settle();
  assert.equal(ui.elements['guide-panel'].hidden, false);
  assert.ok(ui.find('.guide-structured'));
});

test('copy denial does not hide content or trigger network fallbacks', async () => {
  const ui = guideUI(structured(), { clipboardFailure: true });
  ui.ready(); ui.open(); await settle();
  ui.find('.guide-copy').dispatchEvent(new Event('click')); await settle();
  assert.equal(ui.find('.guide-copy').textContent, 'Copy unavailable');
  assert.equal(ui.find('code').textContent, structured().steps[0].code);
  assert.equal(ui.requests.length, 1);
  assert.deepEqual(ui.copied, []);
});
