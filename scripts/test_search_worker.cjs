const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { execFileSync } = require('node:child_process');
const { performance } = require('node:perf_hooks');

// Exercise the actual offline producer and browser worker together. No manually
// duplicated postings fixture can hide a mismatch between Python and JavaScript.
function prepare(index, guideRecords) {
  return JSON.parse(execFileSync(process.env.PMM_TEST_PYTHON || 'python3', ['-c',
    'import json,sys; sys.path.insert(0,sys.argv[1]); from prepare_search import prepare_index; '
      + 'request=json.load(sys.stdin); index=request["index"]; '
      + 'index["search"]["inverted"]=prepare_index(index,request.get("guideRecords")); json.dump(index,sys.stdout)',
    __dirname], { input: JSON.stringify({ index, guideRecords }), encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 }));
}

async function workerFor(index, source = fs.readFileSync(path.join(__dirname, '../assets/search-worker.js'), 'utf8'), extras = {}) {
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
    ...extras,
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

test('the worker sends release archetypes once with readiness and never refetches them per query', async () => {
  const index = fixture();
  index.archetypes = { version: 1, revision: index.revision, archetypes: [{
    id: 'oewn-fixture-laptop-n', label: 'Laptop', aliases: ['laptop'], guideCount: 2,
    parts: [{ id: 'battery', label: 'Battery', query: 'battery', aliases: ['battery'] }], artUrl: null,
  }], models: [{ name: 'MacBook A2179', nounId: 'oewn-fixture-laptop-n' }] };
  const worker = await workerFor(prepare(index));
  assert.equal(typeof worker.ready.archetypesJSON, 'string');
  assert.deepEqual(JSON.parse(worker.ready.archetypesJSON), index.archetypes);
  assert.equal(worker.ready.archetypes, undefined, 'the object is not also copied across the worker boundary');
  await worker.search('laptop battery');
  await worker.search('MacBook A2179');
  assert.equal(worker.fetches(), 1);
  assert.equal(worker.messages.some(message => message.type === 'ready'), false);
  const legacy = await workerFor(prepare(fixture()));
  assert.equal(legacy.ready.archetypesJSON, '', 'legacy catalogs do not gain a hardcoded noun subset');
});

function symptomFixture() {
  const anaphylaxis = '00000000000000000001';
  const other = '00000000000000000002';
  const hinge = '00000000000000000003';
  const index = { version: 1, revision: 'symptom-fixture', source: {},
    categories: [{ id: 'health', name: 'Health & medicine' }, { id: 'household', name: 'Household' }],
    items: [
      [anaphylaxis, 'Anaphylaxis', 'health', 'article', ''],
      [other, 'Allergy Care', 'health', 'article', ''],
      [hinge, 'Door Hinge Repair', 'household', 'guide', 'Door'],
    ],
    search: { version: 1, groups: ['', 'allergy\nallergic'], itemGroups: [1, 1, 0] },
  };
  const guideRecords = {
    [anaphylaxis]: { id: anaphylaxis, version: 2,
      symptoms: ['Throat swelling', 'Hives', 'Impending doom', 'Shortness of breath'],
      materials: ['Epinephrine autoinjector'] },
    [hinge]: { id: hinge, version: 2,
      symptoms: ['Squeaky door hinge'], materials: ['Mineral oil'] },
  };
  return { index, guideRecords, anaphylaxis, other, hinge };
}

test('AI symptoms and materials become browser-resident matches without guide fetches', async () => {
  const { index, guideRecords, anaphylaxis, hinge } = symptomFixture();
  const before = await workerFor(prepare(index));
  assert.equal((await before.search('throat swelling')).total, 0);
  assert.equal((await before.search('hives')).total, 0);
  assert.equal((await before.search('mineral oil')).total, 0);
  const prepared = prepare(index, guideRecords);
  assert.deepEqual(prepared.items, index.items, 'new terms must not change the five-field public rows');
  const worker = await workerFor(prepared);
  for (const query of ['throat swelling', 'hives', 'impending doom', 'shortness of breath',
    'epinephrine autoinjector', 'my throat is swelling']) {
    assert.deepEqual(Array.from((await worker.search(query)).items, row => row[0]), [anaphylaxis], query);
  }
  for (const query of ['mineral oil', 'squeaky door hinge']) {
    assert.deepEqual(Array.from((await worker.search(query)).items, row => row[0]), [hinge], query);
  }
  assert.equal(before.fetches(), 1);
  assert.equal(worker.fetches(), 1, 'symptom/material queries use only the initial index, with no guide/API requests');
});

test('AI postings remain document-specific and retain stem, typo and alias intersections', async () => {
  const { index, guideRecords, anaphylaxis, other, hinge } = symptomFixture();
  const worker = await workerFor(prepare(index, guideRecords));
  assert.deepEqual(new Set(Array.from((await worker.search('allergy')).items, row => row[0])),
    new Set([anaphylaxis, other]));
  for (const query of ['allergy throat swelling', 'throats swelling', 'throar swelling', 'autoinjecter']) {
    assert.deepEqual(Array.from((await worker.search(query)).items, row => row[0]), [anaphylaxis], query);
  }
  assert.deepEqual(Array.from((await worker.search('minerals oil')).items, row => row[0]), [hinge]);
  assert.equal((await worker.search('allergy mineral oil')).total, 0, 'a material must not leak into unrelated alias groups');
  assert.equal((await worker.search('hives mineral oil')).total, 0, 'terms from different guides cannot form one match');
  assert.equal(worker.fetches(), 1);
});

test('symptom enrichment preserves exact title priority and literal model matching', async () => {
  const { index, guideRecords, anaphylaxis } = symptomFixture();
  const exact = '00000000000000000004';
  index.items.push([exact, 'Hives', 'health', 'article', '']);
  index.items.push(['s23', 'Samsung Galaxy S23 Screen Replacement', 'household', 'guide', 'Samsung Galaxy S23']);
  index.items.push(['s24', 'Samsung Galaxy S24 Screen Replacement', 'household', 'guide', 'Samsung Galaxy S24']);
  index.search.itemGroups.push(0, 0, 0);
  const worker = await workerFor(prepare(index, guideRecords));
  assert.deepEqual(Array.from((await worker.search('hives')).items, row => row[0]), [exact, anaphylaxis]);
  assert.equal((await worker.search('Anaphylaxis')).items[0][0], anaphylaxis);
  assert.deepEqual(Array.from((await worker.search('Galaxy S23')).items, row => row[0]), ['s23']);
  assert.deepEqual(Array.from((await worker.search('Galaxy S24')).items, row => row[0]), ['s24']);
  assert.equal((await worker.search('Galaxy S25')).total, 0);
  assert.equal(worker.fetches(), 1);
});

test('numeric AI materials retain exact identifier and punctuation checks', async () => {
  const { index, guideRecords, anaphylaxis, hinge } = symptomFixture();
  guideRecords[anaphylaxis].materials.push('Antihistamines (H1 and H2)');
  guideRecords[hinge].materials.push('Threadlocker 243', '3M 300LSE adhesive');
  const prepared = prepare(index, guideRecords);
  assert.deepEqual(prepared.search.inverted.guideLiterals.map(([doc]) => doc), [0, 2]);
  const worker = await workerFor(prepared);
  for (const query of ['antihistamines H1', 'antihistamines H2', 'H1']) {
    assert.deepEqual(Array.from((await worker.search(query)).items, row => row[0]), [anaphylaxis], query);
  }
  for (const query of ['threadlocker 243', '3M 300LSE adhesive']) {
    assert.deepEqual(Array.from((await worker.search(query)).items, row => row[0]), [hinge], query);
  }
  for (const query of ['antihistamines H3', 'antihistamines H1+', 'threadlocker 244', '3M 300LSE+', 'allergy 243']) {
    assert.equal((await worker.search(query)).total, 0, query);
  }
  assert.equal(worker.fetches(), 1);
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

test('conversational qualifiers and intensifiers do not become required search terms', async () => {
  const index = fixture();
  index.items = [
    ['arm', 'Broken Arm', 'technology', 'article', ''],
    ['arm-guide', 'Arm Bone Broken Repair', 'technology', 'guide', ''],
    ['dick', 'Dick problems', 'technology', 'article', ''],
    ['niche', 'Dick smith 2 channel dvd player Disassembly', 'technology', 'guide', ''],
    ['hurt', 'Arm Hurts', 'technology', 'article', ''],
  ];
  index.search = { version: 1, groups: [''], itemGroups: index.items.map(() => 0) };
  for (const catalog of [index, prepare(index)]) {
    const worker = await workerFor(catalog);
    const ids = async query => Array.from((await worker.search(query)).items, row => row[0]);
    const broken = await ids('arm broken');
    for (const query of ["arm it's broken", 'arm it’s broken', 'arm it is broken', 'MY ARM IS BROKEN',
      "my fucking arm it's broken", 'the damn arm is broken']) {
      assert.deepEqual(await ids(query), broken, query);
    }
    const dick = await ids('dick');
    for (const query of ['fucking dick', 'my fucking dick', 'FUCKING, DICK!', 'goddamn dick']) {
      assert.deepEqual(await ids(query), dick, query);
      assert.equal((await worker.search(query)).items[0][0], 'dick');
    }
    assert.deepEqual(await ids('arm it hurts'), await ids('arm hurts'));
    assert.equal((await worker.search('arm it hurts')).items[0][0], 'hurt');
    assert.equal(worker.fetches(), 1, 'conversational searches use only the already fetched browser index');
  }
});

test('negation and medical symptom adjectives remain required', async () => {
  const index = fixture();
  index.items = [
    ['positive', 'Laptop Working', 'technology', 'guide', ''],
    ['notebook', 'Laptop Notebook Working', 'technology', 'guide', ''],
    ['negative', 'Laptop Not Working', 'technology', 'guide', ''],
    ['power', 'Laptop Power', 'technology', 'guide', ''],
    ['no-power', 'Laptop No Power', 'technology', 'guide', ''],
    ['urine', 'Urine care', 'technology', 'article', ''],
    ['bloody', 'Bloody Urine', 'technology', 'article', ''],
  ];
  index.search = { version: 1, groups: [''], itemGroups: index.items.map(() => 0) };
  for (const catalog of [index, prepare(index)]) {
    const worker = await workerFor(catalog);
    for (const query of ['laptop not working', "laptop it's not working", 'laptop it is not working',
      "laptop isn't working", 'laptop it isn’t working', 'laptop it does not work']) {
      assert.deepEqual(Array.from((await worker.search(query)).items, row => row[0]), ['negative'], query);
    }
    assert.deepEqual(Array.from((await worker.search("laptop it's no power")).items, row => row[0]), ['no-power']);
    assert.deepEqual(Array.from((await worker.search('my bloody urine')).items, row => row[0]), ['bloody']);
    assert.equal(worker.fetches(), 1);
  }
});

test('qualifier handling preserves exact literal titles, possessives and strict model identifiers', async () => {
  const index = fixture();
  index.items = [
    ['plain', 'Arm Broken', 'technology', 'article', ''],
    ['literal', "Arm It's Broken", 'technology', 'guide', ''],
    ['profanity', 'Fucking Dick', 'technology', 'guide', ''],
    ['general', 'Dick problems', 'technology', 'article', ''],
    ['single', 'Fucking', 'technology', 'article', ''],
    ['possessive', "John's Laptop Repair", 'technology', 'guide', ''],
    ['s23', 'Samsung Galaxy S23 Screen Replacement', 'technology', 'guide', ''],
    ['s24', 'Samsung Galaxy S24 Screen Replacement', 'technology', 'guide', ''],
    ['is-model', 'Lexus IS 200 Screen Replacement', 'technology', 'guide', ''],
    ['es-model', 'Lexus ES 200 Screen Replacement', 'technology', 'guide', ''],
  ];
  index.search = { version: 1, groups: [''], itemGroups: index.items.map(() => 0) };
  for (const catalog of [index, prepare(index)]) {
    const worker = await workerFor(catalog);
    assert.equal((await worker.search("Arm It's Broken")).items[0][0], 'literal');
    assert.equal((await worker.search('Fucking Dick')).items[0][0], 'profanity');
    assert.equal((await worker.search('fucking')).items[0][0], 'single', 'standalone intensifiers retain their search meaning');
    assert.equal((await worker.search("John's Laptop Repair")).items[0][0], 'possessive');
    assert.deepEqual(Array.from((await worker.search('my Samsung Galaxy S23 screen')).items, row => row[0]), ['s23']);
    assert.equal((await worker.search('my Samsung Galaxy S23+ screen')).total, 0);
    assert.equal((await worker.search('my Samsung Galaxy S25 screen')).total, 0);
    assert.deepEqual(Array.from((await worker.search('my Lexus IS 200 screen')).items, row => row[0]), ['is-model']);
  }
});

test('qualifier-expanded broad searches still expose only the best 100 entries', async () => {
  const index = fixture();
  index.items = Array.from({ length: 180 }, (_, at) => ['arm-' + at, 'Arm Broken ' + at, 'technology', 'guide', '']);
  index.search = { version: 1, groups: [''], itemGroups: index.items.map(() => 0) };
  const worker = await workerFor(prepare(index));
  const result = await worker.search("my fucking arm it's broken");
  assert.equal(result.total, 100);
  worker.send({ type: 'range', requestId: result.requestId, rangeId: 1, start: 100, count: 60 });
  assert.equal((await worker.next('range')).items.length, 0);
  assert.equal(worker.fetches(), 1);
});

test('negative contractions retain exact literal titles even when their expanded token is absent', async () => {
  const index = fixture();
  index.items = [
    ['plain', 'Laptop Not Working', 'technology', 'guide', ''],
    ['literal', "Laptop Isn't Working", 'technology', 'guide', ''],
    ['panic', "Don't Panic", 'technology', 'article', ''],
  ];
  index.search = { version: 1, groups: [''], itemGroups: index.items.map(() => 0) };
  for (const catalog of [index, prepare(index)]) {
    const worker = await workerFor(catalog);
    assert.equal((await worker.search("Laptop Isn't Working")).items[0][0], 'literal');
    assert.equal((await worker.search("Don't Panic")).items[0][0], 'panic');
    assert.equal(worker.fetches(), 1);
  }
});

test('complete literal title words outrank partial niche guides while exact order stays first', async () => {
  const index = fixture();
  index.items = [
    ['niche', 'How to Fix a Broken Temple Arm of Gaoye Blue Light Eyeglasses', 'technology', 'guide', ''],
    ['complete', 'Broken arm', 'technology', 'article', ''],
    ['duplicate', 'Arm arm', 'technology', 'guide', 'Broken'],
    ['prefix', 'Arm Broken Hardware Specific Device Repair', 'technology', 'guide', ''],
  ];
  index.search = { version: 1, groups: [''], itemGroups: index.items.map(() => 0) };
  for (const catalog of [index, prepare(index)]) {
    const worker = await workerFor(catalog);
    for (const query of ['arm broken', "arm it's broken", 'my fucking arm it is broken']) {
      assert.equal((await worker.search(query)).items[0][0], 'complete', query);
    }
    assert.equal((await worker.search(index.items[0][1])).items[0][0], 'niche', 'an exact detailed title remains first');
    assert.equal((await worker.search(index.items[3][1])).items[0][0], 'prefix');
    assert.equal(worker.fetches(), 1);
  }
  index.items.push(['ordered', 'Arm broken', 'technology', 'article', '']);
  index.search.itemGroups.push(0);
  const worker = await workerFor(prepare(index));
  assert.equal((await worker.search('arm broken')).items[0][0], 'ordered', 'exact requested order outranks reordered words');
});

test('long queries retain every required term beyond the old compact mask width', async () => {
  const index = fixture();
  const terms = Array.from({ length: 33 }, (_, index) => 'required' + String.fromCharCode(97 + Math.floor(index / 26), 97 + index % 26));
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
  const expected = ['exact-guide', ...Array.from({ length: 99 }, (_, at) => 'prefix-' + at)];
  for (const catalog of [index, prepare(index)]) {
    const worker = await workerFor(catalog);
    const result = await worker.search('laptop');
    worker.send({ type: 'range', requestId: result.requestId, rangeId: 1, start: 60, count: 60 });
    const tail = await worker.next('range');
    assert.equal(result.total, 100);
    assert.deepEqual(Array.from([...result.items, ...tail.items], row => row[0]), expected);
    assert.equal((await worker.search('laptop')).items[0][0], 'exact-guide', 'repeat queries retain the same ranking');
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
    ['general-car', 'Car care', 'technology', 'article', ''],
    ['niche-laptop', 'Laptop Cooling Pad Airflow Improvement and Performance Optimization Technique', 'technology', 'guide', ''],
    ['general-laptop', 'Laptop care', 'technology', 'article', ''],
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
  for (const query of ['laptop', 'lap', 'notebook', 'laptpp', 'laptop care', 'care laptop']) {
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

test('prepared single-letter results exactly match live scoring across titles, aliases and kinds', async () => {
  const index = fixture();
  index.items = [];
  index.search = { version: 1, groups: ['', 'alpha repair', 'battery\nλ'], itemGroups: [] };
  for (let at = 0; at < 520; at++) {
    const kind = ['guide', 'article', 'topic'][at % 3];
    const title = ['Artery', 'Repair Battery', 'Solar panel', 'Lambda λ', '猫 repair', 'X repair'][at % 6] + (at % 13 ? ' ' + at : '');
    index.items.push(['letter-' + at, title, 'technology', kind, at % 4 ? '' : 'Alpha Model A23']);
    index.search.itemGroups.push(at % 3);
  }
  index.items.push(['exact-a', 'A', 'technology', 'article', '']);
  index.search.itemGroups.push(0);
  const prepared = prepare(index), live = structuredClone(prepared);
  assert.ok(prepared.search.inverted.shortPrefixes.queries.length);
  delete live.search.inverted.shortPrefixes;
  const fastWorker = await workerFor(prepared), liveWorker = await workerFor(live);
  for (const query of ['a', 'r', 'b', 's', 'λ', '猫', '1', 'A23']) {
    const fast = await fastWorker.search(query), full = await liveWorker.search(query);
    fastWorker.send({ type: 'range', requestId: fast.requestId, rangeId: 1, start: 60, count: 40 });
    liveWorker.send({ type: 'range', requestId: full.requestId, rangeId: 1, start: 60, count: 40 });
    const fastTail = await fastWorker.next('range'), liveTail = await liveWorker.next('range');
    assert.equal(fast.total, full.total, query);
    assert.deepEqual(Array.from([...fast.items, ...fastTail.items], r => r[0]), Array.from([...full.items, ...liveTail.items], r => r[0]), query);
  }
  const first = await fastWorker.search('a'), again = await fastWorker.search('a');
  assert.notEqual(first.requestId, again.requestId);
  assert.deepEqual(Array.from(first.items, r => r[0]), Array.from(again.items, r => r[0]));
});

test('invalid prepared short-prefix documents fail startup', async () => {
  const prepared = prepare(fixture());
  prepared.search.inverted.shortPrefixes.queries = [['a', [999999]]];
  await assert.rejects(workerFor(prepared), /inverted index is invalid/);
});

test('selective token probes match full expansions and never poison later queries', async () => {
  const index = fixture();
  index.items = [];
  index.search = { version: 1, groups: ['', 'laptop\nnotebook', 'battery'], itemGroups: [] };
  for (let at = 0; at < 700; at++) {
    index.items.push(['probe-' + at, 'How to Repair ' + (at % 4 ? 'Radio' : 'Laptop') + ' Battery ' + at,
      'technology', at % 5 ? 'guide' : 'article', at % 11 ? '' : 'Sony WH-1000XM4']);
    index.search.itemGroups.push(at % 3);
  }
  index.items.push(['rare', 'How to Be an Organized Artist1', 'technology', 'guide', '']);
  index.search.itemGroups.push(0);
  const prepared = prepare(index);
  const source = fs.readFileSync(path.join(__dirname, '../assets/search-worker.js'), 'utf8');
  const fullSource = source.replace('const useRestricted = candidates && !plan.cached &&',
    'const useRestricted = false && candidates && !plan.cached &&');
  assert.notEqual(source, fullSource);
  const fast = await workerFor(prepared), full = await workerFor(prepared, fullSource);
  for (const query of ['How to Be an Organized Artist1', 'laptop battery', 'repairing laptop', 'battery laptop',
    'battery', 'how', 'laptop', 'laptpp batery', 'WH-1000XM4 battery', 'WH-1000XM5 battery', 'laptop not radio']) {
    const a = await fast.search(query), b = await full.search(query);
    fast.send({ type: 'range', requestId: a.requestId, rangeId: 1, start: 60, count: 40 });
    full.send({ type: 'range', requestId: b.requestId, rangeId: 1, start: 60, count: 40 });
    const aTail = await fast.next('range'), bTail = await full.next('range');
    assert.equal(a.total, b.total, query);
    assert.deepEqual(Array.from([...a.items, ...aTail.items], r => r[0]), Array.from([...b.items, ...bTail.items], r => r[0]), query);
  }
});

test('published topic anchors stay resident while only actionable source matches become cards', async () => {
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
    ['laptop-action', 'Laptop Battery Replacement', 'technology', 'guide', 'PC Laptop'],
  ];
  index.search = { version: 1, groups: ['', 'laptop\nnotebook\nlaptops', 'electronics'],
    itemGroups: [0, 2, 0, 2, 2, 1, 1, 0, 1],
    sources: [{ label: 'laptop', aliases: ['notebook'], curated: ['laptops'],
      ifixitPaths: ['Device/PC_Laptop', 'Device/Mac_Laptop'] }] };
  for (const catalog of [index, prepare(index)]) {
    const worker = await workerFor(catalog);
    assert.equal((await worker.search('car')).items[0][0], 'car-acronym');
    const laptopRows = (await worker.search('laptop')).items;
    const laptop = laptopRows.map(row => row[0]);
    assert.equal(laptop[0], 'laptop-exact', 'exact title stays ahead of canonical aliases');
    assert.ok(laptopRows.every(row => row[3] === 'guide' || row[3] === 'article'));
    assert.ok(!laptop.some(id => ['laptop-root', 'laptop-mac', 'laptop-component'].includes(id)));
    assert.equal((await worker.search('notebook')).items[0][0], 'laptop-action');
    assert.equal((await worker.search('Laptop Lenovo G510 Keyboard Replacement')).items[0][0], 'laptop-specific');
  }
});

test('topic rows are excluded from all card paths without changing catalog document positions', async () => {
  const index = { version: 1, revision: 'mixed-kind-fixture', source: {},
    categories: [{ id: 'technology', name: 'Technology' }],
    items: [
      ['00000000000000000001', 'Laptop Battery', 'technology', 'topic', 'Laptop'],
      ['00000000000000000002', 'Laptop Battery Care', 'technology', 'article', 'Laptop'],
      ['00000000000000000003', "Laptop Isn't Working", 'technology', 'topic', 'Laptop'],
      ['00000000000000000004', 'Laptop Battery Replacement', 'technology', 'guide', 'Laptop'],
      ['00000000000000000005', "Laptop Isn't Working", 'technology', 'guide', 'Laptop'],
      ['00000000000000000006', 'Laptop hidden metadata', 'technology', 'metadata', 'Laptop'],
    ],
    search: { version: 1, groups: ['', 'laptop\nnotebook'], itemGroups: [1, 1, 1, 1, 1, 1] },
  };
  const enriched = { [index.items[3][0]]: { id: index.items[3][0], version: 2,
    symptoms: ['Charging stops'], materials: ['Threadlocker 243'] } };
  const prepared = prepare(index, enriched);
  assert.deepEqual(prepared.items, index.items);
  assert.equal(prepared.search.inverted.documents, index.items.length);
  assert.deepEqual(prepared.search.inverted.guideLiterals.map(([doc]) => doc), [3], 'sparse literals retain original row positions');
  for (const catalog of [index, prepared]) {
    const worker = await workerFor(catalog);
    assert.equal(worker.ready.total, index.items.length, 'topic files/row identities are still part of the catalog');
    for (const query of ['', 'laptop', 'lap', 'laptop battery', "Laptop Isn't Working", 'notebook battery']) {
      const result = await worker.search(query, 1);
      worker.send({ type: 'range', requestId: result.requestId, rangeId: 1, start: 1, count: 60 });
      const range = await worker.next('range');
      const rows = [...result.items, ...range.items];
      assert.ok(rows.length > 0, query);
      assert.ok(rows.every(row => row[3] === 'guide' || row[3] === 'article'), query);
      assert.ok(rows.every(row => index.items.some(original => original[0] === row[0])), 'IDs were not renumbered');
      assert.equal(result.total, rows.length);
    }
    assert.equal((await worker.search("Laptop Isn't Working")).items[0][0], index.items[4][0],
      'literal title rescue never restores a topic card');
    if (catalog.search.inverted) {
      assert.deepEqual(Array.from((await worker.search('threadlocker 243')).items, row => row[0]), [index.items[3][0]]);
    }
    assert.equal(worker.fetches(), 1);
  }
});

test('ready supplies the exact normalized resident dictionary once, including symptom and material words', async () => {
  const { index, guideRecords } = symptomFixture();
  const prepared = prepare(index, guideRecords);
  const worker = await workerFor(prepared);
  assert.deepEqual(Array.from(worker.ready.terms), prepared.search.inverted.terms);
  assert.equal(worker.ready.termCount, worker.ready.terms.length);
  assert.ok(worker.ready.terms.includes('swelling'));
  assert.ok(worker.ready.terms.includes('mineral'));
  assert.ok(worker.ready.terms.every((term, at, terms) => at === 0 || terms[at - 1] < term));
  await worker.search('hives');
  assert.equal(worker.messages.some(message => message.type === 'ready'), false);
  assert.equal(worker.fetches(), 1);
  const legacy = await workerFor(index);
  assert.deepEqual(Array.from(legacy.ready.terms), []);
});

function bicycleFixture() {
  const items = [
    ['bicycle-topic', 'Bicycle', 'transport', 'topic', 'Bicycle'],
    ['bicycle-light', 'How To Fix A Rear Bike Light', 'transport', 'guide', 'Bicycle'],
    ['bicycle-pedal', 'How To Fix A Loose Bicycle Pedal', 'transport', 'guide', 'Bicycle'],
    ['dirt-tire', 'Dirt Bike Tire Replacement', 'transport', 'guide', 'Motorcycle'],
    ['bicycle-tire', 'Bicycle Tire and Tube Replacement', 'transport', 'guide', 'Bicycle'],
    ['bicycle-brake', 'Bicycle Brake Adjustment', 'transport', 'guide', 'Bicycle'],
    ['bicycle-wheel', 'Bicycle Wheel and Spoke Repair', 'transport', 'guide', 'Bicycle'],
    ['bicycle-seat', 'Bicycle Seat Height Adjustment', 'transport', 'guide', 'Bicycle'],
    ['bicycle-handlebar', 'Bicycle Handlebar Replacement', 'transport', 'guide', 'Bicycle'],
    ['dirt-pedal', 'Dirt Bike Pedal Replacement', 'transport', 'guide', 'Motorcycle'],
    ['motorcycle-prefix', 'Bike Tire Replacement', 'transport', 'guide', 'Motorcycle'],
  ];
  return { version: 1, revision: 'bicycle-fixture', source: {},
    categories: [{ id: 'transport', name: 'Vehicles & transport' }], items,
    search: { version: 1, groups: ['', 'bike\nbicycle\npedal cycle', 'bike\nmotorbike\nmotorcycle'],
      itemGroups: items.map(row => row[4] === 'Bicycle' ? 1 : 2),
      sources: [
        { label: 'bicycle', aliases: ['bike', 'pedal cycle'], curated: [], ifixitPaths: ['Device/Bicycle'] },
        { label: 'motorcycle', aliases: ['bike', 'motorbike'], curated: [], ifixitPaths: ['Device/Motorcycle'] },
      ] },
  };
}

test('primary bicycle synonyms and direct part postings outrank broad alias collisions', async () => {
  const index = bicycleFixture();
  const worker = await workerFor(prepare(index));
  const pedal = await worker.search('bike pedal');
  assert.equal(pedal.items[0][0], 'bicycle-pedal', 'pedal from a real guide beats shared pedal-cycle aliases');
  assert.ok(pedal.items.findIndex(row => row[0] === 'bicycle-pedal') < pedal.items.findIndex(row => row[0] === 'bicycle-light'));
  for (const [part, expected] of [['tire', 'bicycle-tire'], ['tube', 'bicycle-tire'], ['brake', 'bicycle-brake'],
    ['wheel', 'bicycle-wheel'], ['seat', 'bicycle-seat'], ['handlebar', 'bicycle-handlebar']]) {
    assert.equal((await worker.search('bike ' + part)).items[0][0], expected, part);
    assert.equal((await worker.search('bikes ' + part)).items[0][0], expected, 'plural subject ' + part);
    assert.equal((await worker.search('bicycle ' + part)).items[0][0], expected, 'canonical ' + part);
  }
  assert.equal((await worker.search('Dirt Bike Tire Replacement')).items[0][0], 'dirt-tire', 'exact motorcycle procedure remains first');
  assert.equal((await worker.search('dirt bike tire')).items[0][0], 'dirt-tire', 'explicit dirt-bike intent remains required');
  assert.equal((await worker.search('Bike Tire Replacement')).items[0][0], 'motorcycle-prefix', 'an exact motorcycle title still wins');
  assert.equal((await worker.search('motorbike tire')).items[0][4], 'Motorcycle', 'explicit motorcycle synonym overrides the bicycle default');
  assert.ok((await worker.search('bike')).items.every(row => row[3] !== 'topic'));
  assert.equal(worker.fetches(), 1);
});

test('concept preference remains below exact and complete title intent', async () => {
  const index = bicycleFixture();
  index.items.push(['literal-pedal', 'Bike Pedal', 'transport', 'article', '']);
  index.items.push(['literal-reordered', 'Pedal Bike', 'transport', 'article', '']);
  index.search.itemGroups.push(0, 0);
  const worker = await workerFor(prepare(index));
  const result = await worker.search('bike pedal');
  assert.equal(result.items[0][0], 'literal-pedal');
  assert.equal(result.items[1][0], 'literal-reordered');
  assert.equal(result.items[2][0], 'bicycle-pedal');
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

test('malformed sparse guide literals fail closed while older indexes remain valid', async () => {
  const index = prepare(fixture());
  const older = await workerFor(index);
  assert.equal((await older.search('Galaxy S23')).items[0][0], 's23');
  const malformed = [null, {}, 'h1', [null], [[0]], [[0, 'h1', 'extra']],
    [[-1, 'h1']], [[index.items.length, 'h1']], [[true, 'h1']], [['0', 'h1']],
    [[0.5, 'h1']], [[0, null]], [[0, '']], [[0, '   ']], [[0, 'H1']],
    [[0, 'plain text']], [[0, 'h1'], [0, 'h2']], [[1, 'h1'], [0, 'h2']]];
  for (const guideLiterals of malformed) {
    const bad = structuredClone(index);
    bad.search.inverted.guideLiterals = guideLiterals;
    await assert.rejects(workerFor(bad), /prepared search inverted index is invalid/i, JSON.stringify(guideLiterals));
  }
  const empty = structuredClone(index);
  empty.search.inverted.guideLiterals = [];
  assert.equal((await (await workerFor(empty)).search('Galaxy S23')).items[0][0], 's23');
});

// Startup recovery uses a synthetic clock so retry limits/timing do not depend
// on wall time, and cannot silently leave a polling loop running after success.
async function startupWorker(outcomes) {
  let now = 0, timerId = 0;
  const timers = new Map(), requests = [], messages = [], canceled = [];
  const context = vm.createContext({ self: {}, performance: { now: () => now },
    atob: value => Buffer.from(value, 'base64').toString('binary'),
    setTimeout(callback, delay) { const id = ++timerId; timers.set(id, { callback, at: now + delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch: async (url, options) => {
      assert.equal(url, '/api/catalog');
      assert.equal(options.credentials, 'same-origin');
      assert.equal(options.mode, 'same-origin');
      const outcome = outcomes[Math.min(requests.length, outcomes.length - 1)];
      requests.push({ url, options: { ...options }, at: now });
      if (outcome instanceof Error) throw outcome;
      if (typeof outcome === 'number') return { ok: false, status: outcome,
        body: { cancel: async () => { canceled.push(outcome); } } };
      return { ok: true, json: async () => typeof outcome === 'function' ? outcome() : structuredClone(outcome) };
    },
    postMessage(message) { messages.push(structuredClone(message)); },
  });
  async function flush() { for (let at = 0; at < 16; at++) await Promise.resolve(); }
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/search-worker.js'), 'utf8'), context);
  await flush();
  return { requests, messages, timers, canceled,
    send(data) { context.self.onmessage({ data }); },
    async advance(duration) {
      const end = now + duration;
      while (true) {
        const next = [...timers].sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
        if (!next || next[1].at > end) break;
        now = next[1].at;
        timers.delete(next[0]);
        next[1].callback();
        await flush();
      }
      now = end;
      await flush();
    },
  };
}

test('startup503 recovers silently and runs only the latest queued query without further fetches', async () => {
  const worker = await startupWorker([503, fixture()]);
  assert.equal(worker.requests.length, 1);
  assert.equal(worker.requests[0].options.cache, undefined, 'healthy loads retain normal ETag caching');
  assert.deepEqual(worker.messages, [], 'transient failures never flash an error or loading message');
  worker.send({ type: 'search', requestId: 1, query: 'fridge' });
  worker.send({ type: 'search', requestId: 2, query: 'laptop battery' });
  await worker.advance(249);
  assert.equal(worker.requests.length, 1);
  await worker.advance(1);
  assert.equal(worker.requests.length, 2);
  assert.equal(worker.requests[1].options.cache, 'no-store');
  assert.deepEqual(worker.canceled, [503], 'discard the failed response body before retrying');
  assert.deepEqual(worker.messages.map(message => message.type), ['ready', 'results']);
  assert.equal(worker.messages[1].requestId, 2);
  assert.equal(worker.messages[1].total, 2);
  assert.equal(worker.timers.size, 0, 'recovery stops its retry timer');
  worker.send({ type: 'search', requestId: 3, query: 'fridge' });
  await worker.advance(0);
  worker.send({ type: 'range', requestId: 3, rangeId: 1, start: 0, count: 60 });
  await worker.advance(100000);
  assert.equal(worker.messages.at(-2).type, 'results');
  assert.equal(worker.messages.at(-1).type, 'range');
  assert.equal(worker.requests.length, 2, 'search/ranges never request the catalog again');
});

test('startup retries temporary network and server failures, then stops on success', async () => {
  for (const failure of [new TypeError('Failed to fetch'), 429, 500, 502, 504]) {
    const worker = await startupWorker([failure, fixture()]);
    assert.deepEqual(worker.messages, []);
    await worker.advance(250);
    assert.deepEqual(worker.messages.map(message => message.type), ['ready']);
    assert.equal(worker.requests.length, 2);
    assert.equal(worker.timers.size, 0);
  }
});

test('startup retry budget is finite and reports one error only after backoff is exhausted', async () => {
  const worker = await startupWorker([503]);
  for (const delay of [250, 500, 1000, 2000, 4000]) {
    const before = worker.requests.length;
    await worker.advance(delay - 1);
    assert.equal(worker.requests.length, before);
    assert.deepEqual(worker.messages, []);
    await worker.advance(1);
    assert.equal(worker.requests.length, before + 1);
  }
  assert.deepEqual(worker.requests.map(request => request.at), [0, 250, 750, 1750, 3750, 7750]);
  assert.equal(worker.messages.length, 1);
  assert.equal(worker.messages[0].type, 'error');
  assert.match(worker.messages[0].message, /unavailable \(503\)/);
  assert.equal(worker.timers.size, 0);
  await worker.advance(100000);
  assert.equal(worker.requests.length, 6);
  assert.equal(worker.messages.length, 1);
});

test('permanent HTTP failures and invalid catalog payloads are never retried', async () => {
  for (const outcome of [400, 401, 403, 404, { items: [], categories: null },
    () => { throw new SyntaxError('Invalid JSON'); }]) {
    const worker = await startupWorker([outcome]);
    assert.equal(worker.messages.length, 1);
    assert.equal(worker.messages[0].type, 'error');
    assert.equal(worker.requests.length, 1);
    assert.equal(worker.timers.size, 0);
    await worker.advance(100000);
    assert.equal(worker.requests.length, 1);
  }
});

module.exports = { workerFor, prepare };

test('unpunctuated contractions match apostrophized search while preserving negation', async () => {
  const index = fixture();
  index.items = [
    ['negative', 'Laptop Not Working', 'technology', 'guide', 'Laptop'],
    ['positive', 'Laptop Working', 'technology', 'guide', 'Laptop'],
    ['arm', 'Broken arm', 'health', 'article', ''],
  ];
  index.search = { version: 1, groups: [''], itemGroups: [0, 0, 0] };
  for (const catalog of [index, prepare(index)]) {
    const worker = await workerFor(catalog);
    for (const [plain, marked] of [['wont', "won't"], ['cant', "can't"], ['dont', "don't"],
      ['doesnt', "doesn't"], ['didnt', "didn't"], ['isnt', "isn't"], ['arent', "aren't"],
      ['wasnt', "wasn't"], ['hasnt', "hasn't"], ['havent', "haven't"]]) {
      const result = await worker.search('laptop ' + plain + ' working');
      assert.deepEqual(Array.from(result.items, row => row[0]), ['negative'], plain);
      assert.deepEqual(Array.from(result.items, row => row[0]),
        Array.from((await worker.search('laptop ' + marked + ' working')).items, row => row[0]), plain);
    }
    for (const [plain, marked] of [['its', "it's"], ['thats', "that's"], ['im', "i'm"], ['youre', "you're"], ['theyre', "they're"]]) {
      assert.deepEqual(Array.from((await worker.search('arm ' + plain + ' broken')).items, row => row[0]), ['arm'], plain);
      assert.deepEqual(Array.from((await worker.search('arm ' + marked + ' broken')).items, row => row[0]), ['arm'], marked);
    }
    assert.equal(worker.fetches(), 1);
  }
});

function failureFixture() {
  const items = [
    ['dog-aid', 'Dog Emergency First Aid', 'animals', 'article', 'Dog'],
    ['dog-flu', 'How to Diagnose Dog Flu', 'animals', 'guide', 'Dog'],
    ['car-start', 'How to Jump Start a Car', 'vehicles', 'guide', 'Car'],
    ['car-battery', 'Car Battery Repair', 'vehicles', 'guide', 'Car'],
    ['car-trouble', 'Car Troubleshooting', 'vehicles', 'article', 'Car'],
    ['laptop-trouble', 'Laptop Troubleshooting', 'technology', 'article', 'Laptop'],
    ['laptop-hack', 'How to Hack a Dead Laptop Battery', 'technology', 'guide', 'Laptop'],
    ['wrong-subject', 'Phone Not Working', 'technology', 'guide', 'Phone'],
    ['arm', 'Broken arm', 'health', 'article', ''],
    ['model', 'MacBook A2179 Battery Repair', 'technology', 'guide', 'MacBook A2179'],
    ['dog-niche', 'Dog Paw Repair', 'animals', 'guide', 'Dog'],
    ['dog-treats', 'Healthy Emergency Dog Treats', 'animals', 'guide', 'Dog'],
    ['car-kit', 'Car Emergency Kit', 'vehicles', 'guide', 'Car'],
    ['dog-paw-aid', 'Dog Paw Emergency First Aid', 'animals', 'article', 'Dog'],
    ['dog-cpr', 'How to Perform CPR on a Dog', 'animals', 'guide', 'Dog'],
  ];
  return { version: 1, revision: 'failure-fixture', source: {}, categories: [
    { id: 'animals', name: 'Animals' }, { id: 'vehicles', name: 'Vehicles' },
    { id: 'technology', name: 'Technology' }, { id: 'health', name: 'Health' },
  ], items,
    search: { version: 1, groups: ['', 'dog\ndogs', 'car\nautomobile', 'laptop\nnotebook'],
      itemGroups: [1, 1, 2, 2, 2, 3, 3, 2, 0, 3, 1, 1, 2, 1, 1] },
    archetypes: { version: 1, revision: 'failure-fixture', archetypes: ['dog', 'car', 'laptop', 'arm'].map(label => ({
      id: 'noun-' + label, label, aliases: [label], guideCount: 5, parts: [], artUrl: null,
    })), models: [] },
  };
}

test('empty failure intersections relax symptoms within mandatory subject and prefer general diagnosis', async () => {
  const worker = await workerFor(prepare(failureFixture()));
  for (const [query, expected] of [['dog wont start', 'dog-aid'], ['car wont start', 'car-start'],
    ['laptop wont start', 'laptop-trouble'], ['dog not working', 'dog-aid']]) {
    const result = await worker.search(query);
    assert.equal(result.items[0]?.[0], expected, query);
    assert.equal(result.items.some(row => row[0] === 'wrong-subject'), false, query);
    if (query.startsWith('dog')) assert.ok(result.items.some(row => row[0] === 'dog-cpr'), 'available CPR guide participates in triage fallback');
    assert.ok(result.items.every(row => ['guide', 'article'].includes(row[3])));
  }
  assert.equal(worker.fetches(), 1, 'fallback uses resident metadata/postings only');
});

test('failure fallback retains specific symptoms and numeric intent and never reverses not-broken', async () => {
  const worker = await workerFor(prepare(failureFixture()));
  for (const query of ['arm isnt broken', "arm isn't broken", 'laptop A2178 wont start',
    'laptop A2179 wont start', 'unknownsubject wont start', 'laptop missingwidget wont start']) {
    assert.equal((await worker.search(query)).total, 0, query);
  }
  assert.deepEqual(Array.from((await worker.search('dog paw wont start')).items, row => row[0]), ['dog-paw-aid']);
  assert.equal((await worker.search('MacBook A2179 Battery Repair')).items[0][0], 'model');
  assert.equal((await worker.search('Dog Paw Repair')).items[0][0], 'dog-niche', 'strict exact title remains authoritative');
});

function hierarchyFixture() {
  const index = { version: 1, revision: 'hierarchy-fixture', source: {},
    categories: [{ id: 'technology', name: 'Technology' }, { id: 'health', name: 'Health' }],
    items: [
      ['model', 'Dell Inspiron 1500 Screen Replacement', 'technology', 'guide', 'Dell Inspiron 1500'],
      ['laptop', 'Laptop Screen Replacement', 'technology', 'article', 'Laptop'],
      ['computer', 'Computer Screen Repair', 'technology', 'article', 'Computer'],
      ['other-model', 'Dell Inspiron 1501 Screen Replacement', 'technology', 'guide', 'Dell Inspiron 1501'],
      ['choking', 'Choking', 'health', 'article', ''],
      ['dog-choking', 'Dog Choking First Aid', 'health', 'guide', 'Dog'],
      ['wrong-domain', 'Choking Hazard Screen Repair', 'technology', 'guide', 'Machine'],
      ['person-topic', 'Person', 'health', 'topic', ''],
    ],
    search: { version: 1, groups: [''], itemGroups: Array(8).fill(0) },
    archetypes: { version: 1, revision: 'hierarchy-fixture', archetypes: [
      ['computer', 'Computer', 1], ['laptop', 'Laptop', 3], ['dog', 'Dog', 1], ['canine', 'Canine', 1],
    ].map(([id, label, count]) => ({ id, label, aliases: [label.toLowerCase()],
      aliasRanks: { [label.toLowerCase()]: 0 }, guideCount: count, parts: [], artUrl: null })),
    models: [{ name: 'Dell Inspiron 1500', nounId: 'laptop' }] },
  };
  const nodes = [
    ['computer', 'Computer', 'artifact', 5, 1], ['laptop', 'Laptop', 'artifact', 5, 3],
    ['person', 'Person', 'person', 6, 0], ['grandmother', 'Grandmother', 'person', 4, 0],
    ['woman', 'Woman', 'person', 5, 0], ['dog', 'Dog', 'animal', 5, 1],
    ['husky', 'Husky', 'animal', 3, 0], ['canine', 'Canine', 'animal', 4, 1],
    ['choking', 'Choking', 'state', 4, 1],
  ];
  const lists = [[2], [0, 1, 3], [], [], [], [5], [], [5], [4]], data = [], offsets = [0];
  for (const list of lists) {
    let previous = 0;
    for (const doc of list) {
      let delta = doc - previous; previous = doc;
      while (delta >= 128) { data.push((delta & 127) | 128); delta >>>= 7; }
      data.push(delta);
    }
    offsets.push(data.length);
  }
  index.search.hierarchy = { version: 1, fields: ['id', 'label', 'domain', 'frequency', 'guideCount'], nodes,
    parents: [[], [0], [], [4], [2], [7], [5], [], []],
    aliases: [['grandma', 3, 0], ['grandmother', 3, 0], ['human', 2, 0], ['husky', 6, 0]],
    documents: { offsets, data: Buffer.from(data).toString('base64') },
    categories: nodes.map(row => row[2] === 'artifact' ? ['technology'] : ['health']),
    domainCategories: { person: ['health'], animal: ['health'], artifact: ['technology'] },
  };
  return index;
}

test('hierarchy ranks exact known models before nearest class then broader ancestors without changing numeric intent', async () => {
  const worker = await workerFor(prepare(hierarchyFixture()));
  const result = await worker.search('Dell Inspiron 1500 screen');
  assert.deepEqual(Array.from(result.items, row => row[0]), ['model', 'laptop', 'other-model', 'computer']);
  assert.deepEqual(Array.from(result.inheritance, scope => [scope.id, scope.label, scope.distance]),
    [['laptop', 'Laptop', 1], ['other-model', 'Laptop', 1], ['computer', 'Computer', 2]]);
  const again = await worker.search('Dell Inspiron 1500 screen');
  assert.deepEqual(again.items, result.items, 'cached queries retain inherited ranking');
  assert.deepEqual(again.inheritance, result.inheritance, 'cached answers retain their general-guidance labels');
  await worker.search('screen'); await worker.search('replacement');
  for (const query of ['Dell Inspiron 1500 screen replacement', 'Dell Inspiron 1500 replacement screen']) {
    const warm = await worker.search(query);
    assert.equal(warm.items[0][0], 'model', 'warm multiword token plans retain exact model ranking');
    assert.ok(warm.items.some(row => row[0] === 'laptop'));
  }
  assert.deepEqual(Array.from((await worker.search('Dell Inspiron 1501 screen')).items, row => row[0]), ['other-model']);
  for (const query of ['Dell Inspiron 1502 screen', 'Dell Inspiron 1500+ screen', 'Dell Inspiron 1500 screen 9999']) {
    assert.equal((await worker.search(query)).total, 0, query);
  }
  assert.equal(worker.fetches(), 1);
});

test('unfilled WordNet subtypes inherit within their domain and universal human symptoms never become pet/device matches', async () => {
  const worker = await workerFor(prepare(hierarchyFixture()));
  const person = await worker.search('grandma choking');
  assert.deepEqual(Array.from(person.items, row => row[0]), ['choking']);
  assert.deepEqual(Array.from(person.inheritance, scope => [scope.label, scope.distance]), [['person', 32]]);
  const dog = await worker.search('husky choking');
  assert.deepEqual(Array.from(dog.items, row => row[0]), ['dog-choking']);
  assert.equal(dog.inheritance[0].label, 'Dog');
  assert.equal((await worker.search('grandma not choking')).total, 0, 'inherited symptoms preserve negation');
  assert.equal((await worker.search('grandma choking 911')).total, 0, 'residual numeric intent stays mandatory');
  assert.equal((await worker.search('grandma missingwidget')).total, 0);
  assert.equal(worker.fetches(), 1, 'inheritance requires no per-query catalog or guide fetch');
});

test('malformed hierarchy edges, domain categories and topic memberships fail closed at startup', async () => {
  for (const mutate of [
    index => index.search.hierarchy.parents[1].push(99),
    index => index.search.hierarchy.categories[2].push('unknown-category'),
    index => index.search.hierarchy.documents.data = Buffer.from([2, 0, 1, 2, 5, 5, 7]).toString('base64'),
  ]) {
    const index = prepare(hierarchyFixture()); mutate(index);
    await assert.rejects(workerFor(index), /invalid/);
  }
});

test('inheritance never treats a group subject alias as a directly matching requested part', async () => {
  const index = hierarchyFixture();
  index.search.groups.push('dog\npaw');
  index.search.itemGroups[5] = 1;
  const worker = await workerFor(prepare(index));
  assert.equal((await worker.search('husky paw')).total, 0, 'a dog article without direct paw evidence does not inherit a paw repair query');
});

test('inherited parts require raw title/device evidence even when noun expansion added direct postings', async () => {
  const index = hierarchyFixture();
  index.categories.push({ id: 'other', name: 'Other repairs' });
  index.items[5][1] = 'Dog Choking Paw First Aid';
  index.items.push(['balm', 'How to Make Dog Paw Balm', 'other', 'guide', '']);
  index.search.itemGroups.push(0);
  const prepared = prepare(index);
  // Simulate the release's offline noun expansion: the posting remains, but
  // the actual source title/device never named this part.
  prepared.items[5][1] = 'How to Adopt a Working Dog';
  const worker = await workerFor(prepared);
  const result = await worker.search('husky paw');
  assert.deepEqual(Array.from(result.items, row => row[0]), ['balm']);
  assert.equal(result.inheritance[0].label, 'Dog');
  assert.equal(worker.fetches(), 1);
});

test('an alias-only wrong-domain membership cannot veto a canonical ancestor and real part', async () => {
  const index = hierarchyFixture(), metadata = index.search.hierarchy;
  index.categories.push({ id: 'other', name: 'Other repairs' });
  index.items.push(['balm', 'How to Make Dog Paw Balm', 'other', 'guide', '']);
  index.search.itemGroups.push(0);
  // An ambiguous animal-name synonym linked this source to an unrelated
  // mechanical sense. The source never actually names that canonical object.
  metadata.nodes.push(['pawl', 'Pawl', 'artifact', 3, 1]);
  metadata.parents.push([]); metadata.categories.push(['other']);
  const bytes = Buffer.from(metadata.documents.data, 'base64');
  metadata.documents.data = Buffer.concat([bytes, Buffer.from([8])]).toString('base64');
  metadata.documents.offsets.push(bytes.length + 1);
  const worker = await workerFor(prepare(index));
  const result = await worker.search('husky paw');
  assert.deepEqual(Array.from(result.items, row => row[0]), ['balm']);
  assert.equal(result.inheritance[0].label, 'Dog');
  assert.equal((await worker.search('grandma choking')).items[0][0], 'choking',
    'real canonical animal evidence still fences universal human results');
});

test('inheritance intersects residual postings inside its subject scope without global token expansion', async () => {
  const resolved = [], source = fs.readFileSync(path.join(__dirname, '../assets/search-worker.js'), 'utf8')
    .replace('function resolveToken(token, widen = false, strict = false, plan = undefined) {',
      'function resolveToken(token, widen = false, strict = false, plan = undefined) { resolved.push(token);');
  const worker = await workerFor(prepare(hierarchyFixture()), source, { resolved });
  assert.equal((await worker.search('husky choking')).items[0][0], 'dog-choking');
  assert.equal(resolved.includes('choking'), false,
    'a broad residual must not materialize unrelated documents or alias groups');
});

test('six visible exact model answers retain strict ranking without ancestor candidate work', async () => {
  const index = hierarchyFixture(), scoped = [];
  for (let at = 1; at <= 5; at++) {
    index.items.push(['exact-' + at, 'Dell Inspiron 1500 Screen Repair ' + at,
      'technology', 'guide', 'Dell Inspiron 1500']);
    index.search.itemGroups.push(0);
  }
  const source = fs.readFileSync(path.join(__dirname, '../assets/search-worker.js'), 'utf8')
    .replace('function hierarchyCandidates(context, scope) {',
      'function hierarchyCandidates(context, scope) { scoped.push(context.subject);');
  const worker = await workerFor(prepare(index), source, { scoped });
  const result = await worker.search('Dell Inspiron 1500 screen');
  assert.equal(result.items.length, 6);
  assert.ok(result.items.every(row => row[4] === 'Dell Inspiron 1500'));
  assert.equal(scoped.length, 0);
  assert.equal(result.inheritance.length, 0);
});

test('whole known noun phrases and trailing component nouns do not invent hierarchy symptoms', async () => {
  const index = hierarchyFixture(), contexts = [];
  index.archetypes.archetypes.find(row => row.id === 'laptop').aliases.push('portable laptop');
  index.search.groups.push('portable laptop'); index.search.itemGroups[1] = 1;
  const source = fs.readFileSync(path.join(__dirname, '../assets/search-worker.js'), 'utf8')
    .replace('const context = hierarchyContext(query);',
      'const context = hierarchyContext(query); contexts.push(context?.subject || null);');
  const worker = await workerFor(prepare(index), source, { contexts });
  await worker.search('portable laptop');
  await worker.search('repairing laptop');
  await worker.search('missingprefix laptop');
  await worker.search('grandma choking');
  assert.deepEqual(contexts, [null, null, null, 'grandma']);
  assert.equal(worker.fetches(), 1);
});

test('component-primary guides use real canonical or known-device evidence for exact subject scope', async () => {
  for (const knownModel of [false, true]) {
    const index = hierarchyFixture(), scoped = [];
    index.search.groups.push('laptop');
    for (let at = 1; at <= 6; at++) {
      const device = knownModel ? 'Vendor Model ' + at : 'Laptop';
      index.items.push(['battery-' + at, device + ' Battery Replacement', 'technology', 'guide', device]);
      index.search.itemGroups.push(1);
      if (knownModel) index.archetypes.models.push({ name: device, nounId: 'laptop' });
    }
    // The new guide rows intentionally have no primary Laptop membership:
    // offline linking can select their component instead of their device.
    const source = fs.readFileSync(path.join(__dirname, '../assets/search-worker.js'), 'utf8')
      .replace('function hierarchyCandidates(context, scope) {',
        'function hierarchyCandidates(context, scope) { scoped.push(context.subject);');
    const worker = await workerFor(prepare(index), source, { scoped });
    const result = await worker.search('laptop battery');
    assert.equal(result.items.length, 6);
    assert.equal(scoped.length, 0, knownModel ? 'known device binding' : 'actual canonical subject');
    assert.equal(result.inheritance.length, 0);
  }
});

test('inherited symptoms and materials retain source-backed structured terms without fetching guides', async () => {
  const index = hierarchyFixture();
  index.items.push(['anaphylaxis', 'Anaphylaxis', 'health', 'article', '']);
  index.search.itemGroups.push(0);
  const prepared = prepare(index, { anaphylaxis: { id: 'anaphylaxis', version: 2,
    symptoms: ['Throat swelling', 'Hives'], materials: ['Epinephrine autoinjector'] } });
  const worker = await workerFor(prepared);
  for (const query of ['grandma throat swelling', 'grandmother hives', 'human epinephrine']) {
    const result = await worker.search(query);
    assert.deepEqual(Array.from(result.items, row => row[0]), ['anaphylaxis'], query);
    assert.equal(result.inheritance[0].label, 'person');
  }
  assert.equal((await worker.search('grandma not swelling')).total, 0, 'clinical negation remains required');
  assert.equal(worker.fetches(), 1, 'source-backed inheritance uses the prepared index only');
});

test('malformed sparse structured term evidence fails closed before worker readiness', async () => {
  for (const malformed of [[[0, ['not-in-dictionary']]], [[0, ['laptop', 'battery']]],
    [[0, ['battery']], [0, ['laptop']]], [[-1, ['laptop']]], [[99, ['laptop']]]]) {
    const index = prepare(fixture());
    index.search.inverted.structuredTerms = malformed;
    await assert.rejects(workerFor(index), /invalid/);
  }
});

test('numeric model aliases never turn an unknown code into a different model guide', async () => {
  const index = { version: 1, revision: 'numeric-alias-fixture', source: {},
    categories: [{ id: 'technology', name: 'Technology' }],
    items: [['wrong', 'Dell Inspiron 1526 Screen Replacement', 'technology', 'guide', 'Dell Inspiron 1526']],
    search: { version: 1, groups: ['', 'dell inspiron 1500'], itemGroups: [1] },
  };
  for (const catalog of [index, prepare(index)]) {
    const worker = await workerFor(catalog);
    assert.equal((await worker.search('Dell Inspiron 1500 screen replacement')).total, 0);
    assert.equal((await worker.search('Dell Inspiron 150 screen replacement')).total, 0, 'a prefix present only in an alias does not become an own model match');
    assert.equal((await worker.search('Dell Inspiron 152 screen replacement')).items[0][0], 'wrong', 'own model prefixes remain usable while typing');
    assert.equal((await worker.search('Dell Inspiron 1526 screen')).items[0][0], 'wrong');
  }
});

test('cached catalog schema failures reset partial worker state and recover once from the network', async () => {
  const good = prepare(fixture()), bad = structuredClone(good), calls = [], accepted = [], rejected = [];
  bad.search.inverted.offsets[1] = -1;
  let probes = 0;
  const helper = {
    async loadCatalog(options) {
      calls.push(options.forceNetwork);
      return { catalog: structuredClone(options.forceNetwork ? good : bad),
        etag: options.forceNetwork ? 'new' : 'bad', source: options.forceNetwork ? 'network' : 'cache',
        timings: { readMs: 2, parseMs: 3 } };
    },
    async acceptCatalog(entry) { accepted.push(entry.etag); return true; },
    async rejectCatalog(entry) { rejected.push(entry.etag); },
    async probeCatalog() { probes++; return null; },
  };
  const worker = await workerFor(good, undefined, { self: { mendClientCache: helper } });
  assert.deepEqual(calls, [false, true]);
  assert.deepEqual(rejected, ['bad']);
  assert.deepEqual(accepted, ['new'], 'only fully validated catalogs become active');
  assert.equal(worker.ready.cacheSource, 'network');
  assert.equal(worker.ready.catalogETag, 'new');
  assert.equal(worker.ready.cacheTimings.readMs, 2);
  assert.equal(worker.ready.cacheTimings.parseMs, 3);
  await worker.search('laptop battery'); await worker.search('fridge');
  assert.equal(probes, 1, 'the online revision probe runs once after startup, never per keystroke');
  assert.equal(worker.fetches(), 0, 'optional cache transport owns the catalog request');
});

test('candidate workers prepare in isolation and never accept or revalidate their own generation', async () => {
  const index = prepare(fixture()), accepted = [], rejected = [], loads = [];
  let probes = 0;
  const helper = {
    async loadCatalog(options) {
      loads.push(options);
      return { catalog: structuredClone(index), etag: 'v2', source: 'candidate', candidateKey: 'pinned-key', timings: {} };
    },
    async acceptCatalog(entry) { accepted.push(entry); return true; },
    async rejectCatalog(entry) { rejected.push(entry); },
    async probeCatalog() { probes++; return null; },
  };
  const worker = await workerFor(index, undefined, { URL, self: { mendClientCache: helper,
    location: { href: 'http://127.0.0.1:8080/assets/search-worker.js?candidateKey=pinned-key' } } });
  assert.equal(worker.ready.candidateKey, 'pinned-key');
  assert.equal(worker.ready.catalogETag, 'v2');
  assert.equal(loads[0].candidateKey, 'pinned-key');
  assert.equal((await worker.search('laptop battery')).total, 2);
  assert.deepEqual(accepted, []);
  assert.deepEqual(rejected, []);
  assert.equal(probes, 0, 'a replacement cannot create a catalog-update worker loop');
});

test('valid catalog readiness does not wait for slow device-cache persistence', async () => {
  const index = prepare(fixture());
  let finish, probes = 0;
  const pending = new Promise(resolve => { finish = resolve; });
  const helper = {
    async loadCatalog() { return { catalog: structuredClone(index), etag: 'v1', source: 'network', timings: {} }; },
    acceptCatalog() { return pending; },
    async probeCatalog() { probes++; return null; },
  };
  const worker = await workerFor(index, undefined, { self: { mendClientCache: helper } });
  assert.equal((await worker.search('laptop battery')).total, 2, 'worker searches while its durable copy is still being written');
  assert.equal(probes, 0);
  finish(true);
  for (let pass = 0; pass < 20 && probes === 0; pass++) await Promise.resolve();
  assert.equal(probes, 1, 'the one freshness probe starts after durable acceptance resolves');
});


test('he/she pronouns and contractions normalize identically without dropping clinical negation', async () => {
  const index = fixture();
  index.items = [
    ['symptom', 'Lethargic Dog', 'animals', 'guide', 'Dog'],
    ['negated', 'Dog Not Lethargic', 'animals', 'guide', 'Dog'],
  ];
  index.search = { version: 1, groups: [''], itemGroups: [0, 0] };
  for (const catalog of [index, prepare(index)]) {
    const worker = await workerFor(catalog);
    for (const [plain, marked] of [['dog hes lethargic', "dog he's lethargic"],
      ['dog shes lethargic', "dog she's lethargic"],
      ['dog he isnt lethargic', "dog he isn't lethargic"],
      ['dog she isnt lethargic', "dog she isn't lethargic"]]) {
      assert.deepEqual(Array.from((await worker.search(plain)).items, row => row[0]),
        Array.from((await worker.search(marked)).items, row => row[0]), plain);
    }
    for (const query of ['dog he isnt lethargic', 'dog she isnt lethargic']) {
      assert.deepEqual(Array.from((await worker.search(query)).items, row => row[0]), ['negated'], query);
    }
    for (const query of ['his dog lethargic', 'her dog lethargic', 'dog hes lethargic', 'dog shes lethargic']) {
      assert.equal((await worker.search(query)).total, 2, query);
    }
    assert.equal(worker.fetches(), 1);
  }
});


test('subject category evidence preserves normalized Unicode word/brand boundaries without per-row tokenization', () => {
  const context = vm.createContext({ self: {}, performance: { now: () => 0 },
    fetch: () => new Promise(() => {}), postMessage() {} });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/search-worker.js'), 'utf8'), context);
  for (const [alias, label, text] of [
    ['dog', 'canine', 'Underdog dogma'], ['dog', 'canine', "Dog's emergency care"],
    ['car', 'automobile', 'Discard cardboard repair'], ['car', 'automobile', 'Automobile battery'],
    ['road bike', 'bicycle', 'Road-bike chain'], ['road bike', 'bicycle', 'Road bikes chain'],
    ['resume', 'resume', 'Résumé formatting'], ['arm', 'arm', 'Firearm disassembly'],
    ['αβ', 'αβ', 'αβγ'], ['αβ', 'αβ', 'Part αβ replacement'],
    ['macbook air', 'laptop', 'MacBook Air'], ['macbook air', 'laptop', 'MacBook Airways'],
  ]) {
    Object.assign(context, { inputAlias: alias, inputLabel: label, inputText: text });
    const result = vm.runInContext(`(() => {
      const alias = normalizeSubject(inputAlias), label = normalizeSubject(inputLabel);
      const normalized = " " + normalizeSubject(inputText) + " ";
      return [subjectEvidencePattern({ alias, subject: { label } }).test(clean(inputText)),
        normalized.includes(" " + alias + " ") || normalized.includes(" " + label + " ")];
    })()`, context);
    assert.equal(result[0], result[1], text);
  }
});

test('scopedSubjectSearch confines candidate results strictly to the subject category and prefers repair guides', async () => {
  const worker = await workerFor(prepare(hierarchyFixture()));
  let id = 100;
  worker.send({ type: 'search', requestId: ++id, query: 'laptop screen', subject: 'laptop', specifics: 'screen', count: 6 });
  const scopedRes = await worker.next('results');
  assert.equal(scopedRes.requestId, id);
  assert.ok(scopedRes.items.every(item => item[0] !== 'dog-choking' && item[0] !== 'wrong-domain'));
  const ids = scopedRes.items.map(item => item[0]);
  assert.ok(ids.includes('model'));
  assert.ok(ids.includes('other-model'));
  assert.ok(ids.indexOf('model') < ids.indexOf('laptop'), 'guide should outrank article in scoped search');

  worker.send({ type: 'search', requestId: ++id, query: 'dog choking', subject: 'dog', specifics: 'choking', count: 6 });
  const dogRes = await worker.next('results');
  assert.equal(dogRes.requestId, id);
  assert.deepEqual(Array.from(dogRes.items, row => row[0]), ['dog-choking']);
});
