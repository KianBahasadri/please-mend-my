The bicycle art reuses two published SVGs. Their original bytes are retained here;
the browser receives only the offline-prepared geometry in `assets/bicycle-art.js`.

“Bicycle diagram” by Al2 and Wikimedia Commons contributors:

- Source: https://commons.wikimedia.org/wiki/File:Bicycle_diagram.svg
- Original: `commons-bicycle-diagram.original.svg`
- Selected license: Creative Commons Attribution 3.0 Unported (CC BY 3.0),
  https://creativecommons.org/licenses/by/3.0/
- The upstream file also offers GFDL; this adaptation uses the CC BY 3.0 option.
- Changes: removed labels, translation text, callouts, markers, blurred ground
  shadows, and repeated tyre-tread ornaments; cropped the viewBox; recolored the
  remaining geometry for light/dark mode; increased thin outlines; expanded local
  SVG references and grouped original shapes into interactive assemblies.

“Bicycle” by WRomey, Openclipart:

- Source: https://openclipart.org/detail/3891/bicycle
- Original: `openclipart-bicycle.original.svg`
- License: CC0 1.0 Universal / public-domain dedication,
  https://creativecommons.org/publicdomain/zero/1.0/
- License confirmation: https://openclipart.org/share
- Changes: extracted the original headlight path `path10148`, reflected, scaled,
  positioned, and recolored it as the Commons bicycle's supplemental light.

Source authors retain credit for their artwork. The grouping, interaction,
monochrome adaptation, and light placement are modifications by this project;
the authors do not endorse the site. Source URLs, SHA-256 pins, and preparation
details are recorded in `sources.json`. Regenerate deterministically, offline:

```sh
python3 scripts/prepare_bicycle_art.py
python3 scripts/prepare_bicycle_art.py --check
```
