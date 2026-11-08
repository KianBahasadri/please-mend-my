const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { performance } = require('node:perf_hooks');

const origin = 'http://127.0.0.1:8080';
const code = fs.readFileSync(path.join(__dirname, '../assets/client-cache.js'), 'utf8');
const catalog = revision => ({ version: 1, revision, items: [['one', 'Guide', 'other', 'guide', '']], categories: [] });
const json = (value, etag, extra = {}) => new Response(JSON.stringify(value), {
  status: 200, headers: { 'Content-Type': 'application/json', ETag: etag, ...extra },
});

function storage() {
  const stores = new Map();
  let failed = false;
  const url = key => new URL(typeof key === 'string' ? key : key.url, origin).href;
  return {
    stores,
    fail(value = true) { failed = value; },
    async open(name) {
      if (failed) throw Error('Denied or full');
      if (!stores.has(name)) {
        const entries = new Map();
        stores.set(name, { entries,
          async match(key) { if (failed) throw Error('Denied'); return entries.get(url(key))?.clone(); },
          async put(key, response) { if (failed) throw Error('Quota'); entries.set(url(key), response.clone()); },
          async delete(key) { return entries.delete(url(key)); },
          async keys() { return [...entries.keys()].map(key => new Request(key)); },
        });
      }
      return stores.get(name);
    },
    async delete(name) { return stores.delete(name); },
    async keys() { return [...stores.keys()]; },
  };
}

function helper({ caches = storage(), locks, transport = async () => { throw Error('Unexpected network'); } } = {}) {
  const requests = [];
  const scope = { caches, performance, URL, Response, Request, Headers, DOMException, console,
    location: { origin, protocol: 'http:' }, navigator: { onLine: true, locks },
    fetch: async (url, options) => {
      requests.push({ url, options });
      assert.equal(options.mode, 'same-origin');
      assert.equal(options.credentials, 'same-origin');
      return transport(url, options);
    },
  };
  vm.runInNewContext(code, scope);
  return { cache: scope.mendClientCache, scope, caches, requests };
}

async function activate(ui, revision = 'one') {
  const store = await ui.caches.open('pmm-catalog-v1');
  await store.put('/api/catalog', json(catalog(revision), '"' + revision + '"'));
  const entry = await ui.cache.loadCatalog();
  assert.equal(await ui.cache.acceptCatalog(entry), true);
  return entry;
}

test('warm catalog returns local JSON before an explicit conditional HEAD probe, offline too', async () => {
  const ui = helper({ transport: async (_url, options) => {
    assert.equal(options.method, 'HEAD');
    assert.equal(options.headers['If-None-Match'], '"one"');
    assert.equal(options.cache, 'no-store');
    return new Response(null, { status: 304 });
  } });
  const entry = await activate(ui);
  assert.equal(entry.source, 'cache');
  assert.equal(entry.catalog.revision, 'one');
  assert.equal(ui.requests.length, 0, 'cache bootstrap never waits for a probe');
  assert.equal(await ui.cache.probeCatalog(entry.etag), null);
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.cache.stats.unchanged, 1);
  ui.scope.navigator.onLine = false;
  assert.equal((await ui.cache.loadCatalog()).catalog.revision, 'one');
  assert.equal(await ui.cache.probeCatalog(entry.etag), null);
  assert.equal(ui.requests.length, 1);
});

test('changed catalog stages without replacing the good response until validation accepts it', async () => {
  const ui = helper({ transport: async (_url, options) => options.method === 'HEAD'
    ? new Response(null, { status: 200, headers: { ETag: '"two"' } }) : json(catalog('two'), '"two"') });
  await activate(ui);
  const guides = await ui.caches.open('pmm-guides-v1');
  await guides.put('/api/guides/one?catalog=%22one%22', json({ id: 'one', title: 'Old', html: '<p>Old</p>' }, '"body"'));
  const candidate = await ui.cache.probeCatalog('"one"');
  assert.ok(candidate.candidateKey);
  assert.equal((await ui.cache.loadCatalog()).catalog.revision, 'one');
  const prepared = await ui.cache.loadCatalog({ candidateKey: candidate.candidateKey });
  assert.equal(prepared.source, 'candidate');
  assert.equal(prepared.catalog.revision, 'two');
  assert.equal((await ui.cache.loadCatalog()).catalog.revision, 'one', 'parsing does not publish an unvalidated index');
  assert.equal(await ui.cache.acceptCatalog(prepared), true);
  assert.equal((await ui.cache.loadCatalog()).catalog.revision, 'two');
  assert.equal(ui.caches.stores.has('pmm-guides-v1'), false, 'a changed catalog purges persisted guides');
});

test('invalid staged updates preserve the last good catalog and missing candidates cannot activate', async () => {
  const ui = helper({ transport: async (_url, options) => options.method === 'HEAD'
    ? new Response(null, { status: 200, headers: { ETag: '"two"' } }) : json({ version: 99 }, '"two"') });
  await activate(ui);
  const candidate = await ui.cache.probeCatalog('"one"');
  await assert.rejects(ui.cache.loadCatalog({ candidateKey: candidate.candidateKey }), /Invalid cached catalog/);
  assert.equal((await ui.cache.loadCatalog()).catalog.revision, 'one');
  assert.equal(await ui.cache.acceptCatalog(candidate), false);
  assert.equal(await ui.cache.acceptCatalog({ etag: '"two"', candidateKey: 'https://outside.example/private' }), false);
});

test('an older tab cannot replace a newer accepted catalog with its delayed candidate', async () => {
  const caches = storage();
  const first = helper({ caches, transport: async (_url, options) => options.method === 'HEAD'
    ? new Response(null, { status: 200, headers: { ETag: '"two"' } }) : json(catalog('two'), '"two"') });
  await activate(first);
  const delayed = await first.cache.probeCatalog('"one"');
  const second = helper({ caches, transport: async (_url, options) => options.method === 'HEAD'
    ? new Response(null, { status: 200, headers: { ETag: '"three"' } }) : json(catalog('three'), '"three"') });
  const newer = await second.cache.probeCatalog('"one"');
  assert.equal(await second.cache.acceptCatalog(newer), true);
  assert.equal(await first.cache.acceptCatalog(delayed), false);
  assert.equal((await second.cache.loadCatalog()).catalog.revision, 'three');
});

test('malformed local catalog falls back to network; storage denial and quota never prevent search', async () => {
  const ui = helper({ transport: async () => json(catalog('one'), '"one"') });
  const store = await ui.caches.open('pmm-catalog-v1');
  await store.put('/api/catalog', new Response('broken json', { headers: { ETag: '"bad"' } }));
  const entry = await ui.cache.loadCatalog();
  assert.equal(entry.source, 'network');
  assert.equal(entry.catalog.revision, 'one');
  assert.equal(await ui.cache.acceptCatalog(entry), true);
  assert.equal(ui.requests.length, 1);
  ui.caches.fail();
  const uncached = await ui.cache.loadCatalog();
  assert.equal(uncached.catalog.revision, 'one');
  assert.equal(await ui.cache.acceptCatalog(uncached), true, 'storage is an optimization, not a requirement');
});

test('cold readiness can parse while the staged disk write remains pending', async () => {
  const caches = storage();
  const store = await caches.open('pmm-catalog-v1');
  const put = store.put.bind(store);
  let release;
  const hold = new Promise(resolve => { release = resolve; });
  store.put = async (key, response) => { await hold; return put(key, response); };
  const ui = helper({ caches, transport: async () => json(catalog('one'), '"one"') });
  const entry = await ui.cache.loadCatalog();
  assert.equal(entry.catalog.revision, 'one');
  assert.ok(entry.staging && typeof entry.staging.then === 'function');
  let accepted = false;
  const pending = ui.cache.acceptCatalog(entry).then(value => { accepted = value; });
  await Promise.resolve();
  assert.equal(accepted, false);
  release(); await pending;
  assert.equal(accepted, true);
});

test('a cached catalog without a validator is recovered online instead of staying permanently stale', async () => {
  const ui = helper({ transport: async () => json(catalog('new'), '"new"') });
  const store = await ui.caches.open('pmm-catalog-v1');
  await store.put('/api/catalog', new Response(JSON.stringify(catalog('old'))));
  const entry = await ui.cache.loadCatalog();
  assert.equal(entry.source, 'network');
  assert.equal(entry.catalog.revision, 'new');
  assert.equal(await ui.cache.acceptCatalog(entry), true);
  assert.equal(ui.requests.length, 1);
});

test('guide hits stay generation-scoped, cache all IDs uniformly, and evict least recent beyond 500', async () => {
  const ui = helper({ transport: async url => {
    const id = url.slice('/api/guides/'.length);
    return json({ id, title: 'Guide ' + id, html: '<p>Body</p>' }, '"body"', { 'X-PMM-Catalog-ETag': '"one"' });
  } });
  await activate(ui);
  for (let at = 0; at < 500; at++) {
    const result = await ui.cache.fetchGuide('id-' + at, { etag: '"one"' });
    assert.equal((await result.json()).id, 'id-' + at);
    await ui.cache.idle();
  }
  await ui.cache.fetchGuide('id-0', { etag: '"one"' }); await ui.cache.idle();
  assert.equal(ui.requests.length, 500);
  await ui.cache.fetchGuide('id-500', { etag: '"one"' }); await ui.cache.idle();
  const store = await ui.caches.open('pmm-guides-v1');
  const keys = (await store.keys()).filter(key => key.url.includes('/api/guides/'));
  assert.equal(keys.length, 500);
  assert.ok(keys.some(key => key.url.includes('/id-0?')));
  assert.equal(keys.some(key => key.url.includes('/id-1?')), false);
  assert.ok(keys.some(key => key.url.includes('/id-500?')));
  ui.scope.fetch = async () => { throw Error('Offline'); };
  assert.equal((await (await ui.cache.fetchGuide('id-0', { etag: '"one"' })).json()).id, 'id-0');
});

test('shared native locks keep the guide LRU bounded across tabs and offline direct links infer their generation', async () => {
  const caches = storage();
  const lockNames = [], tails = new Map();
  const locks = { request(name, options, action) {
    assert.equal(options.mode, 'exclusive');
    lockNames.push(name);
    const next = (tails.get(name) || Promise.resolve()).catch(() => {}).then(action);
    tails.set(name, next.catch(() => {}));
    return next;
  } };
  const transport = async url => json({ id: url.slice('/api/guides/'.length), title: 'Guide', html: 'Body' },
    '"body"', { 'X-PMM-Catalog-ETag': '"one"' });
  const first = helper({ caches, locks, transport });
  const second = helper({ caches, locks, transport });
  await activate(first); await activate(second);
  for (let at = 0; at < 501; at++) {
    const ui = at % 2 ? first : second;
    await ui.cache.fetchGuide('shared-' + at, { etag: '"one"' });
    await ui.cache.idle();
  }
  const store = await caches.open('pmm-guides-v1');
  const keys = (await store.keys()).filter(key => key.url.includes('/api/guides/'));
  assert.equal(keys.length, 500, 'both contexts share one recency order');
  assert.equal(keys.some(key => key.url.includes('/shared-0?')), false);
  const offline = helper({ caches, locks });
  assert.equal((await (await offline.cache.fetchGuide('shared-500')).json()).id, 'shared-500',
    'direct guide startup can read the saved catalog generation before its worker is ready');
  assert.equal(offline.requests.length, 0);
  assert.ok(lockNames.length > 500, 'native locks cover publication and recency writes');
});

test('late old-guide responses cannot repopulate a changed catalog generation', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const ui = helper({ transport: async () => { await pending; return json({ id: 'one', title: 'Old', html: 'Old' }, '"guide"', { 'X-PMM-Catalog-ETag': '"one"' }); } });
  await activate(ui);
  const old = ui.cache.fetchGuide('one', { etag: '"one"' });
  const store = await ui.caches.open('pmm-catalog-v1');
  await store.put('/__pmm_catalog_candidate__/two/fixture', json(catalog('two'), '"two"', { 'X-PMM-Base-Catalog-ETag': '"one"' }));
  await ui.cache.acceptCatalog({ candidateKey: '/__pmm_catalog_candidate__/two/fixture', etag: '"two"' });
  release(); await old; await ui.cache.idle();
  const guides = await ui.caches.open('pmm-guides-v1');
  assert.equal((await guides.keys()).filter(key => key.url.includes('/api/guides/')).length, 0);
});

test('a cached-guide lookup spanning a catalog change never returns the old payload', async () => {
  const ui = helper({ transport: async () => json({ id: 'one', title: 'Old', html: 'Old' },
    '"guide"', { 'X-PMM-Catalog-ETag': '"one"' }) });
  await activate(ui);
  await ui.cache.fetchGuide('one', { etag: '"one"' }); await ui.cache.idle();
  const guides = await ui.caches.open('pmm-guides-v1'), match = guides.match.bind(guides);
  let release, entered;
  const holding = new Promise(resolve => { release = resolve; });
  const started = new Promise(resolve => { entered = resolve; });
  guides.match = async key => {
    const value = await match(key);
    if (String(key).startsWith('/api/guides/')) { entered(); await holding; }
    return value;
  };
  const pending = ui.cache.fetchGuide('one', { etag: '"one"' });
  await started;
  const catalogs = await ui.caches.open('pmm-catalog-v1');
  await catalogs.put('/__pmm_catalog_candidate__/two/fixture', json(catalog('two'), '"two"',
    { 'X-PMM-Base-Catalog-ETag': '"one"' }));
  assert.equal(await ui.cache.acceptCatalog({ candidateKey: '/__pmm_catalog_candidate__/two/fixture', etag: '"two"' }), true);
  release();
  await assert.rejects(pending, /Catalog changed/);
  await ui.cache.idle();
  assert.equal(ui.requests.length, 1, 'the retired cache hit is not replaced with an unscoped network request');
});

test('origin-generation mismatches never render or cache and request one refresh notification', async () => {
  const ui = helper({ transport: async () => json({ id: 'one', title: 'New', html: 'New' }, '"guide"', { 'X-PMM-Catalog-ETag': '"two"' }) });
  await activate(ui);
  const changes = []; ui.cache.subscribe(change => changes.push(change));
  await assert.rejects(ui.cache.fetchGuide('one', { etag: '"one"' }), /Catalog changed/);
  await ui.cache.idle();
  assert.deepEqual(JSON.parse(JSON.stringify(changes)), [{ type: 'catalog-changed', etag: '"two"' }]);
  assert.equal(ui.caches.stores.has('pmm-guides-v1') && ui.caches.stores.get('pmm-guides-v1').entries.size, 0);
});

test('corrupt cached guides refetch and invalid/non-2xx responses never enter persistence', async () => {
  let invalid = false;
  const ui = helper({ transport: async url => invalid ? new Response('Unavailable', { status: 503 })
    : json({ id: url.slice('/api/guides/'.length), title: 'Good', html: 'Good' }, '"guide"', { 'X-PMM-Catalog-ETag': '"one"' }) });
  await activate(ui);
  const store = await ui.caches.open('pmm-guides-v1');
  await store.put('/api/guides/one?catalog=%22one%22', new Response('broken', { headers: { 'X-PMM-Catalog-ETag': '"one"' } }));
  assert.equal((await (await ui.cache.fetchGuide('one', { etag: '"one"' })).json()).title, 'Good');
  await ui.cache.idle(); invalid = true;
  assert.equal((await ui.cache.fetchGuide('missing', { etag: '"one"' })).status, 503);
  await ui.cache.idle();
  assert.equal((await store.keys()).filter(key => key.url.includes('/missing?')).length, 0);
});

module.exports = { storage };
