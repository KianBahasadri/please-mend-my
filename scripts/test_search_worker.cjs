const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { performance } = require('node:perf_hooks');

async function workerFor(index, source = fs.readFileSync(path.join(__dirname, '../assets/search-worker.js'), 'utf8')) {
  const messages = [];
  let fetches = 0;
  const context = vm.createContext({ self: {}, performance, setTimeout, clearTimeout,
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
  return { ready, fetches: () => fetches,
    async search(query) {
      context.self.onmessage({ data: { type: 'search', query, requestId: ++requestId, count: 60 } });
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

test('aliases combine with title terms, prefixes work, and exact titles win', async () => {
  const worker = await workerFor(fixture());
  const laptops = await worker.search('laptop battery');
  assert.equal(laptops.total, 2);
  assert.equal(laptops.items[0][0], 'exact-laptop');
  assert.equal((await worker.search('frid thermostat')).items[0][0], 'fridge');
  assert.equal((await worker.search('notebook battery')).items[0][0], 'alias-laptop');
  assert.equal((await worker.search('fridge battery')).total, 0);
  assert.equal((await worker.search('S23')).items[0][0], 's23');
  assert.equal((await worker.search('S24')).total, 0);
  assert.equal(worker.fetches(), 1, 'queries must not make additional requests');
});

test('legacy catalogs continue to search without enrichment', async () => {
  const index = fixture();
  delete index.search;
  const worker = await workerFor(index);
  assert.equal((await worker.search('MacBook battery')).total, 1);
});

test('long queries retain every required term beyond the compact mask width', async () => {
  const index = fixture();
  const terms = Array.from({ length: 33 }, (_, index) => 'required' + index);
  index.search.groups[1] += '\n' + terms.join(' ');
  const worker = await workerFor(index);
  assert.equal((await worker.search(terms.join(' ') + ' battery')).total, 2);
  assert.equal((await worker.search(terms.join(' ') + ' missingword')).total, 0);
});

test('invalid enrichment fails rather than associating aliases with wrong records', async () => {
  const index = fixture();
  index.search.itemGroups = [1];
  await assert.rejects(workerFor(index), /alias format is invalid/);
});

module.exports = { workerFor };
