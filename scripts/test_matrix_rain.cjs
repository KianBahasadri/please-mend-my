const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { makeDOM } = require('./hero_test_dom.cjs');

const source = fs.readFileSync(path.join(__dirname, '../assets/matrix-rain.js'), 'utf8');

function rainDOM({ theme = 'matrix', width = 1280, height = 800, reduced = false,
  lightMode = false, pixelRatio = 2, glyphCost = 0 } = {}) {
  const dom = makeDOM({ theme, width });
  const media = new Map();
  const calls = { glyphs: [], clears: 0, transforms: [] };
  let cost = 0;
  const canvasContext = {
    clearRect() { calls.clears++; },
    fillText(...args) { calls.glyphs.push({ args, ink: this.fillStyle }); cost += glyphCost; },
    setTransform(...args) { calls.transforms.push(args); },
  };
  const create = dom.document.createElement;
  dom.document.createElement = tag => {
    const node = create(tag);
    if (tag === 'canvas') node.getContext = () => canvasContext;
    return node;
  };
  dom.window.matchMedia = query => {
    if (!media.has(query)) {
      const listeners = [];
      const record = { matches: query.includes('reduced-motion') ? reduced : query.includes('color-scheme') ? lightMode : false,
        addEventListener(type, listener) { assert.equal(type, 'change'); listeners.push(listener); },
        change(value) { record.matches = value; for (const listener of listeners) listener(); } };
      media.set(query, record);
    }
    return media.get(query);
  };
  dom.window.innerHeight = height;
  dom.window.devicePixelRatio = pixelRatio;
  dom.window.navigator = { scheduling: { isInputPending: () => calls.inputPending || false } };
  const originalNow = dom.context.performance.now;
  dom.context.performance.now = () => originalNow() + cost;
  dom.context.Math = Object.create(Math);
  dom.context.Math.random = () => 0.5;
  vm.runInContext(source, vm.createContext(dom.context));
  return Object.assign(dom, { calls, media,
    canvas: () => dom.document.querySelector('canvas'),
    frame(ms = 17) { cost = 0; dom.advance(ms); },
  });
}

test('non-Matrix themes allocate no canvas or animation work', () => {
  const dom = rainDOM({ theme: 'rainbow' });
  assert.equal(dom.canvas(), null);
  dom.advance(5000);
  assert.equal(dom.calls.clears, 0);
  assert.equal(dom.media.size, 0);
});

test('Matrix canvas is inert and caps backing pixels, glyphs and high-refresh work', () => {
  const dom = rainDOM({ width: 8000, height: 8000, pixelRatio: 4 });
  const canvas = dom.canvas();
  assert.ok(canvas.width * canvas.height <= 1_500_000);
  assert.equal(canvas.getAttribute('aria-hidden'), 'true');
  assert.equal(canvas.inert, true);
  dom.response.value = 'please, mend my laptop';
  dom.response.focus();
  dom.response.setSelectionRange(15, 21, 'backward');
  dom.frame(17);
  const first = dom.calls.glyphs.length;
  assert.ok(first > 0 && first <= 336);
  dom.frame(8);
  assert.equal(dom.calls.glyphs.length, first, '120Hz refresh does not double the drawing rate');
  dom.frame(9);
  assert.ok(dom.calls.glyphs.length - first <= 336);
  assert.equal(dom.document.activeElement, dom.response);
  assert.equal(dom.response.selectionStart, 15);
  assert.equal(dom.response.selectionEnd, 21);
  assert.equal(dom.response.selectionDirection, 'backward');
});

test('hidden, reduced motion and guide reading stop painting and resume without catch-up', async () => {
  const dom = rainDOM();
  dom.frame();
  const count = () => dom.calls.glyphs.length;
  let before = count();
  dom.document.visibilityState = 'hidden';
  dom.event(dom.document, 'visibilitychange');
  dom.frame(5000);
  assert.equal(count(), before);
  dom.document.visibilityState = 'visible';
  dom.event(dom.document, 'visibilitychange');
  dom.frame();
  assert.ok(count() > before && count() - before <= 336);
  const motion = dom.media.get('(prefers-reduced-motion: reduce)');
  motion.change(true); before = count();
  dom.frame(5000);
  assert.equal(count(), before);
  motion.change(false); dom.frame();
  assert.ok(count() > before);
  dom.document.body.classList.add('reading');
  await Promise.resolve(); before = count();
  dom.frame(5000);
  assert.equal(count(), before);
  dom.document.body.classList.remove('reading');
  await Promise.resolve(); dom.frame();
  assert.ok(count() > before);
});

test('reduced motion and forced colors never paint falling code', () => {
  const dom = rainDOM({ reduced: true });
  dom.frame(1000);
  assert.equal(dom.calls.glyphs.length, 0);
  dom.media.get('(prefers-reduced-motion: reduce)').change(false);
  dom.media.get('(forced-colors: active)').change(true);
  dom.frame(1000);
  assert.equal(dom.calls.glyphs.length, 0);
});

test('Matrix yields to pending input and limits work on a slow drawing context', () => {
  const pending = rainDOM();
  pending.calls.inputPending = true;
  pending.frame();
  assert.equal(pending.calls.glyphs.length, 0);
  pending.calls.inputPending = false;
  pending.frame();
  assert.ok(pending.calls.glyphs.length > 0);
  const slow = rainDOM({ glyphCost: 0.35 });
  slow.frame();
  assert.ok(slow.calls.glyphs.length > 0 && slow.calls.glyphs.length <= 8,
    'the ~2ms budget stops the frame instead of painting all 336 glyphs');
});

test('theme changes recolor live rain and page lifecycle pauses it', () => {
  const dom = rainDOM();
  dom.frame();
  const dark = dom.calls.glyphs.length;
  assert.match(dom.calls.glyphs.at(-1).ink, /^rgba\(110,245,160,/);
  dom.media.get('(prefers-color-scheme: light)').change(true);
  dom.frame();
  assert.ok(dom.calls.glyphs.length > dark);
  assert.match(dom.calls.glyphs.at(-1).ink, /^rgba\(12,100,52,/);
  dom.event(dom.window, 'pagehide');
  const count = dom.calls.glyphs.length;
  dom.frame(3000);
  assert.equal(dom.calls.glyphs.length, count);
  dom.event(dom.window, 'pageshow');
  dom.frame(); dom.frame();
  assert.ok(dom.calls.glyphs.length > count);
});
