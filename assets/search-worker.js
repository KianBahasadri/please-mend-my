"use strict";

const clean = (value) => {
  const text = String(value || "");
  return /[^\x00-\x7f]/.test(text) ? text.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase() : text.toLowerCase();
};
const words = (value) => value.match(/[\p{L}\p{N}]+/gu) || [];
const step2 = Object.entries({ ational: "ate", tional: "tion", enci: "ence", anci: "ance", izer: "ize",
  abli: "able", alli: "al", entli: "ent", eli: "e", ousli: "ous", ization: "ize", ation: "ate",
  ator: "ate", alism: "al", iveness: "ive", fulness: "ful", ousness: "ous", aliti: "al", iviti: "ive", biliti: "ble" })
  .sort((a, b) => b[0].length - a[0].length);
const step3 = Object.entries({ icate: "ic", ative: "", alize: "al", iciti: "ic", ical: "ic", ful: "", ness: "" })
  .sort((a, b) => b[0].length - a[0].length);
const step4 = ["al", "ance", "ence", "er", "ic", "able", "ible", "ant", "ement", "ment", "ent", "ion",
  "ou", "ism", "ate", "iti", "ous", "ive", "ize"].sort((a, b) => b.length - a.length);

// Original Porter (1980), kept in agreement with scripts/prepare_search.py.
// https://tartarus.org/martin/PorterStemmer/def.txt
// Identifiers containing numbers and non-ASCII words are never stemmed.
function porter(word) {
  if (word.length <= 2 || !/^[a-z]+$/.test(word)) return word;
  const vowels = (part) => {
    const flags = [];
    for (const char of part) flags.push(/[aeiou]/.test(char) || char === "y" && flags.length > 0 && !flags[flags.length - 1]);
    return flags;
  };
  const measure = (part) => {
    const flags = vowels(part);
    let count = 0;
    for (let at = 1; at < flags.length; at++) if (flags[at - 1] && !flags[at]) count++;
    return count;
  };
  const containsVowel = (part) => vowels(part).some(Boolean);
  const cvc = (part) => {
    const flags = vowels(part), size = flags.length;
    return size >= 3 && !flags[size - 3] && flags[size - 2] && !flags[size - 1] && !/[wxy]$/.test(part);
  };
  const replace = (part, rules) => {
    for (const [suffix, replacement] of rules) {
      if (part.endsWith(suffix)) {
        const stem = part.slice(0, -suffix.length);
        return measure(stem) > 0 ? stem + replacement : part;
      }
    }
    return part;
  };
  if (word.endsWith("sses") || word.endsWith("ies")) word = word.slice(0, -2);
  else if (word.endsWith("s") && !word.endsWith("ss")) word = word.slice(0, -1);
  if (word.endsWith("eed")) {
    if (measure(word.slice(0, -3)) > 0) word = word.slice(0, -1);
  } else {
    for (const suffix of ["ed", "ing"]) {
      if (word.endsWith(suffix) && containsVowel(word.slice(0, -suffix.length))) {
        word = word.slice(0, -suffix.length);
        if (/(at|bl|iz)$/.test(word)) word += "e";
        else if (word.length >= 2 && word.at(-1) === word.at(-2) && !vowels(word).at(-1) && !/[lsz]$/.test(word)) word = word.slice(0, -1);
        else if (measure(word) === 1 && cvc(word)) word += "e";
        break;
      }
    }
  }
  if (word.endsWith("y") && containsVowel(word.slice(0, -1))) word = word.slice(0, -1) + "i";
  word = replace(replace(word, step2), step3);
  for (const suffix of step4) {
    if (word.endsWith(suffix)) {
      const stem = word.slice(0, -suffix.length);
      if (measure(stem) > 1 && (suffix !== "ion" || /[st]$/.test(stem))) word = stem;
      break;
    }
  }
  if (word.endsWith("e")) {
    const stem = word.slice(0, -1), count = measure(stem);
    if (count > 1 || count === 1 && !cvc(stem)) word = stem;
  }
  if (word.endsWith("ll") && measure(word) > 1) word = word.slice(0, -1);
  return word;
}

let catalog;
let ready = false;
let entries = [];
let pending;
let scheduled = false;
let ranked = [];
let activeRequest = 0;
let searchGroups = [""];
let itemGroups;
let inverted;
let groupOffsets;
let groupDocuments;
let trie;
let maximumWordLength = 0;
const tokenCache = new Map();
let tokenCacheBytes = 0;

function schedule(request) {
  pending = request;
  if (!ready || scheduled) return;
  scheduled = true;
  setTimeout(() => {
    scheduled = false;
    const latest = pending;
    pending = null;
    if (latest) search(latest);
  }, 0);
}

function sendRange(request) {
  if (request.requestId !== activeRequest) return;
  const start = Math.max(0, Math.min(ranked.length, request.start || 0));
  const end = Math.min(ranked.length, start + Math.min(60, request.count || 30));
  postMessage({ type: "range", requestId: activeRequest, rangeId: request.rangeId,
    start, items: ranked.slice(start, end).map((index) => catalog.items[index]) });
}

function lowerBound(list, word) {
  let low = 0, high = list.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (list[middle] < word) low = middle + 1;
    else high = middle;
  }
  return low;
}

function invalidIndex() {
  throw new Error("The prepared search inverted index is invalid.");
}

function validateTerms(terms) {
  if (!Array.isArray(terms)) invalidIndex();
  for (let at = 0; at < terms.length; at++) {
    const term = terms[at];
    if (typeof term !== "string" || !/^[\p{L}\p{N}]+$/u.test(term) || clean(term) !== term ||
        at > 0 && terms[at - 1] >= term) invalidIndex();
  }
}

function unpackLists(encoded, byteOffsets, size, maximum, forbidden = -1) {
  if (typeof encoded !== "string" || encoded.length % 4 !== 0 ||
      !/^[A-Za-z0-9+/]*={0,2}$/.test(encoded) || !Array.isArray(byteOffsets) ||
      byteOffsets.length !== size + 1 || byteOffsets[0] !== 0) invalidIndex();
  let binary;
  try { binary = atob(encoded); } catch { invalidIndex(); }
  if (byteOffsets[size] !== binary.length) invalidIndex();
  // A byte encodes at most one integer. Decode once; query-time work is only
  // typed-array posting reads, unions and intersections, never JSON parsing.
  const output = new Uint32Array(binary.length), offsets = new Uint32Array(size + 1);
  let written = 0;
  for (let list = 0; list < size; list++) {
    const start = byteOffsets[list], end = byteOffsets[list + 1];
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > binary.length) invalidIndex();
    offsets[list] = written;
    let previous = 0, at = start;
    while (at < end) {
      let delta = 0, shift = 0, byte;
      do {
        if (at >= end || shift > 28) invalidIndex();
        byte = binary.charCodeAt(at++);
        if (shift === 28 && (byte & 127) > 15) invalidIndex();
        delta |= (byte & 127) << shift;
        shift += 7;
      } while (byte & 128);
      const value = previous + (delta >>> 0);
      if (value > maximum || value === forbidden || written > offsets[list] && value <= previous) invalidIndex();
      output[written++] = value;
      previous = value;
    }
  }
  offsets[size] = written;
  return { offsets, values: output.slice(0, written) };
}

function prepareInverted(metadata) {
  if (!metadata || metadata.version !== 1 || metadata.normalization !== "NFKD-no-marks-lower" ||
      metadata.tokenizer !== "unicode-LN" || metadata.stemmer !== "porter1980" ||
      metadata.documents !== catalog.items.length || metadata.groups !== searchGroups.length) invalidIndex();
  validateTerms(metadata.terms);
  validateTerms(metadata.stemTerms);
  const postings = unpackLists(metadata.postings, metadata.offsets, metadata.terms.length,
    catalog.items.length + searchGroups.length - 1, catalog.items.length);
  // Empty group zero has no postings; accepting it could associate aliases with
  // unrelated documents and is therefore an invalid catalog, not a search miss.
  const stems = unpackLists(metadata.stemPostings, metadata.stemOffsets, metadata.stemTerms.length, metadata.terms.length - 1);
  inverted = { terms: metadata.terms, stemTerms: metadata.stemTerms, postings, stems };
  groupOffsets = new Uint32Array(searchGroups.length + 1);
  for (const group of itemGroups) groupOffsets[group + 1]++;
  for (let group = 1; group < groupOffsets.length; group++) groupOffsets[group] += groupOffsets[group - 1];
  groupDocuments = new Uint32Array(catalog.items.length);
  const positions = groupOffsets.slice();
  for (let doc = 0; doc < itemGroups.length; doc++) groupDocuments[positions[itemGroups[doc]]++] = doc;
  buildTrie(metadata.terms);
}

function buildTrie(terms) {
  // Sorted ASCII surface words form a compact trie in typed arrays. Numbers are
  // intentionally absent: a near model number must never turn into another one.
  let capacity = 1;
  for (const term of terms) if (/^[a-z]+$/.test(term)) capacity += term.length;
  const child = new Uint32Array(capacity), sibling = new Uint32Array(capacity);
  const character = new Uint8Array(capacity), terminal = new Uint32Array(capacity), lastChild = new Uint32Array(capacity);
  let nodes = 1, previous = "", path = [0];
  for (let termIndex = 0; termIndex < terms.length; termIndex++) {
    const term = terms[termIndex];
    if (!/^[a-z]+$/.test(term)) continue;
    maximumWordLength = Math.max(maximumWordLength, term.length);
    let shared = 0;
    while (shared < Math.min(previous.length, term.length) && previous[shared] === term[shared]) shared++;
    path.length = shared + 1;
    for (let at = shared; at < term.length; at++) {
      const parent = path[at], node = nodes++;
      character[node] = term.charCodeAt(at);
      if (lastChild[parent]) sibling[lastChild[parent]] = node;
      else child[parent] = node;
      lastChild[parent] = node;
      path.push(node);
    }
    terminal[path[term.length]] = termIndex + 1;
    previous = term;
  }
  trie = { child: child.slice(0, nodes), sibling: sibling.slice(0, nodes),
    character: character.slice(0, nodes), terminal: terminal.slice(0, nodes) };
}

function fuzzyTerms(token, matches) {
  const distance = token.length >= 8 ? 2 : 1;
  if (token.length < 5 || token.length > maximumWordLength + distance || !/^[a-z]+$/.test(token)) return;
  const width = token.length;
  const first = new Uint32Array(width + 1), letters = Uint8Array.from(token, char => char.charCodeAt(0));
  for (let at = 0; at <= width; at++) first[at] = at;
  // Depth-first traversal reuses one DP row per depth. A tuple/row per node
  // creates thousands of short-lived allocations on the first typo query.
  const rows = [first], path = [];
  let node = trie.child[0], depth = 1;
  while (node) {
    path[depth] = node;
    const before = rows[depth - 1];
    let current = rows[depth];
    if (!current) {
      current = new Uint32Array(width + 1);
      current.fill(distance + 1);
      rows[depth] = current;
    }
    current[0] = depth;
    let minimum = depth;
    const start = depth > distance ? depth - distance : 1;
    const end = width < depth + distance ? width : depth + distance;
    if (start > 1) current[start - 1] = distance + 1;
    if (end < width) current[end + 1] = distance + 1;
    for (let at = start; at <= end; at++) {
      const substitution = before[at - 1] + (letters[at - 1] === trie.character[node] ? 0 : 1);
      let value = current[at - 1] < before[at] ? current[at - 1] + 1 : before[at] + 1;
      if (substitution < value) value = substitution;
      current[at] = value;
      if (value < minimum) minimum = value;
    }
    if (minimum <= distance) {
      if (trie.terminal[node] && current[width] <= distance) {
        const term = trie.terminal[node] - 1, quality = 8 + current[width];
        if (!matches.has(term) || quality < matches.get(term)) matches.set(term, quality);
      }
      if (trie.child[node] && depth < width + distance) {
        node = trie.child[node];
        depth++;
        continue;
      }
    }
    while (depth && !trie.sibling[path[depth]]) depth--;
    node = depth ? trie.sibling[path[depth]] : 0;
  }
}

function resolveToken(token, widen = false) {
  const cacheKey = (widen ? "+" : "=") + token;
  const narrow = widen ? tokenCache.get("=" + token) : null;
  if (narrow?.expanded) return narrow;
  if (tokenCache.has(cacheKey)) {
    const result = tokenCache.get(cacheKey);
    tokenCache.delete(cacheKey);
    tokenCache.set(cacheKey, result);
    return result;
  }
  const matches = new Map(), terms = inverted.terms;
  for (let at = lowerBound(terms, token); at < terms.length && terms[at].startsWith(token); at++) {
    matches.set(at, terms[at] === token ? 1 : 2);
  }
  const root = porter(token), surfaceRoot = lowerBound(terms, root), stem = lowerBound(inverted.stemTerms, root);
  if (terms[surfaceRoot] === root && !matches.has(surfaceRoot)) matches.set(surfaceRoot, 3);
  if (inverted.stemTerms[stem] === root) {
    const { offsets, values } = inverted.stems;
    for (let at = offsets[stem]; at < offsets[stem + 1]; at++) {
      const term = values[at];
      if (!matches.has(term)) matches.set(term, 3);
    }
  }
  const expanded = !matches.size || widen;
  if (expanded) fuzzyTerms(token, matches);
  const quality = new Uint8Array(matches.size ? entries.length : 0), groupQuality = new Uint8Array(matches.size ? searchGroups.length : 0);
  const documents = [], groups = [];
  const addDocument = (doc, cost) => {
    if (!quality[doc]) { quality[doc] = cost; documents.push(doc); }
    else if (cost < quality[doc]) quality[doc] = cost;
  };
  const { offsets, values } = inverted.postings;
  for (const [term, cost] of matches) {
    for (let at = offsets[term]; at < offsets[term + 1]; at++) {
      const identifier = values[at];
      if (identifier < entries.length) addDocument(identifier, cost);
      else {
        const group = identifier - entries.length;
        if (!groupQuality[group]) { groupQuality[group] = cost + 16; groups.push(group); }
        else groupQuality[group] = Math.min(groupQuality[group], cost + 16);
      }
    }
  }
  for (const group of groups) {
    const cost = groupQuality[group];
    for (let at = groupOffsets[group]; at < groupOffsets[group + 1]; at++) addDocument(groupDocuments[at], cost);
  }
  const result = { documents: Uint32Array.from(documents).sort(), quality, expanded };
  tokenCache.set(cacheKey, result);
  tokenCacheBytes += result.documents.byteLength + result.quality.byteLength;
  while (tokenCache.size > 32 || tokenCacheBytes > 8 * 1024 ** 2) {
    const key = tokenCache.keys().next().value, old = tokenCache.get(key);
    tokenCache.delete(key);
    tokenCacheBytes -= old.documents.byteLength + old.quality.byteLength;
  }
  return result;
}

function titleScore(entry, query, tokens) {
  let score = entry.title === query ? 1000 : entry.title.startsWith(query) ? 500 : entry.title.includes(query) ? 250 : 0;
  if (entry.row[3] === "guide") score += 15;
  for (const token of tokens) {
    if (entry.title.startsWith(token) || entry.title.includes(` ${token}`)) score += 35;
    else if (entry.title.includes(token)) score += 10;
  }
  return score;
}

function indexedSearch(query, tokens) {
  if (!tokens.length) return query ? [] : entries.map((_, index) => index);
  // Literal punctuation in model identifiers remains significant after word
  // tokenization, e.g. S23+ must not match S23 and WH-1000XM4 stays one model.
  const identifiers = query.split(/\s+/).filter(token => /\p{N}/u.test(token))
    .map(token => token.replace(/^[('"“‘]+|[)'"”’,;:.!?]+$/gu, ""));
  const intersect = (widen) => {
    const lists = [];
    for (const token of tokens) {
      const list = resolveToken(token, widen);
      if (!list.documents.length) return [];
      lists.push(list);
    }
    lists.sort((a, b) => a.documents.length - b.documents.length);
    const buckets = new Map();
    for (const doc of lists[0].documents) {
      let penalty = 0, matches = true, literal = false;
      for (const list of lists) {
        if (!list.quality[doc]) { matches = false; break; }
        penalty += list.quality[doc] - 1;
        if (list.quality[doc] <= 2) literal = true;
      }
      if (!matches) continue;
      const entry = entries[doc];
      for (const identifier of identifiers) {
        if (!entry.text.includes(identifier) && !searchGroups[itemGroups[doc]].includes(identifier)) { matches = false; break; }
      }
      if (!matches) continue;
      // A candidate found solely through aliases/stem changes/typos has no
      // literal title-word bonus. Avoid rescanning thousands of unrelated
      // titles for broad shared aliases such as "cellphone".
      const score = (literal ? titleScore(entry, query, tokens) : entry.row[3] === "guide" ? 15 : 0) - penalty * 100;
      if (!buckets.has(score)) buckets.set(score, []);
      buckets.get(score).push(doc);
    }
    const result = [];
    for (const score of [...buckets.keys()].sort((a, b) => b - a)) {
      for (const doc of buckets.get(score)) result.push(doc);
    }
    return result;
  };
  const literal = intersect(false);
  // A typo can exist as a rare literal elsewhere in the vocabulary. Retry only
  // an empty intersection so it cannot suppress otherwise valid results.
  return literal.length ? literal : intersect(true);
}

function legacySearch(query) {
  const tokens = query.split(/\s+/).filter(Boolean), buckets = new Map();
  // Transitional compatibility while an old catalog is still cached. Published
  // builds have prepared postings; their searches never take this scan path.
  const compact = tokens.length <= 31;
  const groupMasks = new Uint32Array(searchGroups.length);
  const wideMatches = compact ? null : tokens.map(() => new Uint8Array(searchGroups.length));
  for (let group = 1; group < searchGroups.length; group++) {
    for (let term = 0; term < tokens.length; term++) {
      if (searchGroups[group].includes(tokens[term])) {
        if (compact) groupMasks[group] |= 1 << term;
        else wideMatches[term][group] = 1;
      }
    }
  }
  for (let index = 0; index < entries.length; index++) {
    const entry = entries[index], group = itemGroups[index], aliasMask = groupMasks[group];
    let matches = true;
    for (let term = 0; term < tokens.length; term++) {
      if ((compact ? aliasMask & (1 << term) : wideMatches[term][group]) || entry.text.includes(tokens[term])) continue;
      matches = false;
      break;
    }
    if (!matches) continue;
    const score = titleScore(entry, query, tokens);
    if (!buckets.has(score)) buckets.set(score, []);
    buckets.get(score).push(index);
  }
  const result = [];
  for (const score of [...buckets.keys()].sort((a, b) => b - a)) {
    for (const index of buckets.get(score)) result.push(index);
  }
  return result;
}

function search(request) {
  const started = performance.now(), query = clean(request.query).trim();
  ranked = inverted ? indexedSearch(query, [...new Set(words(query))]) : legacySearch(query);
  activeRequest = request.requestId;
  postMessage({ type: "results", requestId: activeRequest, total: ranked.length,
    items: ranked.slice(0, Math.min(60, request.count || 30)).map((index) => catalog.items[index]), duration: performance.now() - started });
}

self.onmessage = ({ data }) => {
  if (data.type === "search") schedule(data);
  else if (data.type === "range") sendRange(data);
};

(async () => {
  try {
    const started = performance.now();
    const response = await fetch("/api/catalog", { credentials: "same-origin", mode: "same-origin" });
    if (!response.ok) throw new Error(`The repair library is unavailable (${response.status}).`);
    catalog = await response.json();
    if (!Array.isArray(catalog.items) || !Array.isArray(catalog.categories)) throw new Error("The repair library format is invalid.");
    const categories = new Map(catalog.categories.map((category) => [category.id, clean(category.name)]));
    const devices = new Map();
    itemGroups = new Uint32Array(catalog.items.length);
    if (catalog.search) {
      const metadata = catalog.search;
      if (metadata.version !== 1 || !Array.isArray(metadata.groups) || metadata.groups[0] !== "" ||
          !Array.isArray(metadata.itemGroups) || metadata.itemGroups.length !== catalog.items.length ||
          !metadata.groups.every((group) => typeof group === "string") ||
          !metadata.itemGroups.every((group) => Number.isInteger(group) && group >= 0 && group < metadata.groups.length)) {
        throw new Error("The prepared search alias format is invalid.");
      }
      searchGroups = metadata.groups;
      itemGroups = Uint32Array.from(metadata.itemGroups);
      delete metadata.itemGroups;
    }
    entries = catalog.items.map((row) => {
      const title = clean(row[1]);
      if (!devices.has(row[4])) devices.set(row[4], clean(row[4]));
      return { row, title, text: `${title} ${devices.get(row[4])} ${categories.get(row[2]) || ""}` };
    });
    if (catalog.search?.inverted) {
      prepareInverted(catalog.search.inverted);
      // Release JSON offsets/base64 strings after decoding; only compact typed
      // arrays, the word dictionary and shared alias strings stay resident.
      delete catalog.search.inverted;
    }
    ready = true;
    postMessage({ type: "ready", total: entries.length, categories: catalog.categories, source: catalog.source,
      revision: catalog.revision, aliasGroups: searchGroups.length - 1, invertedIndex: Boolean(inverted),
      termCount: inverted?.terms.length || 0, stemming: Boolean(inverted), fuzzy: Boolean(inverted),
      loadDuration: performance.now() - started });
    if (pending) schedule(pending);
  } catch (error) {
    postMessage({ type: "error", message: error.message || "The repair library could not be loaded." });
  }
})();
