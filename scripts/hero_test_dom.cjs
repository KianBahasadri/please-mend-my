// Small native-textarea/event fixture for controller tests. It deliberately
// provides no HTML renderer, so tests do not claim real font/layout coverage.
class TestEvent {
  constructor(type, options = {}) {
    Object.assign(this, { type, bubbles: false, cancelable: true, defaultPrevented: false,
      isComposing: false, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
      button: 0, key: '', inputType: '', data: null }, options);
  }
  preventDefault() { if (this.cancelable) this.defaultPrevented = true; }
  stopPropagation() { this.stopped = true; }
  stopImmediatePropagation() { this.stopped = this.immediateStopped = true; }
}

class EventTarget {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, listener, options = {}) {
    const list = this.listeners.get(type) || [];
    list.push({ listener, capture: options === true || Boolean(options.capture), once: Boolean(options.once) });
    this.listeners.set(type, list);
  }
  removeEventListener(type, listener) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter(item => item.listener !== listener));
  }
  dispatchEvent(event) {
    event.target ||= this;
    const ancestors = [];
    for (let parent = this.parentNode; parent; parent = parent.parentNode) ancestors.push(parent);
    const call = (target, capture) => {
      event.currentTarget = target;
      for (const item of [...(target.listeners.get(event.type) || [])]) {
        if (item.capture !== capture) continue;
        typeof item.listener === 'function' ? item.listener.call(target, event) : item.listener.handleEvent(event);
        if (item.once) target.removeEventListener(event.type, item.listener);
        if (event.immediateStopped) break;
      }
    };
    for (const target of [...ancestors].reverse()) { call(target, true); if (event.stopped) break; }
    if (!event.stopped) { call(this, true); call(this, false); }
    if (event.bubbles && !event.stopped) {
      for (const target of ancestors) { call(target, false); if (event.stopped) break; }
    }
    return !event.defaultPrevented;
  }
}

class ClassList {
  constructor(changed = () => {}) { this.values = new Set(); this.changed = changed; }
  contains(value) { return this.values.has(value); }
  add(...values) { values.forEach(value => this.values.add(value)); this.changed(); }
  remove(...values) { values.forEach(value => this.values.delete(value)); this.changed(); }
  toggle(value, force) {
    const active = force === undefined ? !this.contains(value) : force;
    active ? this.add(value) : this.remove(value);
    return active;
  }
}

class Element extends EventTarget {
  constructor(tag, document) {
    super();
    this.tagName = tag.toUpperCase();
    this.ownerDocument = document;
    this.children = [];
    this.attributes = new Map();
    this.dataset = {};
    this.classList = new ClassList(() => document.notifyMutation?.(this, 'class'));
    this.style = { setProperty(name, value) { this[name] = String(value); },
      removeProperty(name) { delete this[name]; } };
    this._value = '';
    this.textContent = '';
    this.selectionStart = this.selectionEnd = 0;
    this.selectionDirection = 'none';
    this.hidden = false;
    this.inert = false;
    this.disabled = false;
    this.clientWidth = document.width;
    this.scrollTop = this.scrollLeft = 0;
  }
  set value(value) {
    this._value = String(value);
    this.selectionStart = this.selectionEnd = this._value.length;
  }
  get value() { return this._value; }
  get scrollHeight() { return Math.max(1, Math.ceil(this.value.length / 40)) * 80; }
  get offsetHeight() { return this.scrollHeight; }
  get offsetWidth() { return this.clientWidth; }
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === 'id') this.id = String(value);
    if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = String(value);
  }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  hasAttribute(name) { return this.attributes.has(name); }
  removeAttribute(name) { this.attributes.delete(name); }
  toggleAttribute(name, force) {
    const active = force === undefined ? !this.hasAttribute(name) : force;
    active ? this.setAttribute(name, '') : this.removeAttribute(name);
    return active;
  }
  append(...children) { for (const child of children) { child.parentNode = this; this.children.push(child); } }
  appendChild(child) { this.append(child); return child; }
  replaceChildren(...children) { for (const child of this.children) child.parentNode = null; this.children = []; this.append(...children); }
  contains(target) { return target === this || this.children.some(child => child.contains?.(target)); }
  matches(selector) {
    return selector.split(',').some(part => {
      part = part.trim();
      if (part.startsWith('#')) return this.id === part.slice(1);
      if (part.startsWith('.')) return this.classList.contains(part.slice(1));
      if (part.startsWith('[')) return this.hasAttribute(part.slice(1, -1).split('=')[0]);
      return this.tagName.toLowerCase() === part;
    });
  }
  closest(selector) {
    for (let node = this; node?.matches; node = node.parentNode) if (node.matches(selector)) return node;
    return null;
  }
  querySelectorAll(selector) {
    return this.children.flatMap(child => [...(child.matches?.(selector) ? [child] : []), ...(child.querySelectorAll?.(selector) || [])]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  getBoundingClientRect() {
    return { top: 0, left: 0, width: this.id?.includes('measure') ? this.textContent.length * 42 : this.clientWidth,
      height: this.scrollHeight, bottom: this.scrollHeight, right: this.clientWidth };
  }
  setSelectionRange(start, end, direction = 'none') {
    this.selectionStart = Math.min(this.value.length, Math.max(0, start));
    this.selectionEnd = Math.min(this.value.length, Math.max(this.selectionStart, end));
    this.selectionDirection = direction;
  }
  setRangeText(value, start = this.selectionStart, end = this.selectionEnd, mode = 'preserve') {
    this._value = this.value.slice(0, start) + value + this.value.slice(end);
    const at = mode === 'start' ? start : start + value.length;
    this.setSelectionRange(at, at);
  }
  focus() {
    const document = this.ownerDocument;
    if (document.activeElement === this) return;
    const previous = document.activeElement;
    document.activeElement = this;
    previous?.dispatchEvent(new TestEvent('blur', { relatedTarget: this }));
    previous?.dispatchEvent(new TestEvent('focusout', { relatedTarget: this, bubbles: true }));
    this.dispatchEvent(new TestEvent('focus', { relatedTarget: previous }));
    this.dispatchEvent(new TestEvent('focusin', { relatedTarget: previous, bubbles: true }));
  }
  blur() {
    if (this.ownerDocument.activeElement === this) {
      this.ownerDocument.activeElement = this.ownerDocument.body;
      this.dispatchEvent(new TestEvent('blur', { relatedTarget: null }));
    }
  }
  getAnimations() { return []; }
  animate() {
    this.ownerDocument.animations++;
    return { cancel() {}, finish() {}, finished: Promise.resolve(), playState: 'finished' };
  }
}

function makeDOM({ theme = 'editorial', reducedMotion = false, width = 1100 } = {}) {
  const timers = new Map(), frames = new Map();
  let clock = 0, nextId = 1;
  const window = new EventTarget();
  const document = new EventTarget();
  const observers = new Set();
  document.notifyMutation = (target, attributeName) => {
    for (const observer of observers) {
      if (observer.target === target && observer.options.attributes &&
          (!observer.options.attributeFilter || observer.options.attributeFilter.includes(attributeName))) {
        queueMicrotask(() => observer.callback([{ target, attributeName, type: 'attributes' }]));
      }
    }
  };
  document.width = width;
  document.animations = 0;
  document.parentNode = window;
  document.documentElement = new Element('html', document);
  document.documentElement.dataset.theme = theme;
  document.documentElement.parentNode = document;
  document.body = new Element('body', document);
  document.documentElement.append(document.body);
  document.activeElement = document.body;
  document.visibilityState = 'visible';
  document.hasFocus = () => true;
  document.fonts = Object.assign(new EventTarget(), { ready: Promise.resolve(), check: () => true });
  document.createElement = tag => new Element(tag, document);
  document.getElementById = id => document.documentElement.querySelector('#' + id);
  document.querySelector = selector => document.documentElement.querySelector(selector);
  document.querySelectorAll = selector => document.documentElement.querySelectorAll(selector);
  const add = (tag, id, parent = document.body) => {
    const element = new Element(tag, document);
    element.id = id;
    parent.append(element);
    return element;
  };
  const area = add('section', 'hero-area'); area.classList.add('hero-area');
  const hero = add('h1', 'hero-title', area);
  const firstMirror = add('span', 'hero-underline', hero);
  const prefixMirror = add('span', 'hero-prefix', firstMirror);
  const suffixMirror = add('span', 'hero-suffix', firstMirror);
  const response = add('textarea', 'response', hero);
  const measure = add('span', 'measure', hero);
  const line = add('span', 'clarification-line', hero);
  line.inert = true;
  line.setAttribute('aria-hidden', 'true');
  const secondHero = add('span', 'clarification-hero', line);
  const question = add('span', 'clarification-question', secondHero);
  const answerMirror = add('span', 'clarification-underline', secondHero);
  const clarification = add('textarea', 'clarification-response', secondHero);
  const secondMeasure = add('span', 'clarification-measure', secondHero);
  const media = Object.assign(new EventTarget(), { matches: reducedMotion, media: '(prefers-reduced-motion: reduce)' });
  const setTimeout = (callback, delay = 0) => {
    const id = nextId++; timers.set(id, { callback, due: clock + Number(delay) }); return id;
  };
  const requestAnimationFrame = callback => { const id = nextId++; frames.set(id, callback); return id; };
  Object.assign(window, { document, innerWidth: width, innerHeight: 800, matchMedia: () => media,
    setTimeout, clearTimeout: id => timers.delete(id), requestAnimationFrame,
    cancelAnimationFrame: id => frames.delete(id), Event: TestEvent, InputEvent: TestEvent,
    CustomEvent: TestEvent, scrollTo() {}, getSelection: () => ({ removeAllRanges() {} }) });
  const getComputedStyle = element => ({ fontSize: element.style.fontSize || '100px', lineHeight: '108px',
    transitionDuration: reducedMotion ? '0s' : '0.18s', getPropertyValue: name => element.style[name] || '' });
  window.getComputedStyle = getComputedStyle;
  const context = { window, document, Event: TestEvent, InputEvent: TestEvent, CustomEvent: TestEvent,
    KeyboardEvent: TestEvent, PointerEvent: TestEvent, queueMicrotask, getComputedStyle,
    requestAnimationFrame, cancelAnimationFrame: window.cancelAnimationFrame, setTimeout,
    clearTimeout: window.clearTimeout, matchMedia: window.matchMedia, innerWidth: width,
    performance: { now: () => clock }, console,
    ResizeObserver: class { observe() {} disconnect() {} },
    MutationObserver: class {
      constructor(callback) { this.callback = callback; }
      observe(target, options) { this.target = target; this.options = options; observers.add(this); }
      disconnect() { observers.delete(this); }
    },
  };
  const flushFrames = () => {
    const pending = [...frames]; frames.clear();
    for (const [, callback] of pending) callback(clock);
  };
  const advance = milliseconds => {
    const end = clock + milliseconds;
    for (let loops = 0; loops < 1000; loops++) {
      const next = [...timers].sort((a, b) => a[1].due - b[1].due)[0];
      if (!next || next[1].due > end) break;
      clock = next[1].due; timers.delete(next[0]); next[1].callback(); flushFrames();
    }
    clock = end; flushFrames();
  };
  return { context, window, document, response, clarification, question, line, hero, measure, secondMeasure,
    prefixMirror, suffixMirror, answerMirror,
    add, event: (target, type, options) => { const event = new TestEvent(type, options); target.dispatchEvent(event); return event; },
    advance, flushFrames };
}

module.exports = { makeDOM, TestEvent };
