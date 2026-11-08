The specifier registry lives in `assets/specifiers.js`, loaded before the hero
controller. `resolve(subject)` maps local aliases to one definition; `nouns`
exposes the supported subject vocabulary. `mount(container, subject, onSelect)`
returns `sync(answer)` and `destroy()`. The registry previews and selects parts;
only the hero writes the native answer. Unknown subjects hide the container.
New diagrams belong in the registry as inline SVG, alongside their parts,
canonical queries, and aliases. No Go asset-type changes or catalog rebuild is
needed.

`window.mendHero` exposes `query()`, `focus()`, `isAwaitingSpecifics()`,
`settleDelay()`, `setVocabulary(terms)`, and immediate `settle()`. Its one timer
handles both inputs. The worker's startup `ready.terms` supplies the sorted
resident dictionary, checked with binary search. A vocabulary arriving during a
pause recalculates its deadline from the last edit, without restarting the beat.
Composition, hidden documents, and open guides cancel pending settlement.

`mend:querychange` invalidates pending frames and old worker responses. It hides
cards immediately for empty queries or an unanswered clarification. `mend:settle`
fires after the hero's clarification transition; search then uses the combined
editable text. A bare subject therefore never briefly reveals cards before
asking for specifics. Explicit actions and guide return bypass the timer.

Part selection focuses the answer and tries a native `insertText` edit to retain
undo history where supported, falling back to `setRangeText` if the edit was not
applied. Native input events may fire synchronously or be absent, so the hero
updates its state in either case and cancels any pending delay before searching.
The optional legacy command is used only from part callbacks, never recursively
from an input handler. Browser differences are described in
[Mozilla's editing API documentation](https://developer.mozilla.org/en-US/docs/Web/API/Document/execCommand).

The worker excludes non-guide/article rows at each candidate path, while keeping
every published row and posting position unchanged. Topic JSON files and their
direct links stay available. Whole source concept aliases identify primary
devices; words inside broad aliases still carry their alias penalty. Bare
`bike` plus a component resolves the Bicycle/Motorcycle ambiguity toward Bicycle;
explicit dirt/motorcycle intent and exact titles retain priority.

Local measurements used the unchanged 207,691-item `ac9690ea…` release. The Node
worker benchmark measures computation, excluding browser/network/paint. Targeted
probes include a synthetic structured clone of the startup dictionary.

| Measurement | Before | After |
| --- | ---: | ---: |
| Worker preparation | 537.0 ms | 530.4 ms |
| Worker prepared heap | 108.66 MiB | 110.30 MiB |
| Warm mixed-query p95 | 3.21 ms | 3.32 ms |
| Warm repair-query p95 | 3.63 ms | 3.53 ms |
| Catalog HTTP requests per worker | 1 | 1 |

The main-thread dictionary has 190,709 terms (2,178,668 bytes as JSON), with a
12.6 ms one-time structured clone in the targeted Node probe. The probe's combined
heap, including that copy, grew from 108.67 to 116.79 MiB. No dictionary lookup
requires another worker message or HTTP request. Targeted bicycle-component
queries had warm p95 at or below 0.51 ms; these samples do not establish browser
input-to-paint latency or a causal startup speedup.

The real catalog now ranks the loose-bicycle-pedal procedure first for `bike
pedal`, and bicycle tire procedures ahead of dirt-bike tire procedures for `bike
tire`. All 18 targeted queries returned zero topic cards. S23/S24 guide/article
ordering stayed identical after removing four topic rows from each result set.
The Bicycle topic endpoint still returns its original topic JSON.

All 97 JavaScript checks and `go test ./...` pass for this change. Tests exercise
the shipped hero, registry, search UI, and worker using deterministic DOM/event
fixtures. SVG artwork was inspected in light/dark static previews; actual browser
layout, font paint, and the browser's undo stack were not exercised. Local
receipts live in `catalog-data/benchmarks/specifiers/`.
