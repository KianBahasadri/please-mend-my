const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { webcrypto, createHash } = require('node:crypto');

const source = fs.readFileSync(path.join(__dirname, '../sw.js'), 'utf8').replaceAll('__PMM_SHELL_VERSION__', 'current');
const origin = 'http://127.0.0.1:8080';

function service({ corruptAsset = '', offline = false } = {}) {
  const listeners = new Map(), stores = new Map(), requests = [];
  let skips = 0, claims = 0;
  const bodies = new Map([['/', '<html>Offline repair library</html>'], ['/assets/search.js', 'native search'], ['/assets/fonts/example.woff2', 'wOF2']]);
  const manifest = { version: 'current', assets: [...bodies].map(([url, body]) => ({
    url, sha256: createHash('sha256').update(body).digest('hex'),
  })) };
  const key = value => new URL(typeof value === 'string' ? value : value.url, origin).href;
  const caches = {
    async open(name) {
      if (!stores.has(name)) {
        const entries = new Map();
        stores.set(name, { entries,
          async match(value) {
            const entry = entries.get(key(value));
            return entry && new Response(entry.body, { headers: entry.headers, status: entry.status });
          },
          async put(value, response) { entries.set(key(value), { body: await response.arrayBuffer(), headers: [...response.headers], status: response.status }); },
        });
      }
      return stores.get(name);
    },
    async keys() { return [...stores.keys()]; },
    async delete(name) { return stores.delete(name); },
  };
  const context = { caches, crypto: webcrypto, URL, Response, Request, Uint8Array,
    self: { location: { origin }, addEventListener: (type, callback) => listeners.set(type, callback),
      skipWaiting: async () => { skips++; }, clients: { claim: async () => { claims++; } } },
    fetch: async (value, options) => {
      const url = new URL(typeof value === 'string' ? value : value.url, origin);
      requests.push({ url: url.pathname, options });
      if (offline) throw new TypeError('Offline');
      if (url.pathname === '/shell-manifest.json') return new Response(JSON.stringify(manifest));
      const body = bodies.get(url.pathname);
      if (body === undefined) return new Response('missing', { status: 404 });
      return new Response(url.pathname === corruptAsset ? body + ' mismatched' : body,
        { headers: { 'Content-Type': url.pathname.endsWith('.woff2') ? 'font/woff2' : 'text/plain' } });
    },
  };
  vm.runInNewContext(source, context);
  return { caches, stores, requests, listeners, manifest,
    skips: () => skips, claims: () => claims,
    setOffline(value) { offline = value; },
    async lifecycle(type) {
      let promise;
      listeners.get(type)({ waitUntil(value) { promise = value; } });
      await promise;
    },
    async request(pathname, { method = 'GET', range = '', external = false } = {}) {
      let response;
      const request = new Request((external ? 'https://external.example' : origin) + pathname,
        { method, headers: range ? { Range: range } : {} });
      listeners.get('fetch')({ request, respondWith(value) { response = value; } });
      return response ? await response : null;
    },
  };
}

test('installation verifies every manifest byte, publishes complete shell and activates without catalog eviction', async () => {
  const ui = service();
  await ui.caches.open('pmm-shell-v1-old');
  await ui.caches.open('pmm-catalog-v1');
  await ui.caches.open('pmm-guides-v1');
  await ui.lifecycle('install');
  assert.equal(ui.skips(), 1);
  assert.deepEqual(ui.requests.map(item => item.url), ['/shell-manifest.json', '/', '/assets/search.js', '/assets/fonts/example.woff2']);
  assert.equal(ui.requests[0].options.cache, 'no-store');
  assert.ok(ui.requests.slice(1).every(item => item.options.cache === 'reload'));
  const store = await ui.caches.open('pmm-shell-v1-current');
  assert.equal(store.entries.size, ui.manifest.assets.length + 1);
  await ui.lifecycle('activate');
  assert.equal(ui.stores.has('pmm-shell-v1-old'), false);
  assert.equal(ui.stores.has('pmm-catalog-v1'), true);
  assert.equal(ui.stores.has('pmm-guides-v1'), true);
  assert.equal(ui.claims(), 1);
});

test('offline reload serves root with guide queries, app script and font from one coherent shell', async () => {
  const ui = service(); await ui.lifecycle('install'); ui.setOffline(true);
  const count = ui.requests.length;
  assert.match(await (await ui.request('/?guide=one')).text(), /Offline repair library/);
  assert.match(await (await ui.request('/index.html?guide=two')).text(), /Offline repair library/);
  assert.equal(await (await ui.request('/assets/search.js')).text(), 'native search');
  assert.equal(await (await ui.request('/assets/fonts/example.woff2')).text(), 'wOF2');
  assert.equal(ui.requests.length, count, 'cached shell requests do not touch the network');
});

test('partial or changed installation never replaces the previous shell', async () => {
  const ui = service({ corruptAsset: '/assets/search.js' });
  const prior = await ui.caches.open('pmm-shell-v1-old');
  await prior.put('/', new Response('last good shell'));
  await assert.rejects(ui.lifecycle('install'), /changed while installing/);
  assert.equal(ui.skips(), 0);
  assert.equal(ui.stores.has('pmm-shell-v1-current'), false);
  assert.equal(await (await prior.match('/')).text(), 'last good shell');
});

test('service worker leaves catalog, guides, media, foreign URLs and non-GET/ranges to their owners', async () => {
  const ui = service();
  for (const target of ['/api/catalog', '/api/guides/one', '/api/art/hash.json', '/media/hash.png', '/sw.js']) {
    assert.equal(await ui.request(target), null);
  }
  assert.equal(await ui.request('/assets/search.js', { external: true }), null);
  assert.equal(await ui.request('/', { method: 'POST' }), null);
  assert.equal(await ui.request('/assets/fonts/example.woff2', { range: 'bytes=0-3' }), null);
  assert.equal(ui.requests.length, 0);
});
