The specifier registry lives in `assets/specifiers.js`, loaded before the hero
controller. `resolve(subject)` maps local aliases to one definition; `nouns`
exposes the supported subject vocabulary. `mount(container, subject, onSelect)`
returns `sync(answer)` and `destroy()`. The registry previews and selects parts;
only the hero writes the native answer. Unknown subjects hide the container.
New diagrams use prepared geometry descriptors alongside their parts, canonical
queries, and aliases. The renderer builds inline SVG from local data; published
JSON artifacts need no catalog rebuild.

The [eight-subject pilot](diagram-pilot.md) adds a generated resident registry and
lazy local JSON artwork. Its phone diagram maps screen, buttons, and speaker;
the other seven experimental subjects retain lists. Source and final-byte review
pins gate publication. The original synchronous bicycle path remains available.

The bicycle uses actual geometry from Al2 and contributors' [Bicycle diagram](https://commons.wikimedia.org/wiki/File:Bicycle_diagram.svg),
choosing its [CC BY 3.0 license](https://creativecommons.org/licenses/by/3.0/),
plus the headlight from WRomey's [Openclipart bicycle](https://openclipart.org/detail/3891/bicycle)
under [CC0](https://creativecommons.org/publicdomain/zero/1.0/). Full source credits
and adaptation notices live on the unlinked `/credits` page and remain in the
SVG's JSON metadata. No credit row or credits link appears beside the diagram.
The original files, source URLs, hashes, and license records are pinned in
[`docs/references/bicycle/`](references/bicycle/).

`scripts/prepare_bicycle_art.py` prepares safe monochrome geometry descriptors
offline from those pinned originals. Its generated `assets/bicycle-art.js` loads
before the registry and exposes `window.mendBicycleArt`. The registry builds
interactive groups from that local data; no drawing is fetched or parsed from
an external service during use. Regeneration and consistency checking run with:

```sh
python3 scripts/prepare_bicycle_art.py
python3 scripts/prepare_bicycle_art.py --check
```

`window.mendHero` exposes `query()`, `focus()`, `isAwaitingSpecifics()`,
`settleDelay()`, `setVocabulary(terms)`, and immediate `settle()`. Its timer
controls answer focus and non-specifier clarification independently of live
search. A bare subject recognized by `mendSpecifiers.resolve` mounts its question
and diagram/list on the input event, preserving first-field focus until the
idle deadline or an explicit action. This applies to every registry alias,
including list-only specifiers. A first-field value that is no longer a registered
bare subject closes this workspace immediately. The worker's startup
`ready.terms` supplies the sorted resident dictionary, checked with
binary search. A vocabulary arriving during a pause recalculates its deadline
from the last edit, without restarting the beat.
Composition, hidden documents, and open guides cancel pending settlement.

`mend:querychange` searches the combined editable text immediately, coalescing
edits within one animation frame and rejecting stale worker replies. There is
no search debounce or dictionary-dependent wait. Empty queries and unanswered
clarifications hide cards immediately; committed answers search immediately.
IME preedit waits for commitment. `mend:settle` handles clarification transitions
and explicit shortcuts; live results never wait for that event. Subjects without
a specifier can show results during the clarification pause. Registered subjects
hide cards immediately when their blank clarification opens.

Part selection focuses the answer and tries a native `insertText` edit to retain
undo history where supported, falling back to `setRangeText` if the edit was not
applied. Native input events may fire synchronously or be absent, so the hero
updates its state in either case and coalesces duplicate query notifications.
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

The shared-settle rollout passed 97 JavaScript checks and `go test ./...`.
Its local receipts live in `catalog-data/benchmarks/specifiers/`.

The sourced drawing update passes 100 JavaScript checks, four Python source
integrity/preparation checks, and `go test ./...`. Every retained shape's geometry
matches its pinned original; hit regions retain the same transforms and use
14 CSS-pixel non-scaling transparent strokes. The generated art is 105,141 bytes
raw / 18,115 bytes with Python gzip; all changed public assets add 18,633 gzip
bytes compared with the former drawing. The Go server's art response is 17,877
gzip bytes. Originals and preparation metadata are outside the public embed.

In the synthetic Node DOM fixture, bicycle construction p95 changed from
0.54 ms / 48 nodes to 5.13 ms / 409 nodes. This runs when mounting the bicycle,
not during ordinary typing; these measurements exclude real DOM layout/paint.
Warm loopback full-body requests for the registry changed from 0.31 ms to
0.20 ms at p95, and the art response measured 0.16 ms. These small samples are
local checks, not evidence of a causal serving speedup or browser latency.
The catalog and search worker were unchanged. Receipts live in
`catalog-data/benchmarks/sourced-bicycle/`.

Tests exercise the shipped hero, registry, search UI, and worker using
deterministic DOM/event fixtures. All eleven sourced part highlights were
inspected in light/dark static previews; actual browser layout, font paint,
and the browser's undo stack were not exercised.
