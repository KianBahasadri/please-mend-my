const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { createHash } = require('node:crypto');
const { releaseFixture, bicycleArt, artURL } = require('./specifier_test_data.cjs');

// This fixture checks DOM/events and the real registry without opening a
// browser. It does not model CSS layout, SVG paint, or font measurements.
async function registry({ art = true, pilot = null, fetchArt, bicycle = bicycleArt() } = {}) {
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
    insertBefore(node, reference) {
      const index = this.children.indexOf(reference);
      assert.ok(index >= 0, 'insertion reference must remain connected');
      this.children.splice(index, 0, node); node.parentNode = this;
    }
    replaceChildren(...nodes) {
      const containsFocus = node => node === document.activeElement || node.children.some(containsFocus);
      if (this.children.some(containsFocus)) document.activeElement = null;
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
  const window = {};
  const payload = releaseFixture({ art });
  if (pilot) {
    const overrides = new Map(pilot.archetypes.map(row => [row.id, row]));
    payload.archetypes = payload.archetypes.map(row => overrides.has(row.id) ? { ...row, ...overrides.get(row.id) } : row);
    for (const row of pilot.archetypes) if (!payload.archetypes.some(item => item.id === row.id)) payload.archetypes.push({ guideCount: 1, ...row });
  }
  const bike = payload.archetypes.find(row => row.id === 'bicycle');
  if (art) bike.artUrl = artURL(bicycle);
  const context = vm.createContext({ document, window, Event, fetch: fetchArt || (art ? async () => artResponse(bicycle) : undefined) });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/specifiers.js'), 'utf8'), context);
  window.mendSpecifiers.configure(payload);
  if (art && !fetchArt) { window.mendSpecifiers.prefetch('bike'); await settleArt(); }
  const container = new Node('span');
  container.className = 'topic-specifier';
  const selected = [];
  const mount = subject => window.mendSpecifiers.mount(container, subject, value => selected.push(value));
  const walk = node => [node, ...node.children.flatMap(walk)];
  const part = (id, tag) => walk(container).find(node => node.dataset.part === id && (!tag || node.tagName === tag));
  return { api: window.mendSpecifiers, art: bicycle, document, container, selected, scrolls, mount, walk, part,
    svg: () => walk(container).find(node => node.tagName === 'svg'),
     };
}

test('bicycle synonyms resolve to one archetype and supported nouns expose concise list fallbacks', async () => {
  const ui = await registry();
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

test('the bicycle contains eleven independent accessible assemblies with generous hit targets', async () => {
  const ui = await registry();
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

test('SVG hover or focus highlights and scrolls the matching list option while list focus highlights the SVG', async () => {
  const ui = await registry();
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

test('both surfaces select the canonical query, preserve edits, and toggle only an unchanged selection', async () => {
  const ui = await registry();
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

test('SVG keyboard controls select once and destroyed controls cannot change a subsequent answer', async () => {
  const ui = await registry();
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


test('the paired controls keep source notices in SVG metadata without a visible credit line or link', async () => {
  const ui = await registry(); ui.mount('bike');
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

test('a noun without release artwork keeps its parts list with no invented diagram or empty SVG', async () => {
  const ui = await registry({ art: false });
  const mounted = ui.mount('bike');
  assert.ok(mounted);
  assert.equal(ui.svg(), undefined);
  assert.equal(ui.container.classList.contains('is-list-only'), true);
  assert.equal(ui.walk(ui.container).filter(node => node.tagName === 'button').length, 11);
  ui.part('chain', 'button').dispatch('click');
  assert.deepEqual(ui.selected, ['chain']);
  assert.equal(ui.container.children.length, 1);
});


test('unsafe source descriptors never create active SVG content and retain the usable parts list', async () => {
  for (const descriptor of [['script', {}, []], ['path', { d: 'M0 0L1 1', onclick: 'alert(1)' }],
    ['path', { d: 'M0 0L1 1', fill: 'url(https://external.example/image)' }]]) {
    const bad = structuredClone(bicycleArt());
    bad.context.push(descriptor);
    const ui = await registry({ bicycle: bad });
    ui.mount('bike');
    assert.equal(ui.svg(), undefined);
    assert.equal(ui.container.classList.contains('is-list-only'), true);
    assert.equal(ui.walk(ui.container).filter(node => node.tagName === 'button').length, 11);
    ui.part('chain', 'button').dispatch('click');
    assert.deepEqual(ui.selected, ['chain']);
  }
});

const pilotFixture = () => ({ version: 1, archetypes: [{
  id: 'phone', label: 'Phone', aliases: ['phone', 'cellphone'], guideCount: 1, artUrl: `/api/art/${'1'.repeat(64)}.json`,
  parts: [{ id: 'screen', label: 'Screen', query: 'screen', aliases: ['display'] },
    { id: 'battery', label: 'Battery', query: 'battery', aliases: ['charging'] },
    { id: 'water', label: 'Water damage', query: 'water damage', aliases: ['wet'] }],
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

test('a generated archetype can be added through data without hardcoding another runtime branch', async () => {
  const pilot = pilotFixture();
  pilot.archetypes.push({ id: 'espresso-machine', label: 'Espresso machine', guideCount: 1, aliases: ['espresso machine'],
    artUrl: null, parts: [{ id: 'drain', label: 'Drain', query: 'drain', aliases: ['draining'] }] });
  const ui = await registry({ pilot });
  assert.equal(ui.api.resolve('espresso machine').id, 'espresso-machine');
  ui.mount('espresso machine');
  assert.equal(ui.container.hidden, false);
  assert.equal(ui.container.getAttribute('aria-label'), 'Espresso machine parts and symptoms');
  ui.part('drain', 'button').dispatch('click');
  assert.deepEqual(ui.selected, ['drain']);
});

test('a lazy local diagram preserves the instant list, selected answer, scroll and focused option', async () => {
  let resolve;
  const requests = [];
  const ui = await registry({ pilot: pilotFixture(), fetchArt: (url, options) => {
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
  option.dispatch('blur'); instance.sync('wet');
  assert.equal(ui.part('water', 'button').getAttribute('aria-pressed'), 'true');
  assert.equal(ui.svg().classList.contains('has-highlight'), false,
    'a list-only symptom must not dim all geometry as though it had a mapped region');
});

test('late diagram responses cannot replace a newer noun and cached artwork mounts without another request', async () => {
  let resolve, requests = 0;
  const ui = await registry({ pilot: pilotFixture(), fetchArt: () => {
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
  const ui = await registry({ pilot: pilotFixture(), fetchArt: async () => { requests++; return artResponse(phoneArt()); } });
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
    const ui = await registry({ pilot: pilotFixture(), fetchArt });
    ui.mount('phone'); await settleArt();
    assert.equal(ui.svg(), undefined);
    assert.equal(ui.container.classList.contains('is-list-only'), true);
    ui.part('battery', 'button').dispatch('click');
    assert.deepEqual(ui.selected, ['battery']);
  }
  const pilot = pilotFixture(); pilot.archetypes[0].artUrl = 'https://example.org/phone.json';
  let requests = 0;
  const ui = await registry({ pilot, fetchArt: async () => { requests++; throw new Error('Must not request external art'); } });
  ui.mount('phone'); await settleArt();
  assert.equal(requests, 0);
  assert.equal(ui.svg(), undefined);
});

test('the approved build data renders through release URLs without embedded catalog modules', async () => {
  const phone = JSON.parse(fs.readFileSync(path.join(__dirname, 'data/approved-phone-art.json'), 'utf8'));
  const payload = releaseFixture();
  const definition = payload.archetypes.find(row => row.id === 'phone');
  definition.artUrl = artURL(phone);
  const ui = await registry({ pilot: { version: 1, archetypes: [definition] }, fetchArt: async (url, options) => {
    assert.equal(url, definition.artUrl);
    assert.equal(options.mode, 'same-origin');
    assert.match(url, /^\/api\/art\/[a-f0-9]{64}\.json$/);
    return artResponse(phone);
  } });
  ui.mount('phone'); await settleArt();
  assert.ok(ui.svg(), 'approved phone geometry renders from the catalog');
  const groups = ui.svg().children.filter(node => node.dataset.part);
  assert.deepEqual(groups.map(node => node.dataset.part).sort(), ['buttons', 'screen', 'speaker']);
  for (const group of groups) {
    const item = definition.parts.find(row => row.id === group.dataset.part);
    group.dispatch('click'); assert.equal(ui.selected.at(-1), item.query);
  }
});

test('a missing registry stays empty and filled data without parts has no empty workspace', async () => {
  const ui = await registry({ art: false });
  assert.equal(ui.api.configure({ version: 1, archetypes: [], models: [] }), true);
  assert.equal(ui.api.resolve('bike'), null);
  assert.equal(ui.mount('bike'), null);
  assert.equal(ui.container.hidden, true);
  assert.equal(ui.api.configure({ version: 1, archetypes: [{ id: 'vessel', label: 'Vessel', aliases: ['pot'], guideCount: 2, parts: [], artUrl: null }], models: [] }), true);
  assert.equal(ui.api.resolve('pot').id, 'vessel');
  assert.equal(ui.mount('pot'), null);
  assert.equal(ui.container.hidden, true);
});

test('unreviewed context-only vectors display without fake highlights and support art-only nouns', async () => {
  for (const parts of [pilotFixture().archetypes[0].parts, []]) {
    const pilot = pilotFixture(); pilot.archetypes[0].parts = parts;
    const art = phoneArt();
    art.context = Object.values(art.parts).flat(); art.parts = {};
    const ui = await registry({ pilot, fetchArt: async () => artResponse(art) });
    ui.mount('phone');
    if (!parts.length) assert.equal(ui.container.hidden, true, 'pending art reserves no empty box');
    await settleArt();
    assert.ok(ui.svg(), 'source geometry appears even without approved part mappings');
    assert.equal(ui.container.hidden, false);
    assert.equal(ui.svg().children.filter(node => node.dataset.part).length, 0);
    if (parts.length) {
      ui.part('battery', 'button').dispatch('click');
      assert.deepEqual(ui.selected, ['battery'], 'unmapped part list remains usable');
    }
  }
});

test('model names resolve literally while release replacement clears stale aliases', async () => {
  const ui = await registry({ art: false });
  assert.equal(ui.api.resolve('MacBook A2179').id, 'laptop');
  assert.equal(ui.api.resolve('SM-G991B').id, 'phone');
  assert.equal(ui.api.resolve('SMG991B'), null);
  assert.equal(ui.api.resolve('SM-G991C'), null);
  ui.api.configure({ version: 1, archetypes: [], models: [] });
  assert.equal(ui.api.resolve('SM-G991B'), null);
  assert.equal(ui.api.nouns.length, 0);
});

test('release noun IDs and exact punctuated model names use the same lazy artwork path', async () => {
  const nounId = 'oewn-fixture-smartphone-n';
  const art = { ...phoneArt(), id: nounId };
  const url = artURL(art);
  const requests = [];
  const ui = await registry({ art: false, fetchArt: async (address, options) => {
    requests.push({ address, options }); return artResponse(art);
  } });
  ui.api.configure({ version: 1, revision: 'release', archetypes: [{
    id: nounId, label: 'Smartphone', aliases: ['smartphone'], guideCount: 4,
    artUrl: url, parts: [{ id: 'screen', label: 'Screen', query: 'screen', aliases: ['display'] }],
  }], models: [{ name: 'SM-G991B', nounId }] });
  assert.equal(ui.api.resolve('SM-G991B').id, nounId);
  assert.equal(ui.api.resolve('SMG991B'), null);
  ui.api.prefetch('SM-G991B');
  await settleArt();
  ui.mount('SM-G991B');
  assert.ok(ui.svg(), 'the art ID follows the release noun, without a canonical-name branch');
  assert.equal(requests.length, 1, 'literal model prefetch is reused by the mount');
  assert.equal(requests[0].address, url);
  assert.equal(requests[0].options.mode, 'same-origin');
  ui.part('screen', 'g').dispatch('click');
  assert.deepEqual(ui.selected, ['screen']);
});

test('build-time sense ranks resolve polysemous aliases without object-specific browser rules', async () => {
  const ui = await registry({ art: false });
  const row = (id, label, alias, rank, guideCount = 1) => ({
    id, label, aliases: [alias], aliasRanks: { [alias]: rank }, guideCount, parts: [], artUrl: null,
  });
  const payload = { version: 1, archetypes: [
    row('motorcycle', 'Motorcycle', 'bike', 0, 90),
    row('weapon', 'Weapon', 'arm', 1, 100),
    row('phoneme', 'Phoneme', 'phone', 1, 100),
    row('bicycle', 'Bicycle', 'bike', -1, 5),
    row('body-arm', 'Arm', 'arm', 0, 5),
    row('telephone', 'Telephone', 'phone', 0, 5),
  ], models: [{ name: 'SM-G991B', nounId: 'telephone' }] };
  for (const order of [payload.archetypes, [...payload.archetypes].reverse()]) {
    ui.api.configure({ ...payload, archetypes: order });
    assert.equal(ui.api.resolve('bike').id, 'bicycle', 'reviewed alias priority can override lexical polysemy');
    assert.equal(ui.api.resolve('arm').id, 'body-arm', 'the first lexical sense beats a more numerous niche sense');
    assert.equal(ui.api.resolve('phone').id, 'telephone');
    assert.equal(ui.api.resolve('Bike!').id, 'bicycle', 'punctuation-normalized lookup uses the same sense winner');
    assert.equal(ui.api.resolve('SM-G991B').id, 'telephone');
    assert.equal(ui.api.resolve('SMG991B'), null, 'model punctuation remains literal');
  }
});

test('equal sense ranks prefer covered guides then a stable noun ID, while absent ranks retain order', async () => {
  const ui = await registry({ art: false });
  const row = (id, guideCount, rank) => ({ id, label: id, aliases: ['shared'], guideCount, parts: [], artUrl: null,
    ...(rank === undefined ? {} : { aliasRanks: { shared: rank } }) });
  const definitions = [row('zeta', 2, 0), row('beta', 4, 0), row('alpha', 4, 0)];
  for (const archetypes of [definitions, [...definitions].reverse()]) {
    ui.api.configure({ version: 1, archetypes, models: [] });
    assert.equal(ui.api.resolve('shared').id, 'alpha');
  }
  ui.api.configure({ version: 1, archetypes: [row('legacy-first', 1), row('legacy-more', 100)], models: [] });
  assert.equal(ui.api.resolve('shared').id, 'legacy-first');
});

test('configuration shares identical immutable lexical parts without mixing different aliases', async () => {
  const ui = await registry({ art: false });
  const shared = { id: 'panel', label: 'Panel', query: 'panel', aliases: ['display panel', 'panel'] };
  const row = (id, aliases) => ({ id, label: id, aliases: [id], guideCount: 1, artUrl: null,
    parts: [{ ...shared, aliases: [...aliases] }] });
  ui.api.configure({ version: 1, archetypes: [row('first', shared.aliases), row('second', shared.aliases),
    row('different', ['control panel'])], models: [] });
  const first = ui.api.resolve('first').parts[0];
  assert.equal(ui.api.resolve('second').parts[0], first, 'the same immutable lexical part is prepared once');
  assert.ok(Object.isFrozen(first));
  assert.ok(Object.isFrozen(first.aliases));
  assert.notEqual(ui.api.resolve('different').parts[0], first);
  assert.ok(!first.aliases.includes('control panel'), 'reuse never combines distinct parts or aliases');
});

test('ASCII preparation fast paths preserve Unicode aliases and literal model punctuation', async () => {
  const ui = await registry({ art: false });
  ui.api.configure({ version: 1, archetypes: [{ id: 'accented', label: 'Réfrigérateur', aliases: ['réfrigérateur'],
    guideCount: 1, parts: [], artUrl: null }], models: [{ name: 'É-1', nounId: 'accented' }] });
  assert.equal(ui.api.resolve('RÉFRIGÉRATEUR').id, 'accented');
  assert.equal(ui.api.resolve('refrigerateur').id, 'accented');
  assert.equal(ui.api.resolve('E-1').id, 'accented');
  assert.equal(ui.api.resolve('E1'), null);
});
