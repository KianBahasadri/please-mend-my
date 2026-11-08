# Diagram pipeline and experiment results

Approved art is permanent, hash-pinned JSON in `scripts/data/archetype-art.json`.
The bicycle retains its eleven reviewed regions and supplementary headlight;
the phone retains screen, buttons and speaker, with the incorrect camera region
excluded. Original sources, authors, licenses and adaptation notices remain
pinned. The browser fetches only local hashed art and keeps unsupported parts
list-only. Credits appear only on the unlinked `/credits` page.

The old eight-subject runner, source-finder CLI, bespoke bicycle compiler,
JavaScript art fixture and one-off seed/approval configurations are retired.
Their reviewed outputs remain available without rerunning those prototypes.
The earlier pilot reviewed one phone drawing; arm, laptop, car, stove, toilet,
refrigerator and jeans proposals remained list-only. Original source evidence is
retained in [references/specifiers/](references/specifiers/) and
[references/bicycle/](references/bicycle/).

## Current build tools

- `diagram_batch.py`: metadata-only archetype selection, taxonomy, primary and
  coverage-gated secondary passes, compilation receipts and lifetime accounting.
- `diagram_ai.py`: strict-schema Qwen requests, bounded concurrency, complete
  response caches and resumable accounting. Its historical cache-format identity
  stays unchanged so previously paid responses replay without new calls.
- `diagram_discovery.py`: generic Commons/Openclipart search, provider-specific
  concurrency limits, licensed source metadata, immutable original byte pins.
- `diagram_svg.py`: shared XML security checks, SVG reference expansion, geometric
  digests, monochrome AST compilation and approved-art validation.
- `review_diagram_batch.py`: physical-only geometry gates and highlight sheets.

Python 3.12.12, Inkscape and librsvg's `rsvg-convert` are build tools only. No
browser is launched. Runtime search/interaction never invokes Qwen or an upstream
art provider. The source/response caches and diagnostic images stay outside Git.
Run the [bounded batch command](../scripts/README.md), or resume an individual
`prepare`, `taxonomy`, `discover`, `digest`, `map`, or `compile` phase. `--offline`
forbids upstream requests. The same scripts work against a cloud-built catalog.

Remote Qwen concurrency is capped at 128. Discovery independently limits Commons
and Openclipart to four requests each and SVG downloads to eight; local geometry
uses at most two workers. The full job has an 8 GiB memory ceiling and an 8 GiB
free-RAM reserve. Failed source requests are distinct from successful empty
searches, so transient failures do not become permanent missing art.

## Quality and publication

Parts distinguish physical assemblies from symptoms. Undefined view references
remain list-only; duplicate normalized IDs fail closed. Primary drawings need
three distinct physical mappings. A secondary view is attempted below 75%
coverage when another view has at least two missing parts, and must add two valid
mappings. At most three views are proposed. The multi-view browser interface is
still planned; unreviewed proposals never modify the served registry.

Safe-vector and license checks reject active content, external/raster geometry,
unsupported licenses and excessive complexity. Numeric gates reject duplicate
geometry, nearly identical or whole-object regions and insufficient bounds.
These checks are triage, not proof of correct part names. Every accepted drawing
still needs visual inspection, with exact source, mapping, compiler and final
artifact hashes retained. Existing reviewed art is preserved independently.

## Local 300-archetype run

The batch processed 300 catalog-backed physical noun candidates, making 300
cached taxonomy calls and 118 image-assisted mapping calls. Peak remote
concurrency was 128; reported accepted-response cost was **$0.31151606**. First
pass taxonomy took 32.5 seconds. The largest measured batch-stage memory peak was
241 MiB under the 8 GiB cap; more than 20 GiB remained available locally.

Discovery retained 319 candidate placements across two passes, representing 231
unique pinned SVGs. Eighteen compiled views across seventeen archetypes passed
the numeric gates. Visual review **rejected seventeen views and held one blender
view for partial jar/lid geometry**. No new diagram was approved or published.
Examples included a disk spindle labelled as a connector, a shoe tongue labelled
as a heel, and refrigerant-flow lines labelled as a door seal. This experiment
shows that the current mapping stage is not reliable enough for unattended
publication. The known-good bicycle and phone remain unchanged.

Many selected noun senses also had ambiguous model/title links, so they were
kept list-only instead of receiving unrelated drawings. The count is 300
processed candidates, not 300 usable diagrams. Selection/link quality and visual
mapping accuracy require improvement before scaling publication.

Reproducible inputs, per-stage receipts, original SVGs, complete AI responses,
highlight sheets and reports are under `catalog-data/build/diagram-batch-300/`.
`report.json` records compilation candidates; `visual-review.json` records the
stricter review outcome. Measurements are in
`catalog-data/benchmarks/diagram-batch-300/`. Cache replay incurs no new API calls;
reported cost excludes any provider charges not returned for failed requests.
