const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// This fixture checks DOM/events and the real registry without opening a
// browser. It does not model CSS layout, SVG paint, or font measurements.
function registry({ art = true, pilot = null, fetchArt } = {}) {
  const document = { activeElement: null };
  const scrolls = [];
  class Node {
    constructor(tag) {
      this.tagName = tag;
      this.children = [];
      this.parentNode = null;
      this.attributes = new Map();
      this.dataset = {};
      this.listeners = new Map();
      this.hidden = false;
      this.textContent = '';
      this.style = { setProperty(name, value) { this[name] = value; } };
      const names = new Set();
      this.classList = {
        add: (...values) => values.forEach(value => names.add(value)),
        remove: (...values) => values.forEach(value => names.delete(value)),
        contains: value => names.has(value),
        toggle(value, force = !names.has(value)) { force ? names.add(value) : names.delete(value); return force; },
      };
      Object.defineProperty(this, 'className', {
        get: () => [...names].join(' '),
        set: value => { names.clear(); String(value).split(/\s+/).filter(Boolean).forEach(name => names.add(name)); },
      });
    }
    setAttribute(name, value) {
      this.attributes.set(name, String(value));
      if (name === 'class') this.className = value;
      if (name.startsWith('data-')) this.dataset[name.slice(5)] = String(value);
    }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    removeAttribute(name) { this.attributes.delete(name); }
    append(...nodes) { for (const node of nodes) { this.children.push(node); node.parentNode = this; } }
    replaceChildren(...nodes) {
      this.children.forEach(node => { node.parentNode = null; });
      this.children = [];
      this.append(...nodes);
    }
    addEventListener(type, listener) {
      if (!this.listeners.has(type)) this.listeners.set(type, []);
      this.listeners.get(type).push(listener);
    }
    dispatch(type, options = {}) {
      const event = { type, target: this, defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; }, ...options };
      for (let node = this; node; node = options.bubbles ? node.parentNode : null) {
        for (const listener of node.listeners.get(type) || []) listener(event);
      }
      return event;
    }
    focus() {
      document.activeElement?.dispatch('blur');
      document.activeElement = this;
      this.dispatch('focus');
    }
    scrollIntoView(options) { scrolls.push({ node: this, options }); }
  }
  document.createElement = tag => new Node(tag);
  document.createElementNS = (namespace, tag) => {
    assert.equal(namespace, 'http://www.w3.org/2000/svg');
    return new Node(tag);
  };
  const window = pilot ? { mendSpecifierPilot: pilot } : {};
  const context = vm.createContext({ document, window, fetch: fetchArt });
  if (art) vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/bicycle-art.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/specifiers.js'), 'utf8'), context);
  const container = new Node('span');
  container.className = 'topic-specifier';
  const selected = [];
  const mount = subject => window.mendSpecifiers.mount(container, subject, value => selected.push(value));
  const walk = node => [node, ...node.children.flatMap(walk)];
  const part = (id, tag) => walk(container).find(node => node.dataset.part === id && (!tag || node.tagName === tag));
  return { api: window.mendSpecifiers, art: window.mendBicycleArt, document, container, selected, scrolls, mount, walk, part,
    svg: () => walk(container).find(node => node.tagName === 'svg'),
    replaceArt(value) { window.mendBicycleArt = value; } };
}

test('bicycle synonyms resolve to one archetype and supported nouns expose concise list fallbacks', () => {
  const ui = registry();
  const bicycle = ui.api.resolve('bike');
  for (const noun of ['bicycle', 'ebike', 'e-bike', 'road bike', 'mountain bike', 'Bike!', 'bikes']) {
    assert.equal(ui.api.resolve(noun), bicycle, noun);
  }
  assert.equal(bicycle.id, 'bicycle');
  for (const noun of ['arm', 'laptop', 'phone', 'car', 'stove', 'toilet', 'refrigerator', 'jeans']) {
    const definition = ui.api.resolve(noun);
    assert.ok(definition.parts.length >= 5, noun);
    ui.mount(noun);
    assert.equal(ui.container.hidden, false);
    assert.equal(ui.container.classList.contains('is-list-only'), true);
    assert.equal(ui.walk(ui.container).filter(node => node.tagName === 'svg').length, 0, 'no empty SVG box');
  }
  assert.equal(ui.api.resolve('unfamiliar thing'), null);
  assert.equal(ui.mount('unfamiliar thing'), null);
  assert.equal(ui.container.hidden, true);
  assert.equal(ui.container.children.length, 0);
  assert.ok(Array.from(ui.api.nouns).includes('road bike'));
});

test('the bicycle contains eleven independent accessible assemblies with generous hit targets', () => {
  const ui = registry();
  ui.mount('bike');
  const svg = ui.svg();
  assert.equal(svg.tagName, 'svg');
  assert.equal(svg.getAttribute('viewBox'), ui.art.viewBox);
  const groups = svg.children.filter(node => node.dataset.part);
  assert.equal(groups.length, 11);
  assert.equal(new Set(groups.map(node => node.dataset.part)).size, 11);
  for (const group of groups) {
    assert.equal(group.getAttribute('role'), 'button');
    assert.equal(group.getAttribute('tabindex'), '0');
    assert.ok(group.getAttribute('aria-label'));
    const [geometry, hit] = group.children;
    const leaves = node => ui.walk(node).filter(child => child.tagName !== 'g');
    const visible = leaves(geometry), targets = leaves(hit);
    assert.ok(visible.length > 0, group.dataset.part);
    assert.ok(visible.every(node => node.getAttribute('data-source-id')), 'every visible shape retains its upstream provenance');
    assert.equal(targets.length, visible.length);
    for (const outline of visible.filter(node => node.classList.contains('specifier-outline'))) {
      assert.ok(Number(outline.style['--specifier-source-stroke']) > 0, 'active outline widths derive from the upstream shape');
    }
    for (let at = 0; at < targets.length; at++) {
      assert.equal(targets[at].getAttribute('stroke'), 'transparent');
      assert.equal(targets[at].getAttribute('stroke-width'), '14');
      assert.equal(targets[at].getAttribute('vector-effect'), 'non-scaling-stroke');
      assert.equal(targets[at].getAttribute('pointer-events'), 'stroke');
      assert.equal(targets[at].tagName, visible[at].tagName);
      for (const attribute of ['d', 'cx', 'cy', 'r', 'transform', 'data-source-id']) {
        assert.equal(targets[at].getAttribute(attribute), visible[at].getAttribute(attribute));
      }
    }
    const transforms = node => ui.walk(node).map(child => child.getAttribute('transform'));
    assert.deepEqual(transforms(geometry), transforms(hit), 'source transforms match the hit geometry exactly');
    const option = ui.part(group.dataset.part, 'button');
    assert.equal(option.type, 'button');
    assert.equal(option.getAttribute('aria-pressed'), 'false');
  }
});

test('SVG hover or focus highlights and scrolls the matching list option while list focus highlights the SVG', () => {
  const ui = registry();
  ui.mount('bicycle');
  const chain = ui.part('chain', 'g'), option = ui.part('chain', 'button');
  chain.dispatch('pointerenter');
  assert.equal(chain.classList.contains('is-highlighted'), true);
  assert.equal(option.classList.contains('is-highlighted'), true);
  assert.equal(ui.scrolls.at(-1).node, option);
  assert.equal(ui.scrolls.at(-1).options.block, 'nearest');
  chain.dispatch('pointerleave');
  assert.equal(option.classList.contains('is-highlighted'), false);
  const brake = ui.part('brakes', 'g'), brakeOption = ui.part('brakes', 'button');
  brakeOption.focus();
  assert.equal(brake.classList.contains('is-highlighted'), true);
  assert.equal(brakeOption.classList.contains('is-highlighted'), true);
  chain.focus();
  assert.equal(brake.classList.contains('is-highlighted'), false);
  assert.equal(option.classList.contains('is-highlighted'), true);
  assert.equal(ui.scrolls.at(-1).node, option);
  assert.equal(ui.selected.length, 0, 'preview never writes the answer');
});

test('both surfaces select the canonical query, preserve edits, and toggle only an unchanged selection', () => {
  const ui = registry();
  const mounted = ui.mount('bike');
  const chain = ui.part('chain', 'g'), option = ui.part('chain', 'button');
  chain.children[1].dispatch('click', { bubbles: true });
  assert.deepEqual(ui.selected, ['chain']);
  assert.equal(chain.classList.contains('is-selected'), true);
  assert.equal(option.getAttribute('aria-pressed'), 'true');
  option.dispatch('click');
  assert.deepEqual(ui.selected, ['chain', '']);
  assert.equal(chain.classList.contains('is-selected'), false);
  mounted.sync('my chain is broken');
  assert.equal(chain.classList.contains('is-selected'), true);
  assert.deepEqual(ui.selected, ['chain', ''], 'typed text is never rewritten by sync');
  option.dispatch('click');
  assert.equal(ui.selected.at(-1), 'chain', 'modified text selects a clean query instead of clearing');
  mounted.sync('a master link broke');
  assert.equal(chain.classList.contains('is-selected'), true);
  mounted.sync('front brake pads');
  assert.equal(ui.part('brakes', 'g').classList.contains('is-selected'), true);
  assert.equal(chain.classList.contains('is-selected'), false);
  mounted.sync('');
  assert.ok(ui.walk(ui.container).every(node => !node.classList.contains('is-selected')));
  assert.equal(ui.svg().classList.contains('has-highlight'), false);
});

test('SVG keyboard controls select once and destroyed controls cannot change a subsequent answer', () => {
  const ui = registry();
  const mounted = ui.mount('bike');
  const pedal = ui.part('pedals', 'g');
  assert.equal(pedal.dispatch('keydown', { key: 'Enter' }).defaultPrevented, true);
  assert.deepEqual(ui.selected, ['pedal']);
  assert.equal(pedal.dispatch('keydown', { key: ' ' }).defaultPrevented, true);
  assert.deepEqual(ui.selected, ['pedal', '']);
  assert.equal(pedal.dispatch('keydown', { key: 'Escape' }).defaultPrevented, false);
  mounted.destroy();
  pedal.dispatch('click');
  mounted.sync('pedal');
  assert.deepEqual(ui.selected, ['pedal', '']);
  assert.equal(ui.container.hidden, true);
  assert.equal(ui.container.children.length, 0);
  ui.mount('arm');
  ui.part('swelling', 'button').dispatch('click');
  assert.equal(ui.selected.at(-1), 'swelling');
});


test('the paired controls keep source notices in SVG metadata without a visible credit line or link', () => {
  const ui = registry(); ui.mount('bike');
  const [controls] = ui.container.children;
  assert.equal(ui.container.children.length, 1, 'the hero has no separate credit row');
  assert.equal(controls.className, 'specifier-controls');
  assert.equal(controls.children[0], ui.svg());
  assert.equal(controls.children[1].className, 'specifier-list');
  const metadata = ui.svg().children.filter(node => node.tagName === 'metadata');
  assert.equal(metadata.length, 1);
  assert.deepEqual(JSON.parse(metadata[0].textContent), JSON.parse(JSON.stringify(ui.art.attribution)),
    'both sources retain titles, authors, source/license URLs and adaptation notices with the artwork');
  assert.equal(ui.svg().getAttribute('aria-label'), 'Bicycle parts', 'licensing does not replace the diagram purpose');
  assert.equal(ui.svg().getAttribute('aria-describedby'), null, 'metadata is not an assistive licensing readout');
  assert.equal(ui.walk(ui.container).some(node => node.tagName === 'a' || node.classList.contains('specifier-credit')), false,
    'no inline source link or link to the unlinked credits page remains');
  ui.mount('laptop');
  assert.equal(ui.container.children.length, 1);
  assert.equal(ui.walk(ui.container).some(node => node.tagName === 'metadata'), false, 'list-only nouns do not inherit bicycle metadata');
});

test('a missing source module falls back to the parts list with no invented diagram or empty SVG', () => {
  const ui = registry({ art: false });
  const mounted = ui.mount('bike');
  assert.ok(mounted);
  assert.equal(ui.svg(), undefined);
  assert.equal(ui.container.classList.contains('is-list-only'), true);
  assert.equal(ui.walk(ui.container).filter(node => node.tagName === 'button').length, 11);
  ui.part('chain', 'button').dispatch('click');
  assert.deepEqual(ui.selected, ['chain']);
  assert.equal(ui.container.children.length, 1);
});


test('unsafe source descriptors never create active SVG content and retain the usable parts list', () => {
  for (const descriptor of [['script', {}, []], ['path', { d: 'M0 0L1 1', onclick: 'alert(1)' }],
    ['path', { d: 'M0 0L1 1', fill: 'url(https://external.example/image)' }]]) {
    const ui = registry();
    const bad = structuredClone(ui.art);
    bad.context.push(descriptor);
    ui.replaceArt(bad);
    ui.mount('bike');
    assert.equal(ui.svg(), undefined);
    assert.equal(ui.container.classList.contains('is-list-only'), true);
    assert.equal(ui.walk(ui.container).filter(node => node.tagName === 'button').length, 11);
    ui.part('chain', 'button').dispatch('click');
    assert.deepEqual(ui.selected, ['chain']);
  }
});

const pilotFixture = () => ({ version: 1, archetypes: [{
  id: 'phone', aliases: ['cellphone'], artUrl: `/assets/art/phone-${'1'.repeat(64)}.json`,
  parts: [{ id: 'screen', label: 'Screen', query: 'screen', aliases: ['display'] },
    { id: 'battery', label: 'Battery', query: 'battery', aliases: ['charging'] }],
}] });
const phoneArt = () => ({ version: 1, id: 'phone', viewBox: '0 0 100 100',
  context: [['rect', { x: 5, y: 5, width: 90, height: 90, fill: 'none', stroke: 'currentColor' }]],
  parts: { screen: [['rect', { x: 15, y: 15, width: 70, height: 40, fill: 'none', stroke: 'currentColor' }]],
    battery: [['rect', { x: 30, y: 65, width: 40, height: 20, fill: 'var(--paper)', stroke: 'currentColor' }]] },
  attribution: { title: 'Phone diagram', author: 'Fixture', source: 'https://example.org/phone',
    license: 'CC0 1.0', licenseUrl: 'https://creativecommons.org/publicdomain/zero/1.0/', changes: 'Adapted for parts.' },
});
const artResponse = data => ({ ok: true, headers: { get: () => null }, text: async () => JSON.stringify(data) });
async function settleArt() { for (let pass = 0; pass < 24; pass++) await Promise.resolve(); }

test('a lazy local diagram preserves the instant list, selected answer, scroll and focused option', async () => {
  let resolve;
  const requests = [];
  const ui = registry({ pilot: pilotFixture(), fetchArt: (url, options) => {
    requests.push({ url, options }); return new Promise(accept => { resolve = accept; });
  } });
  assert.equal(ui.api.resolve('cellphone').id, 'phone');
  const instance = ui.mount('phone');
  const list = ui.walk(ui.container).find(node => node.className === 'specifier-list');
  const option = ui.part('screen', 'button');
  list.scrollTop = 72;
  instance.sync('display cracked'); option.focus();
  assert.equal(ui.svg(), undefined, 'cold artwork never reserves an empty SVG box');
  assert.equal(ui.container.classList.contains('is-list-only'), true);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, pilotFixture().archetypes[0].artUrl);
  assert.equal(requests[0].options.mode, 'same-origin');
  assert.equal(requests[0].options.credentials, 'same-origin');
  resolve(artResponse(phoneArt())); await settleArt();
  assert.equal(ui.svg().getAttribute('aria-label'), 'Phone parts');
  assert.equal(ui.container.classList.contains('is-list-only'), false);
  assert.ok(ui.walk(ui.container).includes(list), 'the same list stays mounted');
  assert.equal(list.scrollTop, 72);
  assert.ok(ui.document.activeElement === option, 'hydration preserves existing focus');
  assert.equal(ui.part('screen', 'g').getAttribute('aria-pressed'), 'true');
  assert.equal(ui.part('screen', 'g').classList.contains('is-highlighted'), true);
  assert.deepEqual(ui.selected, [], 'hydration never writes the native answer');
});

test('late diagram responses cannot replace a newer noun and cached artwork mounts without another request', async () => {
  let resolve, requests = 0;
  const ui = registry({ pilot: pilotFixture(), fetchArt: () => {
    requests++; return new Promise(accept => { resolve = accept; });
  } });
  const previous = ui.mount('phone'); ui.mount('laptop');
  resolve(artResponse(phoneArt())); await settleArt();
  assert.equal(ui.svg(), undefined);
  assert.equal(ui.container.getAttribute('aria-label'), 'Laptop parts and symptoms');
  previous.destroy();
  assert.equal(ui.container.hidden, false, 'destroying a stale mount cannot remove newer controls');
  ui.mount('phone');
  assert.ok(ui.svg(), 'cached art mounts synchronously');
  assert.equal(requests, 1);
});

test('prefix prefetch is bounded, cached and makes an eventual diagram mount synchronous', async () => {
  let requests = 0;
  const ui = registry({ pilot: pilotFixture(), fetchArt: async () => { requests++; return artResponse(phoneArt()); } });
  ui.api.prefetch('ph'); assert.equal(requests, 0);
  ui.api.prefetch('pho'); ui.api.prefetch('phon'); ui.api.prefetch('phone');
  await settleArt();
  assert.equal(requests, 1);
  ui.mount('phone'); assert.ok(ui.svg());
  assert.equal(requests, 1);
});

test('missing, rejected and unsafe lazy artwork retain usable list controls without an error UI', async () => {
  const unsafe = phoneArt(); unsafe.parts.screen = [['script', {}, []]];
  for (const fetchArt of [async () => { throw new TypeError('Offline'); },
    async () => ({ ok: false }), async () => artResponse({ ...phoneArt(), id: 'car' }),
    async () => artResponse(unsafe)]) {
    const ui = registry({ pilot: pilotFixture(), fetchArt });
    ui.mount('phone'); await settleArt();
    assert.equal(ui.svg(), undefined);
    assert.equal(ui.container.classList.contains('is-list-only'), true);
    ui.part('battery', 'button').dispatch('click');
    assert.deepEqual(ui.selected, ['battery']);
  }
  const pilot = pilotFixture(); pilot.archetypes[0].artUrl = 'https://example.org/phone.json';
  let requests = 0;
  const ui = registry({ pilot, fetchArt: async () => { requests++; throw new Error('Must not request external art'); } });
  ui.mount('phone'); await settleArt();
  assert.equal(requests, 0);
  assert.equal(ui.svg(), undefined);
});
