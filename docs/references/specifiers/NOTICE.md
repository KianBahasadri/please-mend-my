# Diagram pilot source originals

These SVG originals are retained without modification. Sources were found with
serial, cached Wikimedia Action API searches and Openclipart searches, then
inspected as rendered vectors. A candidate is not an approved runtime diagram.
The mapping/compiler gate records runtime changes and decides whether a diagram
is usable; uncertain archetypes retain their list-only fallback.

The 21 pins cover eight subjects and include manually reviewed whole-object
choices from those search results. They are not claimed to be an entirely
automatic Qwen selection. The separate Qwen-query discovery experiment accepted
16 structural candidates after inspecting 73 and rejecting 57; some surviving
results still depicted phone booths, people wearing jeans, or refrigerator cycle
illustrations. Those proposals remain in the ignored build directory and do not
replace these curated pins. Replaying their cached discovery produced the same
16 candidates; repeating it added zero candidates and inspected zero new ones.

Source bytes, SHA-256 pins, upload revisions when supplied, authors, source URLs,
and selected licenses are recorded in `sources.json`. Wikimedia original files
were matched to their API SHA-1 metadata before pinning. Openclipart detail pages
explicitly identify the CC0 dedication in their ImageObject license metadata.

Verify all pins without network access:

```sh
python3 scripts/find_diagram_sources.py verify
```

Retrieve missing originals from the pinned download URLs (changed bytes fail):

```sh
python3 scripts/find_diagram_sources.py fetch
```

Discover additional candidates with cached serial requests (review before use):

```sh
python3 scripts/find_diagram_sources.py discover --openclipart \
  --queries catalog-data/build/diagram-pilot/taxonomy.json \
  --output-dir catalog-data/build/diagram-sources/proposals \
  --report catalog-data/build/diagram-sources/discovery-report.json
```

The optional query file contains `archetypes: [{id, svgQueries: [...]}]`. Discovery
rejects raster wrappers, active content, external geometry/paint references,
unsupported licenses, oversized sources, insufficient vector shapes and obvious
subject/title noise. The discovery receipt records queries, inspected/accepted/
rejected candidates and rejection reasons; proposals stay outside approved pins. Shape
count is only a structural heuristic; visual review still rejects unrelated
wordmarks, charts and illustrations that cannot support useful part highlights.
Successful API responses and original downloads are cached. Requests are serial,
identify the project, request `maxlag=5`, and back off on transient transport or
MediaWiki JSON `maxlag`/`ratelimited` failures; error responses are never cached.
`verify` and `fetch` also accept `--output-dir` to check a staged manifest instead
of the pinned source directory.
Unused constant XML namespace entities are stripped with their DTD before parsing;
used, nested, external and parameter entities are rejected without resolution.

## Attribution and selected licenses

### arm

- **Human arm bones diagram.svg** by LadyofHats Mariana Ruiz Villarreal.
  Source: https://commons.wikimedia.org/wiki/File:Human_arm_bones_diagram.svg
  Selected license/status: [Public domain](https://creativecommons.org/publicdomain/mark/1.0/).
  Original: `arm-1daef4de0f375f47.original.svg`; no source edits.
- **Human arm bones diagram zh.svg** by File:Human arm bones diagram.svg : LadyofHats Mariana Ruiz Villarreal derivative work: GnolizX.
  Source: https://commons.wikimedia.org/wiki/File:Human_arm_bones_diagram_zh.svg
  Selected license/status: [Public domain](https://creativecommons.org/publicdomain/mark/1.0/).
  Original: `arm-20dfc2a297099671.original.svg`; no source edits.

### laptop

- **Black laptop.svg** by ilnanny.
  Source: https://commons.wikimedia.org/wiki/File:Black_laptop.svg
  Selected license/status: [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
  Original: `laptop-9decf7b0a5dc8df4.original.svg`; no source edits.
- **Laptop (NIH BioArt 734 - 791300).svg** by Courtesy of NIAID Ryan Kissinger.
  Source: https://commons.wikimedia.org/wiki/File:Laptop_(NIH_BioArt_734_-_791300).svg
  Selected license/status: [Public domain](https://creativecommons.org/publicdomain/mark/1.0/).
  Original: `laptop-5c40a3f1b97f94c2.original.svg`; no source edits.
- **Laptop 01.svg** by The original uploader was PhotoBox at English Wikipedia ..
  Source: https://commons.wikimedia.org/wiki/File:Laptop_01.svg
  Selected license/status: [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
  Original: `laptop-93d549cd2d921425.original.svg`; no source edits.
- **Black laptop computer open frontal.svg** by OpenClipart-Vectors.
  Source: https://commons.wikimedia.org/wiki/File:Black_laptop_computer_open_frontal.svg
  Selected license/status: [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
  Original: `laptop-e6adc16ea756d0f8.original.svg`; no source edits.

### phone

- **Smartphone 2 (layered)** by usr_share.
  Source: https://openclipart.org/detail/217392/smartphone-2-layered
  Selected license/status: [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
  Original: `phone-36fd55f4de6af960.original.svg`; no source edits.
- **Smartphone (layered)** by usr_share.
  Source: https://openclipart.org/detail/216525/smartphone-layered
  Selected license/status: [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
  Original: `phone-5a0ba04afb81de1d.original.svg`; no source edits.

### car

- **Sedan-car.svg** by sheikh_tuhin.
  Source: https://commons.wikimedia.org/wiki/File:Sedan-car.svg
  Selected license/status: [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
  Original: `car-4897b23682186c59.original.svg`; no source edits.
- **Skoda Superb Sedan Side View** by molumen.
  Source: https://openclipart.org/detail/328639/skoda-superb-sedan-side-view
  Selected license/status: [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
  Original: `car-6b73cd989a55409a.original.svg`; no source edits.
- **car side view** by Anonymous.
  Source: https://openclipart.org/detail/9792/car-side-view
  Selected license/status: [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
  Original: `car-983438823ee40241.original.svg`; no source edits.

### stove

- **Gas range.svg** by Paul Robinson.
  Source: https://commons.wikimedia.org/wiki/File:Gas_range.svg
  Selected license/status: [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/).
  Original: `stove-fe1bc5ffe0216165.original.svg`; no source edits.
- **Oven and range.svg** by Gary Gerbrandt.
  Source: https://commons.wikimedia.org/wiki/File:Oven_and_range.svg
  Selected license/status: [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
  Original: `stove-b5a68d50f0d018bf.original.svg`; no source edits.

### toilet

- **Gravity toilet valves at rest.svg** by Klork.
  Source: https://commons.wikimedia.org/wiki/File:Gravity_toilet_valves_at_rest.svg
  Selected license/status: [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/).
  Original: `toilet-58dfcb94b3056309.original.svg`; no source edits.
- **Gravity toilet valves handle down.svg** by Klork.
  Source: https://commons.wikimedia.org/wiki/File:Gravity_toilet_valves_handle_down.svg
  Selected license/status: [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/).
  Original: `toilet-0158e07e40fa515d.original.svg`; no source edits.
- **Cistern Flush Toilet diagram.svg** by Eawag: Swiss Federal Institute of Aquatic Science and Technology Technical drawings: designport, Paolo Monaco, Zurich See http://ecompendium.sswm.info/copyright.
  Source: https://commons.wikimedia.org/wiki/File:Cistern_Flush_Toilet_diagram.svg
  Selected license/status: [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/).
  Original: `toilet-7e7212185595f9f2.original.svg`; no source edits.
- **Pour-flush Toilet diagram.svg** by Eawag: Swiss Federal Institute of Aquatic Science and Technology Technical drawings: designport, Paolo Monaco, Zurich See http://ecompendium.sswm.info/copyright.
  Source: https://commons.wikimedia.org/wiki/File:Pour-flush_Toilet_diagram.svg
  Selected license/status: [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/).
  Original: `toilet-92f46e8aef6f40cf.original.svg`; no source edits.

### refrigerator

- **Refrigerator** by Korbac.
  Source: https://openclipart.org/detail/217656/refrigerator
  Selected license/status: [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
  Original: `refrigerator-65449619c4b3d3b4.original.svg`; no source edits.
- **Fridge** by Machovka.
  Source: https://openclipart.org/detail/2386/fridge
  Selected license/status: [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
  Original: `refrigerator-87a9f41eae523474.original.svg`; no source edits.

### jeans

- **Blue jeans** by AngyPangy.
  Source: https://openclipart.org/detail/263802/blue-jeans
  Selected license/status: [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
  Original: `jeans-63eb6f0efbd215ba.original.svg`; no source edits.
- **jeans-01** by yamachem.
  Source: https://openclipart.org/detail/202395/jeans01
  Selected license/status: [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
  Original: `jeans-8548f3ed7c9a29d6.original.svg`; no source edits.

CC BY-SA sources require the adapted artwork contributions to retain the
same or a compatible license; their inclusion does not change the license of
unrelated application code. Source authors do not endorse this project.
Runtime artwork, its attribution and adaptation notices are emitted from the
same pinned manifest; full visitor-facing credits live on the unlinked `/credits`
page. Do not remove source notices or replace source author names with the host.
