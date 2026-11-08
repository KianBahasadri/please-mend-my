const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// This fixture checks DOM/events and the real registry without opening a
// browser. It does not model CSS layout, SVG paint, or font measurements.
function registry() {
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
  const window = {};
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/specifiers.js'), 'utf8'),
    vm.createContext({ document, window }));
  const container = new Node('span');
  container.className = 'topic-specifier';
  const selected = [];
  const mount = subject => window.mendSpecifiers.mount(container, subject, value => selected.push(value));
  const walk = node => [node, ...node.children.flatMap(walk)];
  const part = (id, tag) => walk(container).find(node => node.dataset.part === id && (!tag || node.tagName === tag));
  return { api: window.mendSpecifiers, document, container, selected, scrolls, mount, walk, part };
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
  const svg = ui.container.children[0];
  assert.equal(svg.tagName, 'svg');
  assert.equal(svg.getAttribute('viewBox'), '0 0 360 220');
  const groups = svg.children.filter(node => node.tagName === 'g');
  assert.equal(groups.length, 11);
  assert.equal(new Set(groups.map(node => node.dataset.part)).size, 11);
  for (const group of groups) {
    assert.equal(group.getAttribute('role'), 'button');
    assert.equal(group.getAttribute('tabindex'), '0');
    assert.ok(group.getAttribute('aria-label'));
    const [geometry, hit] = group.children;
    assert.equal(geometry.getAttribute('stroke'), 'currentColor');
    assert.equal(geometry.getAttribute('fill'), 'none');
    assert.equal(hit.getAttribute('stroke'), 'transparent');
    assert.equal(hit.getAttribute('stroke-width'), '14');
    assert.equal(hit.getAttribute('pointer-events'), 'stroke');
    assert.equal(hit.getAttribute('d'), geometry.getAttribute('d'));
    assert.ok(hit.getAttribute('d').length > 10);
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
  assert.equal(ui.container.children[0].classList.contains('has-highlight'), false);
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
