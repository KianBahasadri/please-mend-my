// Measures the real worker's search computation without opening a browser.
const fs = require('node:fs');
const vm = require('node:vm');
const { performance } = require('node:perf_hooks');
const { spawnSync } = require('node:child_process');

async function benchmark(indexPath, sourcePath) {
  global.gc?.();
  const before = process.memoryUsage().heapUsed;
  const index = JSON.parse(fs.readFileSync(indexPath, 'utf8'));
  const messages = [];
  let requests = 0;
  const context = vm.createContext({ self: {}, performance, setTimeout,
    fetch: async (url) => {
      if (url !== '/api/catalog') throw new Error('Unexpected external request: ' + url);
      requests++;
      return { ok: true, json: async () => index };
    },
    postMessage: message => messages.push(message),
  });
  vm.runInContext(fs.readFileSync(sourcePath, 'utf8'), context);
  async function next(type) {
    while (true) {
      const at = messages.findIndex(message => message.type === type || message.type === 'error');
      if (at >= 0) {
        const message = messages.splice(at, 1)[0];
        if (message.type === 'error') throw new Error(message.message);
        return message;
      }
      await new Promise(resolve => setTimeout(resolve, 1));
    }
  }
  const ready = await next('ready');
  global.gc?.();
  const preparedHeapMiB = (process.memoryUsage().heapUsed - before) / 1024 ** 2;
  const queries = ['fridge', 'frid thermostat', 'laptop battery', 'macbook notebook battery',
    'cellphone', 'tumble dryer', 'washer', 'macbook air', 'Samsung Galaxy S23', 'noresultzzzz'];
  const samples = [], totals = {};
  let requestId = 0;
  for (let round = 0; round < 13; round++) {
    for (const query of queries) {
      context.self.onmessage({ data: { type: 'search', query, requestId: ++requestId, count: 30 } });
      const response = await next('results');
      totals[query] = response.total;
      if (round >= 3) samples.push(response.duration);
    }
  }
  samples.sort((a, b) => a - b);
  if (requests !== 1) throw new Error('Search made extra requests');
  return { documents: index.items.length, aliasGroups: ready.aliasGroups || 0,
    workerLoadMs: +ready.loadDuration.toFixed(2), preparedHeapMiB: +preparedHeapMiB.toFixed(2),
    searchP50Ms: +samples[Math.floor(samples.length * .5)].toFixed(2),
    searchP95Ms: +samples[Math.floor(samples.length * .95)].toFixed(2),
    measuredQueries: samples.length, indexRequests: requests, totals };
}

(async () => {
  if (process.argv[2] === '--single') {
    console.log(JSON.stringify(await benchmark(process.argv[3], process.argv[4])));
    return;
  }
  const [beforeIndex, beforeWorker, afterIndex, afterWorker] = process.argv.slice(2);
  if (!afterWorker) throw new Error('Usage: node --expose-gc scripts/bench_search.cjs before.json before-worker.js after.json after-worker.js');
  function isolated(index, source) {
    const result = spawnSync(process.execPath, ['--expose-gc', __filename, '--single', index, source], { encoding: 'utf8' });
    if (result.status !== 0) throw new Error(result.stderr || 'Benchmark failed');
    return JSON.parse(result.stdout);
  }
  const before = isolated(beforeIndex, beforeWorker);
  const after = isolated(afterIndex, afterWorker);
  console.log(JSON.stringify({ environment: 'Node ' + process.version + ', worker computation (not browser end-to-end)', before, after }, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
