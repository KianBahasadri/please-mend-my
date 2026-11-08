# Noun and frequency sources

The build downloads Open English WordNet 2025 common nouns from its pinned
release archive. `scripts/nouns-source.json` records its URL, size, SHA-256,
license, domain policy, and the pinned wordfreq package version.

`OEWN-LICENSE.md` is the unmodified license from the upstream 2025-edition commit
`dc343f2683279ecbb13fab4e2fd778d7b162d287`, including the Princeton WordNet notice.
Open English WordNet is distributed under CC BY 4.0.

wordfreq 3.1.1 is the build-time frequency provider. Its software is Apache 2.0;
its data is CC BY-SA 4.0. `wordfreq-LICENSE.txt` and `wordfreq-NOTICE.md` preserve
upstream notices, including Robyn Speer, SUBTLEX authors, and constituent sources.
These notice copies were retrieved from upstream master; the installed package
version remains pinned independently. `sources.json` records their exact bytes
and hashes. Attribution is also retained on the site's unlinked `/credits` page.

The corpus and generated SQLite databases are build data outside Git. Noun
synonyms and frequency are enrichment metadata; lexical or frequency matches do
not prove a repair or clinical interpretation is correct. Coverage reports retain
unlinked compact rows and unfilled nouns for build-time inspection.
