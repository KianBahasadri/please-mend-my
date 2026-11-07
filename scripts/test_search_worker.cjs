const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { execFileSync } = require('node:child_process');
const { performance } = require('node:perf_hooks');

// Exercise the actual offline producer and browser worker together. No manually
// duplicated postings fixture can hide a mismatch between Python and JavaScript.
function prepare(index) {
  return JSON.parse(execFileSync(process.env.PMM_TEST_PYTHON || 'python3', ['-c',
    'import json,sys; sys.path.insert(0,sys.argv[1]); from prepare_search import prepare_index; '
      + 'index=json.load(sys.stdin); index["search"]["inverted"]=prepare_index(index); json.dump(index,sys.stdout)',
    __dirname], { input: JSON.stringify(index), encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 }));
}

async function workerFor(index, source = fs.readFileSync(path.join(__dirname, '../assets/search-worker.js'), 'utf8')) {
  const messages = [];
  let fetches = 0;
  const context = vm.createContext({ self: {}, performance, setTimeout, clearTimeout,
    atob: value => Buffer.from(value, 'base64').toString('binary'),
    btoa: value => Buffer.from(value, 'binary').toString('base64'),
    fetch: async (url, options) => {
      fetches++;
      assert.equal(url, '/api/catalog');
      assert.equal(options.mode, 'same-origin');
      return { ok: true, json: async () => structuredClone(index) };
    },
    postMessage: (message) => messages.push(message),
  });
  vm.runInContext(source, context);
  async function next(type) {
    for (let attempt = 0; attempt < 1000; attempt++) {
      const index = messages.findIndex(message => message.type === type || message.type === 'error');
      if (index >= 0) {
        const message = messages.splice(index, 1)[0];
        if (message.type === 'error') throw new Error(message.message);
        return message;
      }
      await new Promise(resolve => setTimeout(resolve, 1));
    }
    throw new Error('Worker did not reply');
  }
  const ready = await next('ready');
  let requestId = 0;
  const send = data => context.self.onmessage({ data });
  function queue(query, count = 60) {
    send({ type: 'search', query, requestId: ++requestId, count });
    return requestId;
  }
  return { ready, fetches: () => fetches, send, queue, next, messages,
    async search(query, count = 60) {
      queue(query, count);
      return next('results');
    },
  };
}

const fixture = () => ({ version: 1, revision: 'fixture', source: {},
  categories: [{ id: 'technology', name: 'Technology' }, { id: 'household', name: 'Household' }],
  items: [
    ['alias-laptop', 'MacBook Air Battery Replacement', 'technology', 'guide', 'MacBook Air'],
    ['exact-laptop', 'Laptop Battery Replacement', 'technology', 'guide', 'Laptop'],
    ['fridge', 'Refrigerator Thermostat Replacement', 'household', 'guide', 'Refrigerator'],
    ['s23', 'Samsung Galaxy S23 Screen Replacement', 'technology', 'guide', 'Samsung Galaxy S23'],
  ],
  search: { version: 1, groups: ['', 'laptop\nnotebook', 'fridge\nrefrigerator'], itemGroups: [1, 1, 2, 0] },
});

test('prepared aliases combine with title terms, prefixes work, and exact titles win', async () => {
  const worker = await workerFor(prepare(fixture()));
  assert.equal(worker.ready.invertedIndex, true);
  assert.equal(worker.ready.stemming, true);
  assert.equal(worker.ready.fuzzy, true);
  const laptops = await worker.search('laptop battery');
  assert.equal(laptops.total, 2);
  assert.equal(laptops.items[0][0], 'exact-laptop');
  assert.equal((await worker.search('frid thermostat')).items[0][0], 'fridge');
  assert.equal((await worker.search('notebook battery')).items[0][0], 'alias-laptop');
  assert.equal((await worker.search('fridge battery')).total, 0);
  assert.equal((await worker.search('MacBook Air Battery Replacement')).items[0][0], 'alias-laptop');
  assert.equal(worker.fetches(), 1, 'queries must not make additional requests');
});

test('unprepared alias catalogs and legacy catalogs remain usable', async () => {
  const aliases = await workerFor(fixture());
  assert.equal((await aliases.search('notebook battery')).total, 2);
  const index = fixture();
  delete index.search;
  const worker = await workerFor(index);
  assert.equal((await worker.search('MacBook battery')).total, 1);
  assert.equal(worker.fetches(), 1);
});

test('Porter forms match across title and alias words without changing exact ranking', async () => {
  const index = fixture();
  index.items.push(['plural', 'Laptop Batteries Repair', 'technology', 'guide', 'Laptop']);
  index.search.itemGroups.push(1);
  const worker = await workerFor(prepare(index));
  for (const query of ['laptop batteries', 'laptops battery']) {
    const result = await worker.search(query);
    assert.equal(result.total, 3, query);
    assert.ok(result.items.some(row => row[0] === 'alias-laptop'));
  }
  assert.equal((await worker.search('laptop repairing')).items[0][0], 'plural');
  assert.equal((await worker.search('Laptop Batteries Repair')).items[0][0], 'plural');
});

test('bounded Levenshtein tolerates typos and combines them with aliases', async () => {
  const worker = await workerFor(prepare(fixture()));
  assert.equal((await worker.search('laptpp batery')).total, 2, 'one edit in each query term');
  assert.equal((await worker.search('notebokk batery')).items[0][0], 'alias-laptop');
  assert.equal((await worker.search('frdge thermostat')).items[0][0], 'fridge');
  assert.equal((await worker.search('refrxxerator')).items[0][0], 'fridge', 'two edits in a long word');
  assert.equal((await worker.search('refxxxerator')).total, 0, 'three edits exceed the long-word bound');
  assert.equal((await worker.search('lapxxp')).total, 0, 'two edits exceed the short-word bound');
  assert.equal((await worker.search('notebxxk')).total, 2, 'eight-character words allow two edits');
  assert.equal((await worker.search('notexxxk')).total, 0, 'three edits exceed the eight-character bound');
  assert.equal((await worker.search('frdg')).total, 0, 'words shorter than five characters are not fuzzy');
  assert.equal(worker.fetches(), 1);
});

test('offline and query stemmers agree on forms beyond typo distance', async () => {
  const index = fixture();
  index.items = [
    ['related', 'Related Repair', 'technology', 'guide', ''],
    ['sensitive', 'Sensitive Repair', 'technology', 'guide', ''],
    ['rational', 'Rational Repair', 'technology', 'guide', ''],
  ];
  index.search = { version: 1, groups: [''], itemGroups: [0, 0, 0] };
  const worker = await workerFor(prepare(index));
  for (const [query, expected] of [['relational', 'related'], ['sensitivity', 'sensitive'], ['rationalization', 'rational']]) {
    const result = await worker.search(query);
    assert.equal(result.total, 1, query);
    assert.equal(result.items[0][0], expected, query);
  }
});

test('a rare literal elsewhere does not block typo recovery for an empty intersection', async () => {
  const index = fixture();
  index.items.push(['literal-typo', 'Batery Label', 'household', 'topic', '']);
  index.search.itemGroups.push(0);
  const worker = await workerFor(prepare(index));
  const result = await worker.search('laptop batery');
  assert.equal(result.total, 2);
  assert.ok(result.items.every(row => row[0] !== 'literal-typo'));
  assert.equal((await worker.search('laptop battery')).items[0][0], 'exact-laptop');
});

test('model identifiers stay strict and survive mixed punctuation and typo queries', async () => {
  const index = fixture();
  index.items.push(['s24', 'Samsung Galaxy S24 Screen Replacement', 'technology', 'guide', 'Samsung Galaxy S24']);
  index.items.push(['a1708', 'MacBook Pro A1708 Battery Replacement', 'technology', 'guide', 'MacBook Pro A1708']);
  index.search.itemGroups.push(0, 1);
  const worker = await workerFor(prepare(index));
  assert.deepEqual(Array.from((await worker.search('S23')).items, row => row[0]), ['s23']);
  assert.deepEqual(Array.from((await worker.search('S24')).items, row => row[0]), ['s24']);
  assert.equal((await worker.search('S25')).total, 0);
  assert.equal((await worker.search('samsun galaxy S23')).items[0][0], 's23');
  assert.equal((await worker.search('A1708')).items[0][0], 'a1708');
  assert.equal((await worker.search('1708')).items[0][0], 'a1708');
  assert.equal((await worker.search('A1709')).total, 0);
  assert.equal((await worker.search('S2')).total, 2, 'model prefixes still work while typing');
  assert.equal((await worker.search('S23+')).total, 0, 'model punctuation must not be discarded');
});

test('accent normalization and word separators agree with the offline producer', async () => {
  const index = fixture();
  index.items.push(['cafe', 'Café Mixer Repair', 'household', 'guide', 'Café Mixer']);
  index.items.push(['wh', 'Sony WH-1000XM4 Battery Replacement', 'technology', 'guide', 'Sony WH-1000XM4']);
  index.search.itemGroups.push(0, 0);
  const worker = await workerFor(prepare(index));
  assert.equal((await worker.search('CAFE mixer')).items[0][0], 'cafe');
  assert.equal((await worker.search('cafe\u0301 mixer')).items[0][0], 'cafe');
  assert.equal((await worker.search('WH-1000XM4 battery')).items[0][0], 'wh');
  assert.equal((await worker.search('WH-1000XM5 battery')).total, 0);
});

test('long queries retain every required term beyond the old compact mask width', async () => {
  const index = fixture();
  const terms = Array.from({ length: 33 }, (_, index) => 'required' + index);
  index.search.groups[1] += '\n' + terms.join(' ');
  const worker = await workerFor(prepare(index));
  assert.equal((await worker.search(terms.join(' ') + ' battery')).total, 2);
  assert.equal((await worker.search(terms.join(' ') + ' missingword')).total, 0);
});

test('only the best 100 matches remain available through viewport ranges and stale ranges are ignored', async () => {
  const index = fixture();
  index.items = Array.from({ length: 120 }, (_, i) => ['battery-' + i, 'Battery Repair ' + i, 'technology', 'guide', 'Battery']);
  index.search = { version: 1, groups: [''], itemGroups: Array(120).fill(0) };
  const worker = await workerFor(prepare(index));
  const result = await worker.search('battery', 30);
  assert.equal(worker.ready.total, 120, 'the complete browser catalog remains available');
  assert.equal(worker.ready.resultLimit, 100);
  assert.equal(result.total, 100);
  assert.equal(result.items.length, 30);
  worker.send({ type: 'range', requestId: result.requestId, rangeId: 1, start: 30, count: 60 });
  const range = await worker.next('range');
  assert.equal(range.start, 30);
  assert.equal(range.items.length, 60);
  assert.equal(new Set([...result.items, ...range.items].map(row => row[0])).size, 90);
  worker.send({ type: 'range', requestId: result.requestId, rangeId: 2, start: 90, count: 60 });
  const last = await worker.next('range');
  assert.equal(last.items.length, 10);
  assert.deepEqual(Array.from(last.items, row => row[0]), index.items.slice(90, 100).map(row => row[0]));
  worker.send({ type: 'range', requestId: result.requestId, rangeId: 3, start: 100, count: 60 });
  assert.equal((await worker.next('range')).items.length, 0);
  const emptyQuery = await worker.search('');
  assert.equal(emptyQuery.total, 100);
  assert.equal(emptyQuery.items.length, 60);
  await worker.search('unmatchedzzzz');
  worker.send({ type: 'range', requestId: result.requestId, rangeId: 4, start: 90, count: 30 });
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(worker.messages.filter(message => message.type === 'range').length, 0);
  assert.equal(worker.fetches(), 1);
});

test('the cap keeps higher-ranked late documents and stable ties in prepared and legacy catalogs', async () => {
  const index = fixture();
  index.items = [];
  index.search = { version: 1, groups: ['', 'laptop'], itemGroups: [] };
  const add = (id, title, kind, group = 0) => {
    index.items.push([id, title, 'technology', kind, '']);
    index.search.itemGroups.push(group);
  };
  // Put weak alias hits first and exact hits last: slicing the first 100
  // candidates, or stopping the candidate scan at 100, must both fail.
  for (let at = 0; at < 160; at++) add('alias-' + at, 'Notebook Repair ' + at, 'guide', 1);
  for (let at = 0; at < 150; at++) add('inside-' + at, 'Repair Laptop Battery ' + at, 'guide');
  for (let at = 0; at < 130; at++) add('prefix-' + at, 'Laptop Battery Repair ' + at, 'guide');
  add('exact-topic', 'Laptop', 'topic');
  add('exact-guide', 'Laptop', 'guide');
  const expected = ['exact-topic', 'exact-guide', ...Array.from({ length: 98 }, (_, at) => 'prefix-' + at)];
  for (const catalog of [index, prepare(index)]) {
    const worker = await workerFor(catalog);
    const result = await worker.search('laptop');
    worker.send({ type: 'range', requestId: result.requestId, rangeId: 1, start: 60, count: 60 });
    const tail = await worker.next('range');
    assert.equal(result.total, 100);
    assert.deepEqual(Array.from([...result.items, ...tail.items], row => row[0]), expected);
    assert.equal((await worker.search('laptop')).items[0][0], 'exact-topic', 'repeat queries retain the same ranking');
    assert.equal(worker.fetches(), 1);
  }
});

test('bare subjects prefer general concise entries over long niche repair titles', async () => {
  const index = fixture();
  index.items = [
    ['niche-dick', 'Dick smith 2 channel dvd player Disassembly', 'technology', 'guide', ''],
    ['general-dick', 'Dick problems', 'technology', 'article', ''],
    ['prefix-dick', 'Dickey', 'technology', 'article', ''],
    ['niche-car', 'Car audio KDC-BT44U firmware update', 'technology', 'guide', ''],
    ['general-car', 'Car and Truck', 'technology', 'topic', ''],
    ['niche-laptop', 'Laptop Cooling Pad Airflow Improvement and Performance Optimization Technique', 'technology', 'guide', ''],
    ['general-laptop', 'Laptop Cooling Pad', 'technology', 'topic', ''],
    ['model-topic', 'S23', 'technology', 'topic', ''],
    ['model-guide', 'S23', 'technology', 'guide', ''],
  ];
  index.search = { version: 1, groups: [''], itemGroups: index.items.map(() => 0) };
  for (const catalog of [index, prepare(index)]) {
    const worker = await workerFor(catalog);
    const dick = (await worker.search('dick')).items.map(row => row[0]);
    assert.ok(dick.indexOf('general-dick') < dick.indexOf('niche-dick'));
    if (catalog.search.inverted) assert.equal(dick[0], 'general-dick', 'literal word quality still outranks a shorter partial-word hit');
    for (const [query, expected] of [['car', 'general-car'], ['laptop', 'general-laptop']]) {
      assert.equal((await worker.search(query)).items[0][0], expected, query);
    }
    assert.equal((await worker.search('Dick smith 2 channel dvd player Disassembly')).items[0][0], 'niche-dick',
      'an exact detailed request still leads to its requested procedure');
    assert.equal((await worker.search('Car audio KDC-BT44U firmware update')).items[0][0], 'niche-car');
    assert.equal((await worker.search('S23')).items[0][0], 'model-guide', 'numeric model scoring keeps its original priority');
    assert.equal(worker.fetches(), 1);
  }
});

test('generality-aware bounded ranking matches exhaustive scoring without pruning', async () => {
  const index = fixture();
  index.items = [];
  index.search = { version: 1, groups: ['', 'laptop'], itemGroups: [] };
  for (let at = 0; at < 420; at++) {
    const kind = at % 3 === 0 ? 'topic' : at % 3 === 1 ? 'guide' : 'article';
    const title = at < 160 ? 'Laptop ' + 'Highly Specific Model Procedure '.repeat(2 + at % 3)
      : at < 300 ? 'Laptop ' + ['care', 'repair', 'overview', 'maintenance'][at % 4]
      : at < 400 ? 'Notebook Battery Replacement ' + at : 'Laptop';
    index.items.push(['rank-' + at, title, 'technology', kind, '']);
    index.search.itemGroups.push(at >= 300 && at < 400 ? 1 : 0);
  }
  index.items.push(['canonical-alias', 'PC Laptop', 'technology', 'topic', 'PC Laptop']);
  index.search.itemGroups.push(1);
  index.search.sources = [{ label: 'laptop', aliases: ['notebook'], ifixitPaths: ['Device/PC_Laptop'] }];
  // With a capacity greater than this fixture the same scorer visits and sorts
  // every match, so no candidate can be discarded by the 100-result upper bound.
  const source = fs.readFileSync(path.join(__dirname, '../assets/search-worker.js'), 'utf8');
  const exhaustive = source.replace('const RESULT_LIMIT = 100;', 'const RESULT_LIMIT = 10000;');
  assert.notEqual(exhaustive, source);
  const prepared = prepare(index);
  const limitedWorker = await workerFor(prepared);
  const fullWorker = await workerFor(prepared, exhaustive);
  for (const query of ['laptop', 'lap', 'notebook', 'laptpp', 'laptop care']) {
    const limited = await limitedWorker.search(query);
    limitedWorker.send({ type: 'range', requestId: limited.requestId, rangeId: 1, start: 60, count: 60 });
    const tail = await limitedWorker.next('range');
    const full = await fullWorker.search(query);
    fullWorker.send({ type: 'range', requestId: full.requestId, rangeId: 1, start: 60, count: 40 });
    const fullTail = await fullWorker.next('range');
    assert.equal(limited.total, Math.min(100, full.total), query);
    assert.deepEqual(Array.from([...limited.items, ...tail.items], row => row[0]),
      Array.from([...full.items, ...fullTail.items], row => row[0]), query);
  }
});

test('published subject anchors and source roots beat components and unrelated short acronyms', async () => {
  const index = fixture();
  index.items = [
    ['car-acronym', 'CAR-NK', 'technology', 'article', ''],
    ['car-component', 'Car Seat', 'technology', 'topic', 'Car Seat'],
    ['car-root', 'Car and Truck', 'technology', 'topic', 'Car and Truck'],
    ['laptop-component', 'Laptop Cooling Pad', 'technology', 'topic', 'Laptop Cooling Pad'],
    ['laptop-specific', 'Laptop Lenovo G510 Keyboard Replacement', 'technology', 'guide', ''],
    ['laptop-root', 'PC Laptop', 'technology', 'topic', 'PC Laptop'],
    ['laptop-mac', 'Mac Laptop', 'technology', 'topic', 'Mac Laptop'],
    ['laptop-exact', 'Laptop', 'technology', 'article', ''],
  ];
  index.search = { version: 1, groups: ['', 'laptop\nnotebook\nlaptops', 'electronics'],
    itemGroups: [0, 2, 0, 2, 2, 1, 1, 0],
    sources: [{ label: 'laptop', aliases: ['notebook'], curated: ['laptops'],
      ifixitPaths: ['Device/PC_Laptop', 'Device/Mac_Laptop'] }] };
  for (const catalog of [index, prepare(index)]) {
    const worker = await workerFor(catalog);
    assert.equal((await worker.search('car')).items[0][0], 'car-root');
    const laptop = (await worker.search('laptop')).items.map(row => row[0]);
    assert.equal(laptop[0], 'laptop-exact', 'exact title stays ahead of canonical aliases');
    assert.ok(laptop.indexOf('laptop-root') < laptop.indexOf('laptop-component'));
    assert.ok(laptop.indexOf('laptop-mac') < laptop.indexOf('laptop-specific'));
    assert.equal((await worker.search('notebook')).items[0][0], 'laptop-root');
    assert.equal((await worker.search('Laptop Lenovo G510 Keyboard Replacement')).items[0][0], 'laptop-specific');
  }
});

test('queued keystrokes coalesce to the latest query', async () => {
  const worker = await workerFor(prepare(fixture()));
  worker.queue('fridge');
  const latestId = worker.queue('laptop battery');
  const result = await worker.next('results');
  assert.equal(result.requestId, latestId);
  assert.equal(result.total, 2);
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(worker.messages.filter(message => message.type === 'results').length, 0);
});

test('invalid alias references and damaged inverted metadata fail closed', async () => {
  const badAliases = fixture();
  badAliases.search.itemGroups = [1];
  await assert.rejects(workerFor(badAliases), /alias format is invalid/);
  const badVersion = prepare(fixture());
  badVersion.search.inverted.version = 987;
  await assert.rejects(workerFor(badVersion), /invalid|unsupported|format/i);
  const badOffsets = prepare(fixture());
  badOffsets.search.inverted.offsets = [0];
  await assert.rejects(workerFor(badOffsets), /invalid|unsupported|format/i);
});

module.exports = { workerFor, prepare };
