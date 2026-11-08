const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { makeDOM } = require('./hero_test_dom.cjs');
const themes = ['pixel', 'editorial', 'comic', 'poster', 'typewriter', 'handwritten', 'toolbox',
  'stitched', 'taped', 'tooltype', 'blueprint', 'cutpaper'];

// Run the shipped controller, not a duplicate of its state machine. The fixture
// models native textarea selection/events; font metrics and CSS paint are not
// available here, and no browser is opened by these tests.
async function heroFor(options = {}) {
  const dom = makeDOM(options);
  options.setup?.(dom);
  dom.queries = [];
  dom.window.addEventListener('mend:querychange', () => dom.queries.push(dom.window.mendHero.query()));
  const context = vm.createContext(dom.context);
  if (options.specifiers) {
    for (const file of ['bicycle-art.js', 'specifiers.js']) {
      vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/' + file), 'utf8'), context);
    }
  }
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/hero.js'), 'utf8'), context);
  await Promise.resolve();
  dom.flushFrames();
  dom.prefix = input => input.value.slice(0, Number(input.dataset.promptLength));
  dom.suffix = input => input.value.slice(Number(input.dataset.promptLength));
  dom.type = (input, suffix, options = {}) => {
    input.value = dom.prefix(input) + suffix;
    input.setSelectionRange(input.value.length, input.value.length);
    dom.event(input, 'input', { bubbles: true, inputType: 'insertText', ...options });
  };
  dom.key = (input, key, options = {}) => dom.event(input, 'keydown', { key, bubbles: true, ...options });
  dom.insert = (input, text, options = {}) => {
    const before = dom.event(input, 'beforeinput', { inputType: 'insertText', data: text, bubbles: true, ...options });
    if (!before.defaultPrevented) {
      assert.equal(dom.document.activeElement, input, 'native insertion must finish before focus moves');
      input.setRangeText(text, input.selectionStart, input.selectionEnd, 'end');
      dom.event(input, 'input', { inputType: 'insertText', data: text, bubbles: true, ...options });
    }
    return before;
  };
  dom.visible = () => dom.line.getAttribute('aria-hidden') !== 'true' && !dom.line.inert;
  dom.reveal = noun => { dom.type(dom.response, noun); dom.advance(380); assert.equal(dom.visible(), true); };
  return dom;
}

test('a bare noun waits 380ms then asks a separate question and focuses a blank answer', async () => {
  const dom = await heroFor();
  assert.equal(dom.window.mendHero.query(), '');
  assert.equal(dom.visible(), false);
  dom.type(dom.response, 'arm');
  dom.advance(379);
  assert.equal(dom.visible(), false);
  dom.advance(1);
  assert.equal(dom.visible(), true);
  assert.equal(dom.response.value, dom.prefix(dom.response) + 'arm');
  assert.equal(dom.question.textContent, "What's wrong with your arm?");
  assert.equal(dom.clarification.getAttribute('aria-label'), dom.question.textContent);
  assert.equal(dom.prefixMirror.textContent, dom.prefix(dom.response));
  assert.equal(dom.clarification.value, '');
  assert.equal(dom.prefix(dom.clarification), '');
  assert.equal(dom.document.activeElement, dom.clarification);
  assert.equal(dom.clarification.selectionStart, 0);
  assert.equal(dom.clarification.selectionEnd, 0);
  assert.equal(dom.suffixMirror.textContent, 'arm');
  assert.equal(dom.answerMirror.textContent, '');
  assert.equal(dom.window.mendHero.query(), 'arm');
  dom.type(dom.clarification, 'hurts');
  assert.equal(dom.clarification.value, 'hurts');
  assert.equal(dom.answerMirror.textContent, 'hurts');
  assert.equal(dom.window.mendHero.query(), 'arm hurts');
  assert.equal(dom.queries.at(-1), 'arm hurts');
});

test('every prepared specifier alias appears at zero clock without moving the first caret', async () => {
  const dom = await heroFor({ specifiers: true });
  for (const noun of [...dom.window.mendSpecifiers.nouns, 'e-bike']) {
    const definition = dom.window.mendSpecifiers.resolve(noun);
    dom.type(dom.response, noun);
    assert.equal(dom.visible(), true, noun);
    assert.equal(dom.question.textContent, `What's wrong with your ${noun}?`, noun);
    assert.equal(dom.specifierContainer.hidden, false, noun);
    assert.equal(dom.specifierContainer.querySelectorAll('.specifier-option').length, definition.parts.length, noun);
    assert.equal(Boolean(dom.specifierContainer.querySelector('svg')), Boolean(definition.svg), noun);
    assert.equal(dom.document.activeElement, dom.response, 'instant narrowing never redirects ongoing typing');
    assert.equal(dom.response.selectionStart, dom.response.value.length, noun);
    assert.equal(dom.response.selectionEnd, dom.response.value.length, noun);
    assert.equal(dom.clarification.value, '');
    assert.equal(dom.window.mendHero.query(), noun);
  }
  dom.type(dom.response, 'bike');
  dom.advance(379); assert.equal(dom.document.activeElement, dom.response);
  dom.advance(1); assert.equal(dom.document.activeElement, dom.clarification);
});

test('instant specifiers preserve native first-field selection and explicit focus controls', async () => {
  const dom = await heroFor({ specifiers: true });
  const prefix = dom.prefix(dom.response);
  dom.response.value = prefix + 'bike';
  dom.response.setSelectionRange(prefix.length + 1, prefix.length + 3);
  dom.event(dom.response, 'input', { bubbles: true, inputType: 'insertFromPaste' });
  assert.equal(dom.visible(), true);
  assert.equal(dom.document.activeElement, dom.response);
  assert.equal(dom.response.selectionStart, prefix.length + 1);
  assert.equal(dom.response.selectionEnd, prefix.length + 3);
  dom.key(dom.response, 'a', { ctrlKey: true });
  assert.equal(dom.response.selectionStart, prefix.length);
  assert.equal(dom.response.selectionEnd, dom.response.value.length);
  for (const shortcut of ['Enter', 'Tab', '?', '.', '!']) {
    dom.response.focus(); dom.type(dom.response, 'bike');
    assert.equal(dom.document.activeElement, dom.response);
    if (shortcut.length === 1) dom.insert(dom.response, shortcut);
    else dom.key(dom.response, shortcut);
    assert.equal(dom.document.activeElement, dom.clarification, shortcut);
    assert.equal(dom.clarification.value, '');
    assert.equal(dom.question.textContent, "What's wrong with your bike?");
    assert.equal(dom.suffix(dom.response), 'bike' + (shortcut.length === 1 ? shortcut : ''));
  }
});

test('continuing a recognized prefix or full phrase replaces instant controls without hijacking typing', async () => {
  const dom = await heroFor({ specifiers: true });
  for (const sequence of [
    ['c', 'ca', 'car', 'carp', 'carpet'], ['b', 'bi', 'bik', 'bike', 'bikes'],
    ['p', 'ph', 'pho', 'phon', 'phone', 'phones'], ['phone', 'headphones'],
  ]) {
    dom.response.focus();
    for (const text of sequence) {
      dom.type(dom.response, text);
      const prepared = Boolean(dom.window.mendSpecifiers.resolve(text));
      assert.equal(!dom.specifierContainer.hidden, prepared, text);
      assert.equal(dom.visible(), prepared, text);
      assert.equal(dom.document.activeElement, dom.response, text);
      assert.equal(dom.suffix(dom.response), text);
    }
  }
  for (const text of ['broken bike', 'bike chain', 'my arm hurts', 'laptop not charging',
    'phone screen cracked', 'car not starting', 'jeans torn', 'notebook battery', '']) {
    dom.type(dom.response, text, { inputType: 'insertFromPaste' });
    assert.equal(dom.visible(), false, text);
    assert.equal(dom.specifierContainer.hidden, true, text);
    assert.equal(dom.specifierContainer.children.length, 0, 'leaving a subject destroys stale controls');
    dom.advance(2000);
    assert.equal(dom.visible(), false, text);
    assert.equal(dom.document.activeElement, dom.response);
  }
});

test('prepared controls wait for IME commit and never open while hidden, reading or suppressed', async () => {
  const dom = await heroFor({ specifiers: true });
  dom.event(dom.response, 'compositionstart');
  dom.type(dom.response, 'bike', { inputType: 'insertCompositionText', isComposing: true });
  dom.advance(2000);
  assert.equal(dom.visible(), false);
  dom.event(dom.response, 'compositionend');
  assert.equal(dom.visible(), true, 'committed text mounts prepared controls without a timer');
  assert.equal(dom.document.activeElement, dom.response);
  dom.key(dom.response, 'Enter');
  dom.key(dom.clarification, 'Backspace');
  dom.type(dom.response, 'bike'); dom.advance(2000);
  assert.equal(dom.visible(), false, 'Backspace suppression includes the instant path');
  dom.key(dom.response, 'Tab');
  assert.equal(dom.visible(), true, 'explicit completion can reopen a suppressed subject');
  assert.equal(dom.document.activeElement, dom.clarification);
  for (const mode of ['hidden', 'reading']) {
    const blocked = await heroFor({ specifiers: true });
    if (mode === 'hidden') blocked.document.visibilityState = 'hidden';
    else blocked.document.body.classList.add('reading');
    blocked.type(blocked.response, 'laptop'); blocked.advance(2000);
    assert.equal(blocked.visible(), false, mode);
    assert.equal(blocked.specifierContainer.hidden, true, mode);
  }
});

test('subjects without prepared controls retain their conservative automatic question pause', async () => {
  const dom = await heroFor({ specifiers: true });
  dom.type(dom.response, 'relationship');
  assert.equal(dom.visible(), false);
  dom.advance(379); assert.equal(dom.visible(), false);
  dom.advance(1);
  assert.equal(dom.question.textContent, "What's wrong with your relationship?");
  assert.equal(dom.specifierContainer.hidden, true, 'there is no empty SVG placeholder');
  assert.equal(dom.document.activeElement, dom.clarification);
});

test('the idle deadline resets and continuing a full phrase cancels clarification', async () => {
  const dom = await heroFor();
  dom.type(dom.response, 'arm');
  dom.advance(200);
  dom.type(dom.response, 'arms');
  dom.advance(379);
  assert.equal(dom.visible(), false);
  dom.advance(1);
  assert.equal(dom.visible(), true);
  dom.response.focus();
  dom.type(dom.response, 'car');
  dom.advance(379);
  assert.equal(dom.visible(), false);
  dom.type(dom.response, 'arm hurts');
  dom.advance(20000);
  assert.equal(dom.visible(), false);
  assert.equal(dom.window.mendHero.query(), 'arm hurts');
  dom.type(dom.response, 'broken arm');
  for (const key of ['Enter', 'Tab']) {
    dom.key(dom.response, key);
    assert.equal(dom.visible(), false);
    assert.notEqual(dom.document.activeElement, dom.clarification);
  }
  dom.advance(20000);
  assert.equal(dom.visible(), false);
});

test('Enter and Tab immediately ask the question, focus its blank answer, and preserve Shift+Tab', async () => {
  for (const key of ['Enter', 'Tab']) {
    const dom = await heroFor();
    dom.type(dom.response, 'car');
    const event = dom.key(dom.response, key);
    assert.equal(event.defaultPrevented, true);
    assert.equal(dom.visible(), true);
    assert.equal(dom.document.activeElement, dom.clarification);
    assert.equal(dom.question.textContent, "What's wrong with your car?");
    assert.equal(dom.clarification.value, '');
    assert.equal(dom.clarification.selectionStart, 0);
    dom.type(dom.clarification, 'will not start');
    assert.equal(dom.window.mendHero.query(), 'car will not start');
    assert.equal(dom.queries.at(-1), 'car will not start');
    assert.equal(dom.document.activeElement, dom.clarification);
    dom.key(dom.clarification, 'Tab', { shiftKey: true });
    assert.equal(dom.document.activeElement, dom.response);
    assert.equal(dom.window.mendHero.query(), 'car will not start');
  }
});

test('desktop completion punctuation remains in the native first field before clarification receives focus', async () => {
  for (const noun of ['arm', 'harpsichord']) {
    for (const punctuation of ['?', '.', '!']) {
      const dom = await heroFor();
      dom.type(dom.response, noun);
      const event = dom.key(dom.response, punctuation, { shiftKey: punctuation !== '.' });
      assert.equal(event.defaultPrevented, false);
      assert.equal(dom.visible(), false);
      assert.equal(dom.document.activeElement, dom.response);
      assert.equal(dom.insert(dom.response, punctuation).defaultPrevented, false);
      assert.equal(dom.visible(), true);
      assert.equal(dom.document.activeElement, dom.clarification);
      assert.equal(dom.question.textContent, `What's wrong with your ${noun}?`);
      assert.equal(dom.suffix(dom.response), noun + punctuation);
      assert.equal(dom.suffixMirror.textContent, noun + punctuation);
      assert.equal(dom.document.title, dom.prefix(dom.response) + noun + punctuation);
      assert.equal(dom.clarification.value, '');
      assert.equal(dom.clarification.selectionStart, 0);
      assert.equal(dom.window.mendHero.query(), noun + punctuation);
      dom.advance(10000);
      assert.equal(dom.document.activeElement, dom.clarification);
      assert.deepEqual(dom.queries, [noun, noun + punctuation]);
    }
  }
});

test('mobile completion punctuation remains in the first field and clarifies once', async () => {
  for (const punctuation of ['?', '.', '!']) {
    const dom = await heroFor();
    dom.type(dom.response, 'car');
    const mobile = dom.insert(dom.response, punctuation);
    assert.equal(mobile.defaultPrevented, false);
    assert.equal(dom.visible(), true);
    assert.equal(dom.document.activeElement, dom.clarification);
    assert.equal(dom.question.textContent, "What's wrong with your car?");
    assert.equal(dom.window.mendHero.query(), 'car' + punctuation);
    assert.equal(dom.suffix(dom.response), 'car' + punctuation);
    assert.equal(dom.suffixMirror.textContent, 'car' + punctuation);
    assert.equal(dom.document.title, dom.prefix(dom.response) + 'car' + punctuation);
    assert.equal(dom.clarification.value, '');
    assert.deepEqual(dom.queries, ['car', 'car' + punctuation]);
  }
});

test('retained punctuation remains editable and undo/redo never steals the first caret', async () => {
  for (const punctuation of ['?', '.', '!']) {
    const dom = await heroFor();
    dom.type(dom.response, 'arm');
    dom.insert(dom.response, punctuation);
    dom.response.focus();
    const beforeDelete = dom.event(dom.response, 'beforeinput', { inputType: 'deleteContentBackward', bubbles: true });
    assert.equal(beforeDelete.defaultPrevented, false);
    dom.type(dom.response, 'arm', { inputType: 'deleteContentBackward' });
    assert.equal(dom.document.activeElement, dom.response);
    assert.equal(dom.window.mendHero.query(), 'arm');
    for (const [inputType, text] of [['historyUndo', 'arm' + punctuation], ['historyRedo', 'arm']]) {
      const before = dom.event(dom.response, 'beforeinput', { inputType, bubbles: true });
      assert.equal(before.defaultPrevented, false, 'native history owns punctuation edits');
      dom.type(dom.response, text, { inputType });
      assert.equal(dom.document.activeElement, dom.response);
      assert.equal(dom.window.mendHero.query(), text);
      assert.equal(dom.suffixMirror.textContent, text);
      assert.equal(dom.document.title, dom.prefix(dom.response) + text);
    }
    dom.type(dom.response, 'arm' + punctuation);
    dom.response.setSelectionRange(dom.response.value.length - 1, dom.response.value.length);
    dom.insert(dom.response, ',');
    assert.equal(dom.suffix(dom.response), 'arm,');
    assert.equal(dom.document.activeElement, dom.response);
  }
});

test('Enter and Tab still clarify a punctuated noun without rewriting it', async () => {
  for (const shortcut of ['Enter', 'Tab']) {
    const dom = await heroFor({ theme: 'toolbox' });
    dom.type(dom.response, 'arm?!');
    const event = dom.key(dom.response, shortcut);
    assert.equal(event.defaultPrevented, true);
    assert.equal(dom.question.textContent, "What's wrong with your arm?");
    assert.equal(dom.document.activeElement, dom.clarification);
    assert.equal(dom.window.mendHero.query(), 'arm?!');
    assert.equal(dom.suffixMirror.textContent, 'arm?!');
    assert.equal(dom.document.title, dom.prefix(dom.response) + 'arm?!');
  }
});

test('cancelled native punctuation cannot leave a completion intent for history or composition', async () => {
  const dom = await heroFor();
  dom.type(dom.response, 'arm');
  const before = dom.event(dom.response, 'beforeinput', { inputType: 'insertText', data: '?', bubbles: true });
  before.preventDefault(); // Another native editor listener may reject the edit.
  assert.equal(dom.suffix(dom.response), 'arm');
  assert.equal(dom.visible(), false);
  dom.type(dom.response, 'arm?', { inputType: 'historyRedo' });
  assert.equal(dom.visible(), false);
  assert.equal(dom.document.activeElement, dom.response);
  dom.event(dom.response, 'compositionstart');
  dom.insert(dom.response, '!', { isComposing: true });
  assert.equal(dom.suffix(dom.response), 'arm?!');
  assert.equal(dom.visible(), false);
  dom.event(dom.response, 'compositionend');
  assert.equal(dom.document.activeElement, dom.response);
  assert.equal(dom.window.mendHero.query(), 'arm?!');
});

test('completion punctuation preserves full phrases, decimals, models, and non-subjects', async () => {
  for (const text of ['broken arm', 'arm hurts', '3', '3.5', 'A1708', 'broken']) {
    const dom = await heroFor();
    dom.type(dom.response, text);
    for (const punctuation of ['?', '.', '!']) {
      assert.equal(dom.key(dom.response, punctuation, { shiftKey: punctuation !== '.' }).defaultPrevented, false);
      assert.equal(dom.event(dom.response, 'beforeinput', { inputType: 'insertText', data: punctuation, bubbles: true }).defaultPrevented, false);
      assert.equal(dom.visible(), false);
      assert.equal(dom.window.mendHero.query(), text);
      assert.equal(dom.document.activeElement, dom.response);
    }
  }
});

test('completion shortcuts respect modifiers, composition, and ordinary answer punctuation', async () => {
  for (const punctuation of ['?', '.', '!']) {
    const dom = await heroFor();
    dom.type(dom.response, 'arm');
    for (const options of [{ ctrlKey: true }, { metaKey: true }, { altKey: true },
      { isComposing: true }, { keyCode: 229 }]) {
      const event = dom.key(dom.response, punctuation, { shiftKey: punctuation !== '.', ...options });
      assert.equal(event.defaultPrevented, false);
      assert.equal(dom.visible(), false);
    }
    dom.event(dom.response, 'compositionstart');
    const composing = dom.event(dom.response, 'beforeinput', { inputType: 'insertText', data: punctuation, bubbles: true });
    assert.equal(composing.defaultPrevented, false);
    assert.equal(dom.visible(), false);
    dom.event(dom.response, 'compositionend');
    dom.key(dom.response, punctuation, { shiftKey: punctuation !== '.' });
    dom.insert(dom.response, punctuation);
    assert.equal(dom.key(dom.clarification, punctuation, { shiftKey: punctuation !== '.' }).defaultPrevented, false);
    assert.equal(dom.event(dom.clarification, 'beforeinput', { inputType: 'insertText', data: punctuation, bubbles: true }).defaultPrevented, false);
    dom.type(dom.clarification, 'hurts' + punctuation);
    assert.equal(dom.window.mendHero.query(), 'arm' + punctuation + ' hurts' + punctuation);
  }
});

test('completion punctuation does not hijack midword insertion or selection replacement', async () => {
  const dom = await heroFor();
  dom.type(dom.response, 'laptop');
  const boundary = Number(dom.response.dataset.promptLength);
  for (const [start, end] of [[boundary + 2, boundary + 2], [boundary, dom.response.value.length]]) {
    dom.response.setSelectionRange(start, end);
    for (const punctuation of ['?', '.', '!']) {
      assert.equal(dom.key(dom.response, punctuation, { shiftKey: punctuation !== '.' }).defaultPrevented, false);
      assert.equal(dom.event(dom.response, 'beforeinput', { inputType: 'insertText', data: punctuation, bubbles: true }).defaultPrevented, false);
      assert.equal(dom.visible(), false);
      assert.equal(dom.response.selectionStart, start);
      assert.equal(dom.response.selectionEnd, end);
    }
  }
});

test('Backspace on an empty second suffix dissolves it and suppresses idle reopening', async () => {
  const dom = await heroFor();
  dom.type(dom.response, 'arm');
  dom.key(dom.response, 'Enter');
  const event = dom.key(dom.clarification, 'Backspace');
  assert.equal(event.defaultPrevented, true);
  assert.equal(dom.visible(), false);
  assert.equal(dom.question.textContent, '');
  assert.equal(dom.document.activeElement, dom.response);
  assert.equal(dom.response.selectionStart, dom.response.value.length);
  dom.advance(20000);
  assert.equal(dom.visible(), false);
  dom.key(dom.response, 'Enter');
  assert.equal(dom.visible(), true);
  dom.type(dom.clarification, 'hurts');
  const nonempty = dom.key(dom.clarification, 'Backspace');
  assert.equal(nonempty.defaultPrevented, false, 'native Backspace edits a nonempty symptom');
  assert.equal(dom.visible(), true);
});

test('mobile backward deletion of an empty second suffix returns to the first line', async () => {
  const dom = await heroFor();
  dom.type(dom.response, 'arm');
  dom.key(dom.response, 'Enter');
  const event = dom.event(dom.clarification, 'beforeinput', { inputType: 'deleteContentBackward', bubbles: true });
  assert.equal(event.defaultPrevented, true);
  assert.equal(dom.visible(), false);
  assert.equal(dom.document.activeElement, dom.response);
  assert.equal(dom.window.mendHero.query(), 'arm');
});

test('manual clarification can handle an unfamiliar noun without treating a phrase as a noun', async () => {
  const dom = await heroFor();
  dom.type(dom.response, 'harpsichord');
  dom.advance(20000);
  assert.equal(dom.visible(), false, 'unfamiliar words do not create speculative automatic prompts');
  dom.key(dom.response, 'Tab');
  assert.equal(dom.visible(), true);
  assert.equal(dom.question.textContent, "What's wrong with your harpsichord?");
  assert.equal(dom.clarification.value, '');
  dom.type(dom.clarification, 'sounds wrong');
  assert.equal(dom.window.mendHero.query(), 'harpsichord sounds wrong');
  dom.response.focus();
  dom.type(dom.response, 'broken harpsichord');
  dom.key(dom.response, 'Enter');
  assert.equal(dom.visible(), false);
  assert.equal(dom.window.mendHero.query(), 'broken harpsichord');
});

test('qualified bare objects agree with the worker contract for immediate keyboard and mobile clarification', async () => {
  const worker = vm.createContext({ self: {}, performance: { now: () => 0 },
    fetch: () => new Promise(() => {}), postMessage() { throw new Error('No worker startup response expected'); } });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/search-worker.js'), 'utf8'), worker);
  const cases = ['dick', 'fucking dick', 'my fucking dick', 'FUCKING, DICK!', 'damn laptop',
    'the freaking washing machine', 'washing goddamn machine', 'my fuckin harpsichord',
    ...vm.runInContext('Array.from(intensifiers, value => value + " laptop")', worker)];
  for (const raw of cases) {
    worker.rawQuery = raw;
    const normalized = vm.runInContext('searchPhrase(clean(rawQuery))', worker);
    for (const shortcut of ['Enter', 'Tab', '?', '.', '!', 'mobile?', 'mobile.', 'mobile!']) {
      const dom = await heroFor();
      dom.type(dom.response, raw);
      const punctuation = shortcut.startsWith('mobile') ? shortcut.slice(6) : ['?', '.', '!'].includes(shortcut) ? shortcut : '';
      const event = shortcut.startsWith('mobile') ? dom.insert(dom.response, punctuation)
        : dom.key(dom.response, shortcut, { shiftKey: shortcut === '?' || shortcut === '!' });
      assert.equal(event.defaultPrevented, !punctuation, `${raw}/${shortcut}`);
      if (punctuation && !shortcut.startsWith('mobile')) dom.insert(dom.response, punctuation);
      const typed = raw + punctuation;
      assert.equal(dom.visible(), true);
      assert.equal(dom.question.textContent.toLowerCase(), `what's wrong with your ${normalized}?`);
      assert.equal(dom.document.activeElement, dom.clarification);
      assert.equal(dom.window.mendHero.query(), typed, 'typed qualifiers and punctuation remain in the raw query API');
      assert.equal(dom.suffix(dom.response), typed, 'question cleanup never rewrites typed text');
      dom.type(dom.clarification, "it's broken");
      assert.equal(dom.window.mendHero.query(), `${typed} it's broken`);
      dom.response.focus();
      dom.key(dom.response, 'a', { ctrlKey: true });
      assert.equal(dom.response.value.slice(dom.response.selectionStart, dom.response.selectionEnd), typed);
      assert.equal(dom.response.selectionStart, Number(dom.response.dataset.promptLength));
    }
  }
});

test('qualified known objects share the idle delay while unfamiliar nouns remain manual', async () => {
  for (const [raw, noun] of [['damn laptop', 'laptop'], ['my fucking arm', 'arm'],
    ['the freaking washing machine', 'washing machine']]) {
    const dom = await heroFor();
    dom.type(dom.response, raw);
    dom.advance(379);
    assert.equal(dom.visible(), false);
    dom.advance(1);
    assert.equal(dom.visible(), true);
    assert.equal(dom.question.textContent, `What's wrong with your ${noun}?`);
    assert.equal(dom.window.mendHero.query(), raw);
  }
  for (const raw of ['dick', 'fucking dick', 'my fucking dick']) {
    const dom = await heroFor();
    dom.type(dom.response, raw);
    dom.advance(20000);
    assert.equal(dom.visible(), false, 'qualifiers do not expand the conservative automatic vocabulary');
    dom.key(dom.response, 'Enter');
    assert.equal(dom.question.textContent, "What's wrong with your dick?");
  }
});

test('qualifier cleanup preserves symptom phrases and cannot turn grammar alone into an object', async () => {
  for (const raw of ['broken arm', 'my fucking broken arm', 'arm not working', "my damn arm isn't working",
    'bloody urine', 'my bloody urine', 'my arm fucking hurts', 'damn', 'fucking', 'my fucking', "it's", 'it is']) {
    const dom = await heroFor();
    dom.type(dom.response, raw);
    dom.advance(20000);
    assert.equal(dom.visible(), false, raw);
    for (const shortcut of ['Enter', 'Tab', '?', '.', '!']) {
      dom.key(dom.response, shortcut, { shiftKey: shortcut === '?' || shortcut === '!' });
      assert.equal(dom.visible(), false, raw);
      assert.equal(dom.document.activeElement, dom.response);
      assert.equal(dom.window.mendHero.query(), raw);
    }
  }
  const dom = await heroFor();
  dom.event(dom.response, 'compositionstart');
  dom.type(dom.response, 'my fucking arm', { isComposing: true });
  dom.advance(20000);
  assert.equal(dom.visible(), false);
  assert.equal(dom.key(dom.response, 'Enter', { isComposing: true }).defaultPrevented, false);
  dom.event(dom.response, 'compositionend');
  dom.advance(380);
  assert.equal(dom.question.textContent, "What's wrong with your arm?");
});

test('Ctrl/Meta+A, mouse selection, copy, and cut target editable suffixes only', async () => {
  const dom = await heroFor();
  dom.reveal('laptop');
  dom.type(dom.clarification, 'screen flickers');
  for (const [input, expected] of [[dom.response, 'laptop'], [dom.clarification, 'screen flickers']]) {
    input.focus();
    for (const modifier of ['ctrlKey', 'metaKey']) {
      const event = dom.key(input, 'a', { [modifier]: true });
      assert.equal(event.defaultPrevented, true);
      assert.equal(input.selectionStart, Number(input.dataset.promptLength));
      assert.equal(input.selectionEnd, input.value.length);
    }
    input.setSelectionRange(0, input.value.length, 'backward');
    dom.event(input, 'select');
    assert.equal(input.selectionStart, Number(input.dataset.promptLength));
    const data = new Map();
    const copy = dom.event(input, 'copy', { clipboardData: { setData: (type, value) => data.set(type, value) } });
    assert.equal(copy.defaultPrevented, true);
    assert.equal(data.get('text/plain'), expected);
  }
  const data = new Map();
  const originalPrefix = dom.prefix(dom.clarification);
  const cut = dom.event(dom.clarification, 'cut', { clipboardData: { setData: (type, value) => data.set(type, value) } });
  assert.equal(cut.defaultPrevented, true);
  assert.equal(data.get('text/plain'), 'screen flickers');
  assert.equal(dom.clarification.value, originalPrefix);
  assert.equal(dom.answerMirror.textContent, '');
  assert.equal(dom.window.mendHero.query(), 'laptop');
});

test('the first prefix stays protected while the entire separate answer is editable', async () => {
  const dom = await heroFor();
  dom.reveal('car');
  dom.type(dom.clarification, 'needs repair');
  for (const input of [dom.response, dom.clarification]) {
    input.focus();
    const prefix = dom.prefix(input);
    for (const inputType of ['deleteContentBackward', 'deleteWordBackward']) {
      input.setSelectionRange(prefix.length, prefix.length);
      const event = dom.event(input, 'beforeinput', { inputType, bubbles: true });
      assert.equal(event.defaultPrevented, true);
    }
    input.value = 'unexpected autofill';
    dom.event(input, 'input', { inputType: 'insertReplacementText', bubbles: true });
    if (input === dom.response) {
      assert.equal(input.value, prefix + 'car');
      assert.ok(input.selectionStart >= prefix.length);
    } else {
      assert.equal(prefix, '');
      assert.equal(input.value, 'unexpected autofill');
      assert.equal(dom.window.mendHero.query(), 'car unexpected autofill');
    }
  }
});

test('changing the noun updates the question and clears an unrelated answer', async () => {
  const dom = await heroFor();
  dom.type(dom.response, 'arm');
  dom.key(dom.response, 'Enter');
  dom.type(dom.clarification, 'hurts');
  assert.equal(dom.window.mendHero.query(), 'arm hurts');
  dom.response.focus();
  dom.type(dom.response, 'laptop');
  assert.equal(dom.window.mendHero.query(), 'laptop');
  dom.advance(380);
  assert.equal(dom.question.textContent, "What's wrong with your laptop?");
  assert.equal(dom.clarification.value, '');
  dom.response.focus();
  dom.type(dom.response, 'broken laptop screen');
  assert.equal(dom.visible(), false);
  assert.equal(dom.question.textContent, '');
  assert.equal(dom.clarification.value, '');
  assert.equal(dom.window.mendHero.query(), 'broken laptop screen');
  dom.advance(20000);
  assert.equal(dom.visible(), false);
});

test('composition does not reveal a prompt until committed and pasted full phrases remain single-line', async () => {
  const dom = await heroFor();
  dom.event(dom.response, 'compositionstart', { bubbles: true });
  dom.type(dom.response, 'arm', { isComposing: true, inputType: 'insertCompositionText' });
  dom.advance(20000);
  assert.equal(dom.visible(), false);
  dom.event(dom.response, 'compositionend', { bubbles: true });
  dom.advance(379);
  assert.equal(dom.visible(), false);
  dom.advance(1);
  assert.equal(dom.visible(), true);
  dom.response.focus();
  dom.type(dom.response, 'broken arm', { inputType: 'insertFromPaste' });
  dom.advance(20000);
  assert.equal(dom.visible(), false);
  assert.equal(dom.window.mendHero.query(), 'broken arm');
});

test('background clicks retain the active second selection while cards and guide focus stay usable', async () => {
  const dom = await heroFor();
  dom.type(dom.response, 'car');
  dom.key(dom.response, 'Enter');
  dom.type(dom.clarification, 'battery flat');
  const start = Number(dom.clarification.dataset.promptLength) + 2;
  dom.clarification.setSelectionRange(start, start + 4);
  const background = dom.add('div', 'background');
  const backgroundDown = dom.event(background, 'pointerdown', { bubbles: true });
  assert.equal(backgroundDown.defaultPrevented, true);
  assert.equal(dom.document.activeElement, dom.clarification);
  assert.equal(dom.clarification.selectionStart, start);
  assert.equal(dom.clarification.selectionEnd, start + 4);
  const card = dom.add('a', 'card');
  card.setAttribute('href', '/?guide=fixture');
  const cardDown = dom.event(card, 'pointerdown', { bubbles: true });
  assert.equal(cardDown.defaultPrevented, false);
  card.focus();
  await Promise.resolve();
  assert.equal(dom.document.activeElement, card);
  dom.document.body.classList.add('reading');
  dom.document.body.focus();
  dom.event(background, 'pointerdown', { bubbles: true });
  await Promise.resolve();
  assert.equal(dom.document.activeElement, dom.document.body);
  dom.document.body.classList.remove('reading');
  dom.window.mendHero.focus();
  assert.equal(dom.document.activeElement, dom.clarification);
  assert.equal(dom.clarification.selectionStart, start);
  assert.equal(dom.clarification.selectionEnd, start + 4);
});

test('pending automatic reveal is cancelled while the document is hidden or a guide is open', async () => {
  const dom = await heroFor();
  dom.type(dom.response, 'arm');
  dom.advance(200);
  dom.document.visibilityState = 'hidden';
  dom.event(dom.document, 'visibilitychange');
  dom.advance(20000);
  assert.equal(dom.visible(), false);
  dom.document.visibilityState = 'visible';
  dom.event(dom.document, 'visibilitychange');
  dom.type(dom.response, 'car');
  dom.document.body.classList.add('reading');
  await Promise.resolve();
  dom.document.body.classList.remove('reading');
  await Promise.resolve();
  dom.advance(20000);
  assert.equal(dom.visible(), false);
});

test('normal search-result visibility does not cancel a bare noun idle prompt', async () => {
  const dom = await heroFor();
  dom.type(dom.response, 'arm');
  dom.document.body.classList.add('searching');
  await Promise.resolve();
  dom.advance(380);
  assert.equal(dom.visible(), true);
  assert.equal(dom.document.activeElement, dom.clarification);
  assert.equal(dom.window.mendHero.query(), 'arm');
});

test('editable underline content stays in sync through typing, cut, and reset', async () => {
  const dom = await heroFor();
  assert.equal(dom.suffixMirror.textContent, '');
  assert.equal(dom.suffixMirror.classList.contains('is-empty'), true);
  assert.equal(dom.answerMirror.classList.contains('is-empty'), true);
  dom.type(dom.response, 'arm');
  assert.equal(dom.suffixMirror.textContent, 'arm');
  assert.equal(dom.suffixMirror.classList.contains('is-empty'), false);
  dom.advance(380);
  assert.equal(dom.answerMirror.textContent, '');
  assert.equal(dom.answerMirror.classList.contains('is-empty'), true);
  dom.type(dom.clarification, 'hurts');
  assert.equal(dom.answerMirror.textContent, 'hurts');
  assert.equal(dom.answerMirror.classList.contains('is-empty'), false);
  dom.key(dom.clarification, 'a', { ctrlKey: true });
  dom.event(dom.clarification, 'cut', { clipboardData: { setData() {} } });
  assert.equal(dom.answerMirror.textContent, '');
  assert.equal(dom.answerMirror.classList.contains('is-empty'), true);
  assert.equal(dom.window.mendHero.query(), 'arm');
  dom.key(dom.clarification, 'Backspace');
  dom.type(dom.response, '');
  assert.equal(dom.suffixMirror.textContent, '');
  assert.equal(dom.suffixMirror.classList.contains('is-empty'), true);
  assert.equal(dom.window.mendHero.query(), '');
});

test('all twelve themes retain protected prompts and the controller stays static with reduced motion', async () => {
  for (const theme of themes) {
    const dom = await heroFor({ theme, reducedMotion: true, width: 320 });
    assert.equal(dom.prefix(dom.response), theme === 'toolbox' ? '🙏, 🔧 my ' : 'please, mend my ');
    dom.reveal('relationship');
    assert.equal(dom.document.activeElement, dom.clarification);
    assert.equal(dom.question.textContent, "What's wrong with your relationship?");
    assert.equal(dom.prefix(dom.clarification), '');
    dom.type(dom.clarification, 'needs attention');
    assert.equal(dom.window.mendHero.query(), 'relationship needs attention');
    assert.equal(dom.document.activeElement, dom.clarification);
    assert.equal(dom.document.animations, 0, 'the controller must not start a decorative animation loop');
    assert.ok(Number.isFinite(parseFloat(dom.hero.style.fontSize)));
  }
});

test('all twelve styles fit the visible question and first prompt on narrow and wide layouts', async () => {
  // Injectable proportional metrics exercise the controller's real measurement
  // path. They model different font widths, not browser font shaping or paint.
  for (const [styleIndex, theme] of themes.entries()) {
    const measureText = (text, size) => [...text].reduce((width, char) =>
      width + (char === ' ' ? 0.25 : /[il'?.]/.test(char) ? 0.3 : 0.48 + styleIndex * 0.035), 0) * size;
    for (const width of [280, 320, 768, 1440]) {
      const dom = await heroFor({ theme, width, measureText });
      const closedSize = parseFloat(dom.hero.style.fontSize);
      const firstSample = dom.prefix(dom.response) + 'relationship';
      assert.ok(measureText(firstSample, closedSize) <= width - 6 + 0.001, `${theme}/${width}: first prompt fits`);
      dom.reveal('finger');
      const questionSize = parseFloat(dom.hero.style.fontSize);
      assert.ok(measureText(dom.question.textContent, questionSize) <= width - 6 + 0.001, `${theme}/${width}: full question fits`);
      assert.ok(measureText(firstSample, questionSize) <= width - 6 + 0.001, `${theme}/${width}: first line remains fitted`);
      assert.ok(questionSize <= closedSize, 'visible question may shrink the shared font, never enlarge it');
      dom.response.focus();
      dom.type(dom.response, 'extraordinarilylongunfamiliarobject');
      dom.key(dom.response, 'Enter');
      const longSize = parseFloat(dom.hero.style.fontSize);
      assert.ok(measureText(dom.question.textContent, longSize) <= width - 6 + 0.001, `${theme}/${width}: a longer dynamic question fits`);
      const beforeInput = dom.hero.style.fontSize;
      dom.type(dom.clarification, 'a very long editable answer '.repeat(20));
      assert.equal(dom.hero.style.fontSize, beforeInput, 'editable long text wraps without shrinking the whole hero');
    }
  }
});

test('a question that already fits keeps its style size and fitting refreshes on resize', async () => {
  const measureText = (text, size) => text.length * size * 0.42;
  const dom = await heroFor({ width: 2000, measureText });
  const originalSize = dom.hero.style.fontSize;
  dom.reveal('finger');
  assert.equal(dom.hero.style.fontSize, originalSize, 'fitting question keeps the current theme size');
  dom.response.clientWidth = 320;
  dom.event(dom.window, 'resize');
  const narrowSize = parseFloat(dom.hero.style.fontSize);
  assert.ok(measureText(dom.question.textContent, narrowSize) <= 314 + 0.001);
  assert.ok(narrowSize < parseFloat(originalSize));
  dom.key(dom.clarification, 'Backspace');
  const closedSize = parseFloat(dom.hero.style.fontSize);
  assert.ok(closedSize >= narrowSize, 'dismissal restores fitting for the first prompt only');
  assert.ok(measureText(dom.prefix(dom.response) + 'relationship', closedSize) <= 314 + 0.001);
});

test('new repair styles keep punctuation, title, query, and protected suffix selection native', async () => {
  for (const theme of themes.slice(7)) {
    const dom = await heroFor({ theme, width: 320 });
    dom.type(dom.response, 'fucking arm');
    for (const modifier of ['ctrlKey', 'metaKey']) {
      dom.key(dom.response, 'a', { [modifier]: true });
      assert.equal(dom.response.selectionStart, dom.prefix(dom.response).length, theme);
      assert.equal(dom.response.value.slice(dom.response.selectionStart, dom.response.selectionEnd), 'fucking arm', theme);
    }
    dom.response.setSelectionRange(0, dom.response.value.length, 'backward');
    dom.event(dom.response, 'select');
    assert.equal(dom.response.selectionStart, dom.prefix(dom.response).length, theme);
    dom.response.setSelectionRange(dom.prefix(dom.response).length, dom.prefix(dom.response).length);
    assert.equal(dom.event(dom.response, 'beforeinput', { inputType: 'deleteWordBackward' }).defaultPrevented, true, theme);
    dom.response.setSelectionRange(dom.response.value.length, dom.response.value.length);
    dom.insert(dom.response, '?');
    assert.equal(dom.suffix(dom.response), 'fucking arm?', theme);
    assert.equal(dom.document.title, 'please, mend my fucking arm?', theme);
    assert.equal(dom.question.textContent, "What's wrong with your arm?", theme);
    assert.equal(dom.document.activeElement, dom.clarification, theme);
    dom.type(dom.clarification, "it's broken");
    assert.equal(dom.window.mendHero.query(), "fucking arm? it's broken", theme);
    assert.equal(dom.document.title, 'please, mend my fucking arm?', theme);
    assert.equal(dom.prefixMirror.textContent, 'please, mend my ', theme);
  }
});

test('prompt artwork mounts once and remeasures on layout or fonts without per-keystroke work', async () => {
  const mounted = [], synced = [];
  let prefixWrites = 0;
  const dom = await heroFor({ theme: 'stitched', setup(dom) {
    let text = dom.prefixMirror.textContent;
    Object.defineProperty(dom.prefixMirror, 'textContent', {
      get: () => text,
      set(value) { prefixWrites++; text = value; },
    });
    dom.window.mendHeroStyles = {
      prompt(theme) { assert.equal(theme, 'stitched'); return 'please, mend my '; },
      mount(field, prefix, theme) {
        mounted.push({ field, prefix, theme, text: prefix.textContent });
        return { sync() {
          synced.push({ size: dom.hero.style.fontSize, prefix: prefix.textContent,
            value: dom.response.value, question: dom.question.textContent });
        } };
      },
    };
  } });
  assert.equal(mounted.length, 1);
  assert.equal(mounted[0].field, dom.firstField);
  assert.equal(mounted[0].prefix, dom.prefixMirror);
  assert.equal(mounted[0].theme, 'stitched');
  assert.equal(mounted[0].text, 'please, mend my ', 'metric anchor is populated before mounting');
  assert.ok(synced.length >= 1);
  assert.ok(synced.every(item => Number.isFinite(parseFloat(item.size))));
  const initial = synced.length;
  for (const text of ['b', 'br', 'bro', 'broken', 'broken arm']) dom.type(dom.response, text);
  assert.equal(synced.length, initial, 'editing a phrase must not remeasure decorative artwork');
  assert.equal(prefixWrites, 1, 'native prefix mirror must retain its original text node while typing');
  dom.response.setSelectionRange(dom.prefix(dom.response).length + 1, dom.response.value.length - 1);
  const selection = [dom.response.selectionStart, dom.response.selectionEnd];
  const title = dom.document.title;
  dom.response.clientWidth = 300;
  dom.event(dom.window, 'resize');
  assert.equal(synced.length, initial + 1);
  assert.deepEqual([dom.response.selectionStart, dom.response.selectionEnd], selection);
  dom.event(dom.document.fonts, 'loadingdone');
  assert.equal(synced.length, initial + 2, 'late font loads refresh artwork metrics');
  assert.deepEqual([dom.response.selectionStart, dom.response.selectionEnd], selection);
  assert.equal(dom.document.title, title);
  assert.equal(dom.window.mendHero.query(), 'broken arm');
  dom.type(dom.response, 'arm');
  dom.key(dom.response, 'Enter');
  assert.equal(synced.at(-1).question, "What's wrong with your arm?");
  const open = synced.length;
  dom.type(dom.clarification, 'a very long symptom '.repeat(40));
  assert.equal(synced.length, open, 'answer wrapping must not redraw the fixed artwork');
  assert.equal(dom.document.activeElement, dom.clarification);
  assert.equal(prefixWrites, 1);
});

test('an older cached page without artwork hooks retains the complete native input behavior', async () => {
  for (const styles of [undefined, {}]) {
    const dom = await heroFor({ theme: 'cutpaper', setup(dom) { dom.window.mendHeroStyles = styles; } });
    dom.type(dom.response, 'relationship');
    dom.insert(dom.response, '!');
    assert.equal(dom.response.value, 'please, mend my relationship!');
    assert.equal(dom.document.title, 'please, mend my relationship!');
    assert.equal(dom.question.textContent, "What's wrong with your relationship?");
    assert.equal(dom.document.activeElement, dom.clarification);
    assert.equal(dom.window.mendHero.query(), 'relationship!');
  }
});
