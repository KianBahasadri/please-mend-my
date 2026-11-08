# Diagram pipeline and experiment results

Reviewed art is permanent, hash-pinned JSON in `scripts/data/archetype-art.json`.
The bicycle retains its eleven reviewed regions and supplementary headlight;
the phone retains screen, buttons and speaker, with the incorrect camera region
excluded. Original sources, authors, licenses and adaptation notices remain
pinned. Available candidate drawings are now shown without visual approval or a
minimum part count; the earlier quality gates are diagnostic only. The browser
fetches local hashed art and leaves unmapped parts as list interactions beside
the context drawing. Credits appear only on the unlinked `/credits` page.

The old eight-subject runner, source-finder CLI, bespoke bicycle compiler,
JavaScript art fixture and one-off seed/approval configurations are retired.
Their reviewed outputs remain available without rerunning those prototypes.
The earlier pilot reviewed one phone drawing; arm, laptop, car, stove, toilet,
refrigerator and jeans proposals remained list-only. Original source evidence is
retained in [references/specifiers/](references/specifiers/) and
[references/bicycle/](references/bicycle/).

## Current build tools

- `diagram_batch.py`: metadata-only sense filtering, taxonomy, numbered-region
  mapping, primary/secondary passes, per-part visual verification and accounting.
- `diagram_ai.py`: strict-schema Qwen requests, bounded concurrency, complete
  response caches and resumable accounting. Taxonomy keeps its historical cache
  identity; set-of-mark mapping and verification use new high-detail, reasoning
  configurations, with image hashes included in their input identities.
- `diagram_discovery.py`: generic Commons/Openclipart search, provider-specific
  concurrency limits, licensed source metadata, immutable original byte pins.
- `diagram_svg.py`: shared XML security checks, SVG reference expansion, geometric
  digests, monochrome AST compilation and approved-art validation.
- `review_diagram_batch.py`: physical-only geometry gates and highlight sheets.

Python 3.12.12, Inkscape and librsvg's `rsvg-convert` are build tools only. No
browser is launched. Runtime search/interaction never invokes Qwen or an upstream
art provider. The source/response caches and diagnostic images stay outside Git.
Run the [bounded batch command](../scripts/README.md), or resume an individual
`prepare`, `taxonomy`, `discover`, `digest`, `map`, `compile`, or `verify` phase. `--offline`
forbids upstream requests. The same scripts work against a cloud-built catalog.

Remote Qwen concurrency is capped at 128. Discovery independently limits Commons
and Openclipart to four requests each and SVG downloads to eight; local geometry
uses at most two workers. The full job has an 8 GiB memory ceiling and an 8 GiB
free-RAM reserve. Failed source requests are distinct from successful empty
searches, so transient failures do not become permanent missing art.

## Candidate publication

Visual approval, confidence, overlap and minimum mapped-part coverage no longer
block a candidate drawing from appearing. `prepare_candidate_art.py` replays
the compact `scripts/data/candidate-sources.json` source/mapping pins, compiles
safe ASTs through `diagram_svg.py`, and packages available art by noun ID.
Missing or obsolete part mappings fall back to the source geometry as context;
the UI does not invent clickable regions. Reviewed bicycle and phone art take
priority without changing their geometry.
The main specifier chooses one available drawing per noun; the gallery retains
alternative candidates. The current local set contains 61 drawings, including
the two reviewed originals.

Generated SVGs and ASTs stay outside Git. A fresh EC2 build can retrieve missing
originals from the same source URLs and verify their SHA pins. XML/content,
license, byte-size and complexity checks remain required. The served release
retains credits and adaptation metadata. No runtime third-party request occurs.

## Diagnostic experiment gates

Parts distinguish physical assemblies from symptoms. Undefined view references
remain list-only; duplicate normalized IDs fail closed. Primary drawings need
three distinct physical mappings. A secondary view is attempted below 75%
coverage when another view has at least two missing parts, and must add two valid
mappings. These historical experiment thresholds remain useful diagnostics;
unreviewed candidates can now enter the served registry independently.

Safe-vector and license checks reject active content, external/raster geometry,
unsupported licenses and excessive complexity. Numeric gates reject duplicate
geometry, nearly identical or whole-object regions and insufficient bounds.
These checks are triage, not proof of correct part names. Every accepted drawing
can retain its review result without being suppressed. Exact source, applied
mapping, compiler and final artifact hashes remain recorded. A separate attempted
mapping hash and context-fallback flag preserve rejected mapping evidence.
Existing reviewed art is preserved independently.

The 10-item set-of-mark experiment filters contradictory catalog/WordNet senses
before taxonomy. It renders bounded numbered source regions individually,
excluding whole-object silhouettes and nearly invisible geometry. Mapping uses
those visible marks rather than asking the model to infer paths from coordinates;
the compiler resolves accepted candidate numbers to pinned tree paths.
Monochrome previews preserve the source viewBox aspect ratio.

A separate cached VLM pass checks each compiled highlighted part and whether the
drawing depicts the requested object. Failed or uncertain regions lose their
interactive mapping; at least three verified physical parts must remain and pass
geometry checks. The frozen pre-prune art and highlight sheet remain as evidence.
Automated visual passes remain diagnostic results, not a requirement for display.

To inspect rejected candidates locally, run `python3 scripts/prepare_diagram_preview.py`
and start the Go server with `DIAGRAM_PREVIEW_DIR` pointing to
`catalog-data/build/diagram-preview`. The unlinked `/diagram-preview/` gallery
shows local source PNGs, numbered regions and available compiled mappings from
both experiment batches, with source and license links. It exports no raw SVGs
and leaves the approved registry and selected catalog unchanged. The route is
absent when the environment variable is unset.

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

## Local 10-subject set-of-mark test

The metadata gate rejected three mismatched senses before selecting ten subjects.
Four of those ten received no eligible physical views from taxonomy. Discovery
retained 32 candidate placements across primary and secondary passes; no new art
was published. Both attempts use the same catalog revision and selected subjects.
The corrected pass excluded unlit paper-only regions, preserved the original
viewBox aspect ratio, and used high-detail images with reasoning enabled and an
8,192-token total output limit. The provider exposes no verified effort enum for
this model. The earlier attempt used a 16,384-token output limit.

Across both attempts, 33 calls produced 28 accepted responses. Reported cost was
**$0.109089**: $0.0928486 for accepted responses and $0.0162404 reported by two
rejected responses. Three earlier rejected calls have no retained charge data;
the total billed cost cannot be established from these receipts.

One secondary loudspeaker view passed the two-part geometry gate. Its independent
verification exhausted the output limit without a valid result, so no parts were
verified and none were retained. The part pass rate is undefined, rather than a
measured zero-percent accuracy rate. The corrected run took 177.2 seconds and
peaked at 141 MiB under the 8 GiB cap. The local gallery includes these rejected
candidates and the provisional mapping for inspection. Evidence and accounting
remain in `catalog-data/build/diagram-batch-10/`, including `initial-run/`.
