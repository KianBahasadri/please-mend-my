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

test('all matches remain available through viewport ranges and stale ranges are ignored', async () => {
  const index = fixture();
  index.items = Array.from({ length: 120 }, (_, i) => ['battery-' + i, 'Battery Repair ' + i, 'technology', 'guide', 'Battery']);
  index.search = { version: 1, groups: [''], itemGroups: Array(120).fill(0) };
  const worker = await workerFor(prepare(index));
  const result = await worker.search('battery', 30);
  assert.equal(result.total, 120);
  assert.equal(result.items.length, 30);
  worker.send({ type: 'range', requestId: result.requestId, rangeId: 1, start: 30, count: 60 });
  const range = await worker.next('range');
  assert.equal(range.start, 30);
  assert.equal(range.items.length, 60);
  assert.equal(new Set([...result.items, ...range.items].map(row => row[0])).size, 90);
  await worker.search('unmatchedzzzz');
  worker.send({ type: 'range', requestId: result.requestId, rangeId: 2, start: 90, count: 30 });
  await new Promise(resolve => setTimeout(resolve, 5));
  assert.equal(worker.messages.filter(message => message.type === 'range').length, 0);
  assert.equal(worker.fetches(), 1);
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
