// Measures the real worker's search computation without opening a browser.
const fs = require('node:fs');
const os = require('node:os');
const vm = require('node:vm');
const { performance } = require('node:perf_hooks');
const { spawnSync } = require('node:child_process');

const commonQueries = ['fridge', 'frid thermostat', 'laptop battery', 'macbook notebook battery',
  'cellphone', 'tumble dryer', 'washer', 'macbook air', 'Samsung Galaxy S23', 'noresultzzzz'];
const queries = [...commonQueries, 'laptop batteries', 'repairing laptop', 'laptpp batery',
  'refrxxerator', 'frdge thermostat', 'samsung galaxi S23', 'Samsung Galaxy S24', 'A1708', 'A1709'];
const stressQueries = ['a', 'r'];
const coldQueries = ['laptpp batery', 'refrxxerator', 'noresultzzzz'];
const rounded = value => +value.toFixed(3);
function percentiles(values) {
  const sorted = [...values].sort((a, b) => a - b);
  return { p50Ms: rounded(sorted[Math.floor(sorted.length * .5)]),
    p95Ms: rounded(sorted[Math.floor(sorted.length * .95)]) };
}

async function settledMemory() {
  global.gc?.();
  await new Promise(resolve => setTimeout(resolve, 0));
  global.gc?.();
  return process.memoryUsage();
}

async function benchmark(indexPath, sourcePath, firstOnlyQuery) {
  const before = await settledMemory();
  const index = JSON.parse(fs.readFileSync(indexPath, 'utf8'));
  const messages = [];
  let requests = 0;
  const context = vm.createContext({ self: {}, performance, setTimeout, clearTimeout,
    atob: value => Buffer.from(value, 'base64').toString('binary'),
    btoa: value => Buffer.from(value, 'binary').toString('base64'),
    fetch: async (url, options) => {
      if (url !== '/api/catalog') throw new Error('Unexpected external request: ' + url);
      if (options.mode !== 'same-origin') throw new Error('Index fetch must stay same-origin');
      requests++;
      return { ok: true, json: async () => index };
    },
    postMessage: message => messages.push(message),
  });
  vm.runInContext(fs.readFileSync(sourcePath, 'utf8'), context);
  async function next(type) {
    const deadline = performance.now() + 30000;
    while (performance.now() < deadline) {
      const at = messages.findIndex(message => message.type === type || message.type === 'error');
      if (at >= 0) {
        const message = messages.splice(at, 1)[0];
        if (message.type === 'error') throw new Error(message.message);
        return message;
      }
      await new Promise(resolve => setTimeout(resolve, 1));
    }
    throw new Error('Worker did not reply within 30 seconds');
  }
  const ready = await next('ready');
  const after = await settledMemory();
  const preparedHeapMiB = (after.heapUsed - before.heapUsed) / 1024 ** 2;
  const preparedExternalMiB = (after.external - before.external) / 1024 ** 2;
  const preparedArrayBufferMiB = (after.arrayBuffers - before.arrayBuffers) / 1024 ** 2;
  const samples = [], commonSamples = [], firstPass = [], totals = {}, perQuery = {};
  let requestId = 0;
  async function run(query) {
    context.self.onmessage({ data: { type: 'search', query, requestId: ++requestId, count: 30 } });
    const response = await next('results');
    if (response.requestId !== requestId) throw new Error('Worker returned a stale query');
    if (!Number.isFinite(response.duration)) throw new Error('Worker did not report search computation time');
    totals[query] = response.total;
    return response;
  }
  if (firstOnlyQuery !== undefined) {
    const response = await run(firstOnlyQuery);
    if (requests !== 1) throw new Error('Search made extra requests');
    return { query: firstOnlyQuery, total: response.total, firstQueryMs: rounded(response.duration),
      workerLoadMs: rounded(ready.loadDuration), indexRequests: requests };
  }
  // Keep initial searches separate: warm repetitions can hide expansion/trie work.
  // This pass starts with empty caches, but later queries can reuse shared terms.
  for (const query of queries) {
    const response = await run(query);
    firstPass.push(response.duration);
    perQuery[query] = { total: response.total, firstMs: rounded(response.duration),
      firstIds: response.items.slice(0, 3).map(row => row[0]), samples: [] };
  }
  for (let round = 0; round < 13; round++) {
    for (const query of queries) {
      const response = await run(query);
      if (response.total !== perQuery[query].total) throw new Error('Search result count changed between repetitions');
      if (round >= 3) {
        samples.push(response.duration);
        perQuery[query].samples.push(response.duration);
        if (commonQueries.includes(query)) commonSamples.push(response.duration);
      }
    }
  }
  for (const entry of Object.values(perQuery)) {
    Object.assign(entry, percentiles(entry.samples));
    delete entry.samples;
  }
  const stress = {};
  for (const query of stressQueries) {
    const response = await run(query);
    const timings = [];
    for (let round = 0; round < 10; round++) timings.push((await run(query)).duration);
    stress[query] = { total: response.total, firstMs: rounded(response.duration), ...percentiles(timings) };
  }
  if (requests !== 1) throw new Error('Search made extra requests');
  const regular = percentiles(samples);
  const packed = indexPath + '.gz';
  return { documents: index.items.length, aliasGroups: ready.aliasGroups || 0,
    invertedIndex: ready.invertedIndex || false, termCount: ready.termCount || 0,
    indexJsonBytes: fs.statSync(indexPath).size,
    indexGzipBytes: fs.existsSync(packed) ? fs.statSync(packed).size : null,
    workerLoadMs: rounded(ready.loadDuration), preparedHeapMiB: rounded(preparedHeapMiB),
    preparedExternalMiB: rounded(preparedExternalMiB), preparedArrayBufferMiB: rounded(preparedArrayBufferMiB),
    searchP50Ms: regular.p50Ms, searchP95Ms: regular.p95Ms,
    firstPass: percentiles(firstPass), commonQueries: percentiles(commonSamples),
    measuredQueries: samples.length, indexRequests: requests, totals, perQuery, stress };
}

(async () => {
  if (process.argv[2] === '--cold-query') {
    console.log(JSON.stringify(await benchmark(process.argv[3], process.argv[4], process.argv[5])));
    return;
  }
  if (process.argv[2] === '--single') {
    console.log(JSON.stringify(await benchmark(process.argv[3], process.argv[4])));
    return;
  }
  const [beforeIndex, beforeWorker, afterIndex, afterWorker] = process.argv.slice(2);
  if (!afterWorker) throw new Error('Usage: node --expose-gc scripts/bench_search.cjs before.json before-worker.js after.json after-worker.js');
  function isolated(index, source, query) {
    const args = ['--expose-gc', __filename, query === undefined ? '--single' : '--cold-query', index, source];
    if (query !== undefined) args.push(query);
    const result = spawnSync(process.execPath, args, { encoding: 'utf8' });
    if (result.status !== 0) throw new Error(result.stderr || 'Benchmark failed');
    return JSON.parse(result.stdout);
  }
  const before = isolated(beforeIndex, beforeWorker);
  const after = isolated(afterIndex, afterWorker);
  // Each typo/no-match cold probe is the very first search in its own process
  // and fresh worker: neither token caches nor earlier searches can hide work.
  before.coldQueries = {};
  after.coldQueries = {};
  for (const query of coldQueries) {
    before.coldQueries[query] = isolated(beforeIndex, beforeWorker, query);
    after.coldQueries[query] = isolated(afterIndex, afterWorker, query);
  }
  console.log(JSON.stringify({ environment: 'Node ' + process.version + ', worker computation (not browser end-to-end)',
    hardware: { platform: process.platform, architecture: process.arch,
      cpu: os.cpus()[0]?.model, logicalCpus: os.cpus().length },
    firstPassPolicy: 'Initial pass precedes warmups in one fresh worker per build; later queries can reuse earlier shared terms.',
    coldQueryPolicy: 'Each coldQueries probe is the first search in its own new process and worker, with empty caches.',
    memoryPolicy: 'Deltas before JSON parsing versus after startup; both settle with GC, an event-loop turn, then GC again. External includes ArrayBuffer memory, so do not add both.',
    before, after }, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
