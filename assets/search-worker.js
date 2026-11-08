"use strict";

const clean = (value) => {
  const text = String(value || "");
  return /[^\x00-\x7f]/.test(text) ? text.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase() : text.toLowerCase();
};
const words = (value) => value.match(/[\p{L}\p{N}]+/gu) || [];
const conversational = new Set(["is", "are", "was", "were", "am", "seems", "feels", "has", "have", "does", "do", "did"]);
const pronouns = new Set(["it", "this", "that", "they", "i", "we"]);
const determiners = new Set(["my", "our", "your", "the", "a", "an"]);
const intensifiers = new Set(["fucking", "fuckin", "fucken", "damn", "damned", "goddamn", "goddamned", "frigging", "freaking"]);
const contractions = new Set(["it's", "that's", "this's", "there's", "i'm", "you're", "we're", "they're"]);
const symptomPredicates = new Set(["hurts", "hurt", "aches", "ache", "bleeds", "bleed", "burns", "burn", "itches", "itch",
  "tingles", "tingle", "swells", "swell", "leaks", "leak", "rattles", "rattle", "freezes", "freeze", "crashes", "crash"]);
const negativeContraction = /^(?:isn|aren|wasn|weren|don|doesn|didn|hasn|haven|can|won)'t$/;

function searchPhrase(query) {
  // These are conversational constructions, not general English stopwords.
  // Keep symptom words (including bloody), negation and literal model tokens.
  if (!/\b(?:it|this|that|there|they|i|we|you|my|our|your|the|a|an|is|are|was|were|am|seems|feels|has|have|does|do|did|fucking|fuckin|fucken|damn|damned|goddamn|goddamned|frigging|freaking|isn|aren|wasn|weren|don|doesn|didn|hasn|haven|can|won)['’\s,.!?]/.test(query + " ")) return query;
  const pieces = query.split(/\s+/);
  const keys = pieces.map(piece => piece.replace(/^[('"“‘]+|[)'"”’,;:.!?]+$/gu, "").replaceAll("’", "'"));
  const kept = [];
  let construction = true;
  for (let at = 0; at < pieces.length; at++) {
    const key = keys[at], next = keys[at + 1] || "";
    if (/\p{N}/u.test(key)) { kept.push(pieces[at]); construction = false; continue; }
    if (negativeContraction.test(key)) {
      kept.push("not"); construction = true;
    } else if (intensifiers.has(key) || contractions.has(key)) {
      construction = true;
    } else if (pronouns.has(key) && (conversational.has(next) || negativeContraction.test(next) ||
        kept.length && symptomPredicates.has(next))) {
      construction = true;
    } else if ((conversational.has(key) || construction && determiners.has(key)) && !/\p{N}/u.test(next)) {
      construction = true;
    } else {
      kept.push(/^[\p{L}\p{M}]+$/u.test(key) ? key : pieces[at]); construction = false;
    }
  }
  // Typing only a qualifier must not turn into an empty query/all catalog.
  return kept.length ? kept.join(" ") : query;
}
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
let broadBonuses;
let shortPrefixes;
// Exact source concept names point to explicit anchor devices. Whole aliases
// count here (bike -> Bicycle); a word inside "pedal cycle" is not a subject.
const primarySubjects = new Map();
// "Bike" is a published alias of both Bicycle and Motorcycle. A component
// request without explicit motor/dirt-bike intent uses the bicycle archetype.
const bicycleParts = new Set(["chain", "master", "link", "tire", "tyre", "tube", "brake", "cable", "gear", "derailleur",
  "wheel", "spoke", "handlebar", "stem", "seat", "seatpost", "pedal", "crank", "bottom", "bracket",
  "fork", "suspension", "light", "bell", "rust"].map(porter));
const motorIntent = new Set(["dirt", "motorcycle", "motorbike", "motor", "motorized", "moped"]);
const RESULT_LIMIT = 100;
const tokenCache = new Map();
let tokenCacheBytes = 0;
const queryCache = new Map();

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

const actionable = entry => entry.row[3] === "guide" || entry.row[3] === "article";

function firstActionable() {
  const documents = [];
  for (let doc = 0; doc < entries.length && documents.length < RESULT_LIMIT; doc++) {
    if (actionable(entries[doc])) documents.push(doc);
  }
  return documents;
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
  if (metadata.shortPrefixes !== undefined) {
    const prepared = metadata.shortPrefixes;
    if (!prepared || prepared.version !== 1 || prepared.limit !== 100 || !Array.isArray(prepared.queries) ||
        prepared.queries.length > metadata.terms.length) invalidIndex();
    shortPrefixes = new Map();
    for (const row of prepared.queries) {
      if (!Array.isArray(row) || row.length !== 2) invalidIndex();
      const [query, documents] = row;
      if (typeof query !== 'string' || query.length !== 1 || !/^\p{L}$/u.test(query) || clean(query) !== query ||
          shortPrefixes.has(query) || !Array.isArray(documents) || documents.length > prepared.limit ||
          new Set(documents).size !== documents.length || documents.some(doc => !Number.isInteger(doc) ||
            doc < 0 || doc >= entries.length || !actionable(entries[doc]))) invalidIndex();
      shortPrefixes.set(query, documents);
    }
  }
  if (metadata.guideLiterals !== undefined) {
    if (!Array.isArray(metadata.guideLiterals)) invalidIndex();
    let previous = -1;
    for (const literal of metadata.guideLiterals) {
      if (!Array.isArray(literal) || literal.length !== 2) invalidIndex();
      const [doc, text] = literal;
      if (!Number.isInteger(doc) || doc <= previous || doc < 0 || doc >= entries.length ||
          typeof text !== "string" || !text.trim() || clean(text) !== text || !/\p{N}/u.test(text)) invalidIndex();
      // Keep exact punctuation/model checks for generated materials and numeric
      // symptoms without duplicating every guide string in browser search rows.
      entries[doc].text += " " + text;
      previous = doc;
    }
  }
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
  // Store shared-prefix lengths during sizing. Allocate only live nodes: the
  // larger dictionary otherwise allocates and then copies several times its
  // final size, causing avoidable startup allocation/GC work.
  const prefixes = new Uint32Array(terms.length);
  let capacity = 1, previous = "";
  for (let at = 0; at < terms.length; at++) {
    const term = terms[at];
    if (!/^[a-z]+$/.test(term)) continue;
    const limit = previous.length < term.length ? previous.length : term.length;
    let shared = 0;
    while (shared < limit && previous.charCodeAt(shared) === term.charCodeAt(shared)) shared++;
    prefixes[at] = shared + 1;
    capacity += term.length - shared;
    if (term.length > maximumWordLength) maximumWordLength = term.length;
    previous = term;
  }
  const child = new Uint32Array(capacity), sibling = new Uint32Array(capacity);
  const character = new Uint8Array(capacity), terminal = new Uint32Array(capacity);
  const path = new Uint32Array(maximumWordLength + 1);
  let nodes = 1, previousLength = 0;
  for (let termIndex = 0; termIndex < terms.length; termIndex++) {
    if (!prefixes[termIndex]) continue;
    const term = terms[termIndex];
    const shared = prefixes[termIndex] - 1;
    for (let at = shared; at < term.length; at++) {
      const parent = path[at], node = nodes++;
      character[node] = term.charCodeAt(at);
      // With sorted words the previous path holds the parent's last child.
      if (at === shared && shared < previousLength) sibling[path[at + 1]] = node;
      else child[parent] = node;
      path[at + 1] = node;
    }
    terminal[path[term.length]] = termIndex + 1;
    previousLength = term.length;
  }
  trie = { child, sibling, character, terminal };
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

function tokenMatches(token, widen, strict) {
  const matches = new Map(), terms = inverted.terms;
  for (let at = lowerBound(terms, token); at < terms.length && terms[at].startsWith(token) && (!strict || terms[at] === token); at++) {
    matches.set(at, terms[at] === token ? 1 : 2);
  }
  if (!strict) {
    const root = porter(token), surfaceRoot = lowerBound(terms, root), stem = lowerBound(inverted.stemTerms, root);
    if (terms[surfaceRoot] === root && !matches.has(surfaceRoot)) matches.set(surfaceRoot, 3);
    if (inverted.stemTerms[stem] === root) {
      const { offsets, values } = inverted.stems;
      for (let at = offsets[stem]; at < offsets[stem + 1]; at++) {
        const term = values[at];
        if (!matches.has(term)) matches.set(term, 3);
      }
    }
  }
  const expanded = !matches.size || widen;
  if (expanded && !strict) fuzzyTerms(token, matches);
  return { matches, expanded };
}

function cachedToken(token, widen, strict) {
  return widen && !strict && tokenCache.get('=' + token)?.expanded ? tokenCache.get('=' + token)
    : tokenCache.get((strict ? '!' : widen ? '+' : '=') + token);
}

function postingHas(term, identifier) {
  const { offsets, values } = inverted.postings;
  let low = offsets[term], high = offsets[term + 1];
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (values[middle] < identifier) low = middle + 1;
    else high = middle;
  }
  return low < offsets[term + 1] && values[low] === identifier;
}

function tokenPlan(token, widen, strict) {
  const cached = cachedToken(token, widen, strict);
  if (cached) return { token, strict, cached, estimate: cached.documents.length };
  const plan = { token, strict, ...tokenMatches(token, widen, strict) };
  let estimate = 0;
  const { offsets, values } = inverted.postings;
  for (const [term] of plan.matches) {
    let low = offsets[term], high = offsets[term + 1];
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (values[middle] < entries.length) low = middle + 1;
      else high = middle;
    }
    estimate += low - offsets[term];
    for (let at = low; at < offsets[term + 1]; at++) {
      const group = values[at] - entries.length;
      estimate += groupOffsets[group + 1] - groupOffsets[group];
    }
  }
  plan.estimate = estimate;
  return plan;
}

function restrictedToken(plan, candidates) {
  // Probe only the current intersection when expanding a common word would
  // visit far more documents. Partial quality maps never enter the token cache.
  const quality = new Uint8Array(entries.length), documents = [];
  const primaryDevices = primarySubjects.get(plan.token) || primarySubjects.get(porter(plan.token));
  if (candidates.length > 128 && candidates.length * plan.matches.size * 12 >= plan.estimate) {
    // A long prefix can have hundreds of terms. Stream its postings through a
    // candidate mask instead of binary-searching every term for every document.
    const allowed = new Uint8Array(entries.length), groupQuality = new Uint8Array(searchGroups.length);
    for (const doc of candidates) allowed[doc] = 1;
    const { offsets, values } = inverted.postings;
    for (const [term, cost] of plan.matches) {
      for (let at = offsets[term]; at < offsets[term + 1]; at++) {
        const identifier = values[at];
        if (identifier < entries.length) {
          if (allowed[identifier] && (!quality[identifier] || cost < quality[identifier])) quality[identifier] = cost;
        } else {
          const group = identifier - entries.length;
          if (!groupQuality[group] || cost + 16 < groupQuality[group]) groupQuality[group] = cost + 16;
        }
      }
    }
    for (const doc of candidates) {
      const group = groupQuality[itemGroups[doc]];
      if (group && (!quality[doc] || group < quality[doc])) quality[doc] = group;
      if (quality[doc]) documents.push(doc);
    }
    return { documents: Uint32Array.from(documents), quality, expanded: plan.expanded, primaryDevices };
  }
  const terms = [...plan.matches].sort((a, b) => a[1] - b[1]);
  for (const doc of candidates) {
    let best = 0;
    for (const [term, cost] of terms) {
      if (best && cost >= best) break;
      const found = postingHas(term, doc) ? cost
        : itemGroups[doc] && postingHas(term, entries.length + itemGroups[doc]) ? cost + 16 : 0;
      if (found && (!best || found < best)) best = found;
    }
    if (best) { quality[doc] = best; documents.push(doc); }
  }
  return { documents: Uint32Array.from(documents), quality, expanded: plan.expanded, primaryDevices };
}

function resolveToken(token, widen = false, strict = false, plan = undefined) {
  const cacheKey = (strict ? "!" : widen ? "+" : "=") + token;
  const cached = cachedToken(token, widen, strict);
  if (cached) {
    if (tokenCache.has(cacheKey)) { tokenCache.delete(cacheKey); tokenCache.set(cacheKey, cached); }
    return cached;
  }
  const { matches, expanded } = plan || tokenMatches(token, widen, strict);
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
  const result = { documents: Uint32Array.from(documents).sort(), quality, expanded,
    primaryDevices: primarySubjects.get(token) || primarySubjects.get(porter(token)) };
  tokenCache.set(cacheKey, result);
  tokenCacheBytes += result.documents.byteLength + result.quality.byteLength;
  while (tokenCache.size > 32 || tokenCacheBytes > 8 * 1024 ** 2) {
    const key = tokenCache.keys().next().value, old = tokenCache.get(key);
    tokenCache.delete(key);
    tokenCacheBytes -= old.documents.byteLength + old.quality.byteLength;
  }
  return result;
}

function titleScore(entry, query, tokens, wordPrefixes, bonus) {
  let score = entry.title === query ? 1000 : entry.title.startsWith(query) ? 500 : entry.title.includes(query) ? 250 : 0;
  // A complete title such as "Broken arm" is closer to "arm broken" than a
  // detailed procedure containing those two words somewhere in a longer title.
  // Only tokenize short candidates; exact requested title order stays first.
  if (score < 950 && tokens.length > 1 && entry.title.length <= query.length + 2) {
    const titleWords = words(entry.title);
    if (titleWords.length === tokens.length && tokens.every(token => titleWords.includes(token))) score = 950;
  }
  score += bonus;
  for (let at = 0; at < tokens.length; at++) {
    const token = tokens[at];
    if (entry.title.startsWith(token) || entry.title.includes(wordPrefixes[at])) score += 35;
    else if (entry.title.includes(token)) score += 10;
  }
  return score;
}

function singleWordTitleScore(entry, query, wordPrefix, bonus) {
  const position = entry.title.indexOf(query);
  if (position < 0) return bonus;
  if (position === 0) return bonus + (entry.title.length === query.length ? 1000 : 500) + 35;
  return bonus + 250 + (entry.title.includes(wordPrefix) ? 35 : 10);
}

function shortlist() {
  // Keep only the best results while visiting candidates. The heap root is the
  // worst retained score, with later catalog positions losing ties. Sorting
  // this bounded heap preserves the former full ranking's first 100 results.
  const documents = new Uint32Array(RESULT_LIMIT), scores = new Float64Array(RESULT_LIMIT);
  let size = 0;
  const worse = (score, doc, otherScore, otherDoc) => score < otherScore || score === otherScore && doc > otherDoc;
  return {
    // Candidates arrive in catalog order, so an equal score cannot beat an
    // earlier retained document. This also permits safe score-bound pruning.
    canBeat: score => size < RESULT_LIMIT || score > scores[0],
    add(doc, score) {
      let at;
      if (size < RESULT_LIMIT) {
        at = size++;
        while (at) {
          const parent = (at - 1) >>> 1;
          if (!worse(score, doc, scores[parent], documents[parent])) break;
          scores[at] = scores[parent];
          documents[at] = documents[parent];
          at = parent;
        }
      } else {
        if (worse(score, doc, scores[0], documents[0]) || score === scores[0] && doc === documents[0]) return;
        at = 0;
        while (at * 2 + 1 < size) {
          let child = at * 2 + 1;
          if (child + 1 < size && worse(scores[child + 1], documents[child + 1], scores[child], documents[child])) child++;
          if (!worse(scores[child], documents[child], score, doc)) break;
          scores[at] = scores[child];
          documents[at] = documents[child];
          at = child;
        }
      }
      scores[at] = score;
      documents[at] = doc;
    },
    sorted() {
      return Array.from({ length: size }, (_, index) => index)
        .sort((a, b) => scores[b] - scores[a] || documents[a] - documents[b])
        .map(index => documents[index]);
    },
  };
}

function broadSearch(query, list) {
  // A bare subject has one posting list: avoid the multi-term intersection and
  // numeric-identifier loops while applying its source-aware ranking.
  const best = shortlist();
  const wordPrefix = " " + query;
  for (const doc of list.documents) {
    const entry = entries[doc];
    if (!actionable(entry)) continue;
    const quality = list.quality[doc], penalty = (quality - 1) * 100;
    const bonus = broadBonuses[doc];
    const upper = (quality <= 2 ? (entry.title.length === query.length ? 1035 : 535) : 0) + bonus - penalty;
    if (!best.canBeat(upper)) continue;
    const score = (quality <= 2 ? singleWordTitleScore(entry, query, wordPrefix, bonus) : bonus) - penalty;
    best.add(doc, score);
  }
  return best.sorted();
}

function indexedSearch(query, tokens) {
  if (!tokens.length) return query ? [] : firstActionable();
  if (RESULT_LIMIT === 100 && tokens.length === 1 && tokens[0] === query && shortPrefixes?.has(query)) {
    return shortPrefixes.get(query);
  }
  const wordPrefixes = tokens.map(token => " " + token);
  const singleWord = tokens.length === 1 && tokens[0] === query;
  // A one-letter prefix is not yet a subject; keep its cheap original ranking.
  const broad = singleWord && query.length > 1 && !/\p{N}/u.test(query);
  const bicycleContext = tokens.some(token => token === "bike" || token === "bikes") &&
    !tokens.some(token => motorIntent.has(token)) && tokens.some(token => bicycleParts.has(porter(token)))
    ? primarySubjects.get("bicycle") : null;
  // Literal punctuation in model identifiers remains significant after word
  // tokenization, e.g. S23+ must not match S23 and WH-1000XM4 stays one model.
  const identifiers = query.split(/\s+/).filter(token => /\p{N}/u.test(token))
    .map(token => token.replace(/^[('"“‘]+|[)'"”’,;:.!?]+$/gu, ""));
  const intersect = (widen) => {
    const lists = [];
    const plans = tokens.map(token => tokenPlan(token, widen, tokens.length > 1 && (token === "not" || token === "no")))
      .sort((a, b) => a.estimate - b.estimate);
    let candidates;
    for (const plan of plans) {
      const useRestricted = candidates && !plan.cached &&
        (candidates.length <= 256 || plan.estimate > candidates.length * 2);
      const list = useRestricted ? restrictedToken(plan, candidates)
        : resolveToken(plan.token, widen, plan.strict, plan);
      if (!list.documents.length) return [];
      lists.push(list);
      if (!candidates || useRestricted || list.documents.length < candidates.length) candidates = list.documents;
    }
    lists.sort((a, b) => a.documents.length - b.documents.length);
    if (broad) return broadSearch(query, lists[0]);
    const best = shortlist();
    for (const doc of lists[0].documents) {
      const entry = entries[doc];
      if (!actionable(entry)) continue;
      let penalty = 0, matches = true, literal = false, primary = false;
      for (const list of lists) {
        if (!list.quality[doc]) { matches = false; break; }
        let quality = list.quality[doc];
        if (list.primaryDevices?.has(entry.device)) {
          // An ambiguous source alias must not boost both competing concepts
          // equally after the part selector has established bicycle context.
          if (!bicycleContext || bicycleContext.has(entry.device)) primary = true;
          // Explicit concept equivalence should cost the same as an own word.
          // Other group aliases (e.g. "pedal" from "pedal cycle") retain their
          // full penalty, so real component postings win over alias collisions.
          if (quality >= 17) quality = 1;
        }
        penalty += quality - 1;
        if (quality <= 2) literal = true;
      }
      if (!matches) continue;
      for (const identifier of identifiers) {
        if (!entry.text.includes(identifier) && !searchGroups[itemGroups[doc]].includes(identifier)) { matches = false; break; }
      }
      if (!matches) continue;
      const bonus = entry.row[3] === "guide" ? 15 : 0;
      const upper = primary ? Infinity : literal ? singleWord ? (entry.title.length === query.length ? 1035 : 535) + bonus : Infinity : bonus;
      if (!best.canBeat(upper - penalty * 100)) continue;
      // A candidate found solely through aliases/stem changes/typos has no
      // literal title-word bonus. Avoid rescanning thousands of unrelated
      // titles for broad shared aliases such as "cellphone".
      let score = literal ? singleWord ? singleWordTitleScore(entry, query, wordPrefixes[0], bonus)
        : titleScore(entry, query, tokens, wordPrefixes, bonus) : bonus;
      // Prefer a named subject's own device over incidental title words such as
      // "Dirt Bike". Keep this below exact/complete title scores so an explicit
      // request for a particular procedure still wins.
      if (primary && tokens.length > 1 && score < 900) {
        score = Math.min(900, score + (bicycleContext?.has(entry.device) ? 650 : 450));
      }
      best.add(doc, score - penalty * 100);
    }
    return best.sorted();
  };
  const literal = intersect(false);
  // A typo can exist as a rare literal elsewhere in the vocabulary. Retry only
  // an empty intersection so it cannot suppress otherwise valid results.
  return literal.length ? literal : intersect(true);
}

function legacySearch(query, literalQuery = "") {
  if (!query) return firstActionable();
  const tokens = query.split(/\s+/).filter(Boolean), best = shortlist();
  const wordPrefixes = tokens.map(token => " " + token);
  const negations = tokens.map(token => token === "not" ? /\bnot\b/ : token === "no" ? /\bno\b/ : null);
  const broad = tokens.length === 1 && query.length > 1 && /^\p{L}+$/u.test(query);
  // Transitional compatibility while an old catalog is still cached. Published
  // builds have prepared postings; their searches never take this scan path.
  const compact = tokens.length <= 31;
  const groupMasks = new Uint32Array(searchGroups.length);
  const wideMatches = compact ? null : tokens.map(() => new Uint8Array(searchGroups.length));
  for (let group = 1; group < searchGroups.length; group++) {
    for (let term = 0; term < tokens.length; term++) {
      if (negations[term] ? negations[term].test(searchGroups[group]) : searchGroups[group].includes(tokens[term])) {
        if (compact) groupMasks[group] |= 1 << term;
        else wideMatches[term][group] = 1;
      }
    }
  }
  for (let index = 0; index < entries.length; index++) {
    const entry = entries[index], group = itemGroups[index], aliasMask = groupMasks[group];
    if (!actionable(entry)) continue;
    if (literalQuery && entry.title === literalQuery) {
      best.add(index, 3000 + (entry.row[3] === "guide" ? 15 : 0));
      continue;
    }
    let matches = true;
    for (let term = 0; term < tokens.length; term++) {
      if ((compact ? aliasMask & (1 << term) : wideMatches[term][group]) ||
          (negations[term] ? negations[term].test(entry.text) : entry.text.includes(tokens[term]))) continue;
      matches = false;
      break;
    }
    if (!matches) continue;
    const bonus = broad ? broadBonuses[index] : entry.row[3] === "guide" ? 15 : 0;
    const score = titleScore(entry, query, tokens, wordPrefixes, bonus);
    best.add(index, score);
  }
  return best.sorted();
}

function literalTitles(query) {
  // Keep an exact original title above conversational interpretations. Recover
  // it from its smallest direct posting, without a title map/catalog scan.
  const { terms, postings } = inverted;
  let first = 0, last = Infinity;
  for (const token of words(query)) {
    const term = lowerBound(terms, token);
    if (terms[term] !== token) return [];
    const start = postings.offsets[term], end = postings.offsets[term + 1];
    if (end - start < last - first) { first = start; last = end; }
  }
  const matches = [];
  if (!Number.isFinite(last)) return matches;
  for (let at = first; at < last; at++) {
    const doc = postings.values[at];
    if (doc < entries.length && actionable(entries[doc]) && entries[doc].title === query) matches.push(doc);
  }
  return matches.sort((a, b) => Number(entries[b].row[3] === "guide") - Number(entries[a].row[3] === "guide") || a - b);
}

function search(request) {
  const started = performance.now(), literal = clean(request.query).trim(), query = searchPhrase(literal);
  const exact = query === literal ? "" : literal;
  if (queryCache.has(literal)) {
    ranked = queryCache.get(literal);
    queryCache.delete(literal);
    queryCache.set(literal, ranked);
  } else {
    ranked = inverted ? indexedSearch(query, [...new Set(words(query))]) : legacySearch(query, exact);
    if (inverted && exact) {
      const titles = literalTitles(literal);
      if (titles.length) {
        const seen = new Set(titles);
        ranked = [...titles, ...ranked.filter(doc => !seen.has(doc))].slice(0, RESULT_LIMIT);
      }
    }
    // Immutable catalog: revisiting an input (Backspace, guide return, repeated
    // searches) can reuse its ranked IDs. Bound both key size and entry count.
    if (literal.length <= 512) {
      queryCache.set(literal, ranked);
      if (queryCache.size > 96) queryCache.delete(queryCache.keys().next().value);
    }
  }
  activeRequest = request.requestId;
  postMessage({ type: "results", requestId: activeRequest, total: ranked.length,
    items: ranked.slice(0, Math.min(60, request.count || 30)).map((index) => catalog.items[index]), duration: performance.now() - started });
}

self.onmessage = ({ data }) => {
  if (data.type === "search") schedule(data);
  else if (data.type === "range") sendRange(data);
};

async function loadCatalog() {
  const delays = [250, 500, 1000, 2000, 4000];
  for (let attempt = 0; ; attempt += 1) {
    let response, failure;
    try {
      response = await fetch("/api/catalog", { credentials: "same-origin", mode: "same-origin",
        ...(attempt ? { cache: "no-store" } : {}) });
    } catch (error) {
      failure = error;
    }
    if (response) {
      // JSON/schema failures are permanent. Retry only the request itself,
      // preserving normal ETag caching and making no requests after startup.
      if (response.ok) return response.json();
      failure = new Error(`The repair library is unavailable (${response.status}).`);
      try { await response.body?.cancel(); } catch {}
      if (![429, 500, 502, 503, 504].includes(response.status)) throw failure;
    }
    if (attempt === delays.length) throw failure;
    await new Promise(resolve => setTimeout(resolve, delays[attempt]));
  }
}

(async () => {
  try {
    const started = performance.now();
    catalog = await loadCatalog();
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
    // Whole source aliases identify their explicit device names. Topic pages
    // remain in the catalog for direct links, but never enter result rankings.
    for (const source of catalog.search?.sources || []) {
      if (!Array.isArray(source.ifixitPaths)) continue;
      const names = [source.label, ...(source.aliases || []), ...(source.curated || [])]
        .filter(name => typeof name === "string").map(clean);
      for (const path of source.ifixitPaths) {
        const title = clean(path.slice(path.lastIndexOf("/") + 1).replaceAll("_", " "));
        for (const name of names) {
          if (!primarySubjects.has(name)) primarySubjects.set(name, new Set());
          primarySubjects.get(name).add(title);
        }
      }
    }
    broadBonuses = new Uint8Array(catalog.items.length);
    entries = catalog.items.map((row, doc) => {
      const title = clean(row[1]);
      broadBonuses[doc] = Math.max(0, 60 - title.length) + (row[3] === "article" ? 18 : 0);
      if (!devices.has(row[4])) devices.set(row[4], clean(row[4]));
      const device = devices.get(row[4]);
      return { row, title, device, text: `${title} ${device} ${categories.get(row[2]) || ""}` };
    });
    if (catalog.search?.inverted) {
      prepareInverted(catalog.search.inverted);
      // Release JSON offsets/base64 strings after decoding; only compact typed
      // arrays, the word dictionary and shared alias strings stay resident.
      delete catalog.search.inverted;
    }
    // A JSON string crosses the worker boundary without deep-cloning thousands
    // of nested part arrays on the main thread. Parse it once in the page.
    const archetypesJSON = catalog.archetypes ? JSON.stringify(catalog.archetypes) : "";
    delete catalog.archetypes;
    ready = true;
    postMessage({ type: "ready", total: entries.length, categories: catalog.categories, source: catalog.source,
      revision: catalog.revision, aliasGroups: searchGroups.length - 1, invertedIndex: Boolean(inverted),
      termCount: inverted?.terms.length || 0, stemming: Boolean(inverted), fuzzy: Boolean(inverted),
      terms: inverted?.terms || [],
      archetypesJSON,
      resultLimit: RESULT_LIMIT,
      loadDuration: performance.now() - started });
    if (pending) schedule(pending);
  } catch (error) {
    postMessage({ type: "error", message: error.message || "The repair library could not be loaded." });
  }
})();
