const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { makeDOM } = require('./hero_test_dom.cjs');

const source = fs.readFileSync(path.join(__dirname, '../assets/hero-styles.js'), 'utf8');
const controller = fs.readFileSync(path.join(__dirname, '../assets/hero.js'), 'utf8');
const repairs = ['stitched', 'taped', 'tooltype', 'blueprint', 'cutpaper'];
const originalThemes = ['pixel', 'editorial', 'comic', 'poster', 'typewriter', 'handwritten', 'toolbox'];

// Synthetic Range bounds exercise the real decoration/controller seam. This
// fixture has no CSS painter or font shaper and does not launch a browser.
function styleDOM({ theme = 'stitched', width = 1100 } = {}) {
  const metrics = { factor: 1, invalid: '', reads: 0, family: 'test font', cssReady: true };
  const dom = makeDOM({ theme, width, measureText: (text, size) => text.length * size * 0.42 * metrics.factor });
  const createElement = dom.document.createElement;
  dom.document.createElement = tag => {
    const node = createElement(tag);
    const replaceChildren = node.replaceChildren;
    node.replaceChildren = function (...children) { this.textContent = ''; replaceChildren.apply(this, children); };
    node.remove = function () {
      if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1);
      this.parentNode = null;
    };
    return node;
  };
  dom.document.createElementNS = (namespace, tag) => {
    assert.equal(namespace, 'http://www.w3.org/2000/svg');
    return dom.document.createElement(tag);
  };
  const textNode = { nodeType: 3, parentNode: dom.prefixMirror,
    get textContent() { return dom.prefixMirror.textContent; } };
  Object.defineProperty(dom.prefixMirror, 'firstChild', { get: () => textNode.textContent ? textNode : null });
  const getComputedStyle = element => {
    const size = element.style.fontSize || dom.hero.style.fontSize || '100px';
    return { fontSize: metrics.invalid === 'font' ? 'invalid' : size,
      lineHeight: `${parseFloat(size) * 1.2}px`, fontFamily: metrics.family,
      font: `${size} ${metrics.family}`, fontWeight: '400', letterSpacing: '0px',
      getPropertyValue: name => name === '--mend-art-ready' && metrics.cssReady ? '1' : '' };
  };
  dom.context.getComputedStyle = dom.window.getComputedStyle = getComputedStyle;
  dom.window.CSS = { supports: () => true };
  dom.document.createRange = () => {
    let start = 0, end = 0;
    return {
      selectNodeContents(node) { assert.equal(node, dom.prefixMirror); start = 0; end = node.textContent.length; },
      setStart(node, at) { assert.equal(node, textNode); start = at; },
      setEnd(node, at) { assert.equal(node, textNode); end = at; },
      getBoundingClientRect() {
        metrics.reads++;
        if (metrics.invalid === 'throws') throw new Error('Measurement unavailable');
        const size = parseFloat(getComputedStyle(dom.response).fontSize);
        const advance = text => dom.document.measureText(text, size);
        return { left: advance(textNode.textContent.slice(0, start)),
          width: metrics.invalid === 'width' ? 0 : advance(textNode.textContent.slice(start, end)),
          height: size, top: 0 };
      },
    };
  };
  dom.context = vm.createContext(dom.context);
  vm.runInContext(source, dom.context);
  dom.styles = dom.window.mendHeroStyles;
  dom.metrics = metrics;
  dom.art = () => dom.firstField.children.filter(node => node.className === 'hero-prompt-art');
  dom.native = () => ({ value: dom.response.value, start: dom.response.selectionStart,
    end: dom.response.selectionEnd, direction: dom.response.selectionDirection,
    focused: dom.document.activeElement });
  return dom;
}

function descendantText(node) {
  return node.textContent + node.children.map(descendantText).join('');
}

function walk(node) { return [node, ...node.children.flatMap(walk)]; }

test('the shared catalog contains twelve unique styles and retains canonical accessible prompts', () => {
  const dom = styleDOM();
  assert.deepEqual(Array.from(dom.styles.themes), [...originalThemes, ...repairs]);
  assert.equal(new Set(dom.styles.themes).size, 12);
  assert.equal(dom.styles.themes.includes('spareparts'), false);
  assert.equal(Object.isFrozen(dom.styles.themes), true);
  for (const theme of dom.styles.themes) {
    assert.equal(dom.styles.prompt(theme), theme === 'toolbox' ? '🙏, 🔧 my ' : 'please, mend my ');
  }
});

test('every repair style creates one inert decoration and preserves native focus, value, and selection', () => {
  for (const theme of repairs) {
    const dom = styleDOM({ theme });
    dom.prefixMirror.textContent = dom.styles.prompt(theme);
    dom.response.value = dom.prefixMirror.textContent + 'a long editable suffix '.repeat(5);
    dom.response.focus();
    dom.response.setSelectionRange(18, 29, 'backward');
    const original = dom.native();
    const mounted = dom.styles.mount(dom.firstField, dom.prefixMirror, theme);
    assert.ok(mounted, theme);
    mounted.sync();
    assert.equal(dom.firstField.classList.contains('has-prompt-art'), true, theme);
    assert.equal(dom.art().length, 1, theme);
    const art = dom.art()[0];
    assert.equal(art.inert, true);
    assert.equal(art.getAttribute('aria-hidden'), 'true');
    assert.equal(descendantText(art), 'please, mend my ', 'art does not duplicate accessible prompt words');
    assert.ok(walk(art).every(node => !['INPUT', 'TEXTAREA', 'BUTTON', 'A'].includes(node.tagName)));
    assert.deepEqual(dom.native(), original, theme);
    mounted.sync();
    assert.equal(dom.art()[0], art, 'same metrics retain the existing artwork');
    assert.equal(dom.art().length, 1);
    assert.deepEqual(dom.native(), original);
  }
});

test('real artwork follows fitted controller metrics and performs no glyph work while typing', async () => {
  const dom = styleDOM({ theme: 'cutpaper', width: 768 });
  vm.runInContext(controller, dom.context);
  await Promise.resolve();
  assert.equal(dom.art().length, 1);
  const initial = dom.art()[0];
  const reads = dom.metrics.reads;
  for (const suffix of ['b', 'broken', 'broken laptop', 'broken laptop screen']) {
    dom.response.value = dom.styles.prompt('cutpaper') + suffix;
    dom.event(dom.response, 'input', { inputType: 'insertText', bubbles: true });
  }
  assert.equal(dom.art()[0], initial);
  assert.equal(dom.metrics.reads, reads, 'keystrokes must not remeasure the fixed artwork');
  dom.response.setSelectionRange(dom.response.value.length, dom.response.value.length);
  const native = dom.native();
  dom.response.clientWidth = 320;
  dom.event(dom.window, 'resize');
  const resized = dom.art()[0];
  assert.ok(resized);
  assert.notEqual(resized, initial, 'resize updates the fitted artwork');
  assert.equal(dom.art().length, 1);
  assert.deepEqual(dom.native(), native, 'resize preserves the native caret');
  dom.metrics.factor = 1.15;
  dom.metrics.family = 'new font';
  dom.event(dom.document.fonts, 'loadingdone');
  assert.ok(dom.art()[0]);
  assert.notEqual(dom.art()[0], resized, 'late font metrics rebuild the artwork');
  assert.equal(dom.art().length, 1);
  assert.deepEqual(dom.native(), native);
  assert.equal(dom.window.mendHero.query(), 'broken laptop screen');
  assert.equal(dom.document.title, 'please, mend my broken laptop screen');
});

test('missing paint capabilities leave the native prefix visible without decoration', () => {
  for (const unavailable of ['CSS', 'supports', 'Range', 'SVG']) {
    const dom = styleDOM();
    dom.prefixMirror.textContent = dom.styles.prompt('stitched');
    dom.response.value = dom.prefixMirror.textContent + 'arm';
    if (unavailable === 'CSS') delete dom.window.CSS;
    if (unavailable === 'supports') dom.window.CSS.supports = () => false;
    if (unavailable === 'Range') delete dom.document.createRange;
    if (unavailable === 'SVG') delete dom.document.createElementNS;
    const native = dom.native();
    assert.equal(dom.styles.mount(dom.firstField, dom.prefixMirror, 'stitched'), null, unavailable);
    assert.equal(dom.firstField.classList.contains('has-prompt-art'), false, unavailable);
    assert.equal(dom.art().length, 0, unavailable);
    assert.deepEqual(dom.native(), native);
  }
  for (const theme of originalThemes) {
    const dom = styleDOM({ theme });
    assert.equal(dom.styles.mount(dom.firstField, dom.prefixMirror, theme), null, theme);
    assert.equal(dom.art().length, 0);
  }
});

test('failed measurements or a missing stylesheet restore native rendering', () => {
  for (const invalid of ['width', 'font', 'throws', 'stylesheet']) {
    const dom = styleDOM();
    dom.prefixMirror.textContent = dom.styles.prompt('stitched');
    dom.response.value = dom.prefixMirror.textContent + 'arm';
    const mounted = dom.styles.mount(dom.firstField, dom.prefixMirror, 'stitched');
    mounted.sync();
    assert.equal(dom.art().length, 1);
    const native = dom.native();
    dom.metrics.invalid = invalid;
    dom.metrics.cssReady = invalid !== 'stylesheet';
    mounted.sync();
    assert.equal(dom.art().length, 0, invalid);
    assert.equal(dom.firstField.classList.contains('has-prompt-art'), false, invalid);
    assert.deepEqual(dom.native(), native);
    dom.metrics.invalid = '';
    dom.metrics.cssReady = true;
    mounted.sync();
    assert.equal(dom.art().length, 1, 'valid metrics recover decoration');
    assert.deepEqual(dom.native(), native);
  }
});

test('all floater styles keep exactly one canonical prompt and create no editable or animated artifacts', () => {
  const dom = styleDOM();
  for (const theme of dom.styles.themes) {
    const node = dom.document.createElement('span');
    node.textContent = dom.styles.prompt(theme);
    dom.styles.decorateFloater(node, theme);
    dom.styles.decorateFloater(node, theme);
    assert.equal(descendantText(node), dom.styles.prompt(theme), theme);
    assert.ok(walk(node).every(child => !['INPUT', 'TEXTAREA'].includes(child.tagName)));
    if (repairs.includes(theme)) {
      assert.equal(node.children.length, 1);
      assert.equal(node.children[0].inert, true);
      assert.equal(node.children[0].getAttribute('aria-hidden'), 'true');
    }
  }
  assert.equal(dom.document.animations, 0);
});
