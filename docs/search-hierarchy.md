# Subject inheritance

Search ancestry is prepared from the noun database and shipped inside
`index.search.hierarchy`. No lookup runs against WordNet, Wikidata, an LLM, or an
external service while the user searches.

`prepare_search.py` exports compact noun rows, parent indices, aliases for
structural nouns, and delta-varint postings for primary guide memberships.
SQLite stores `is-a` edges as parent → child; the browser receives child → parent
lists. Existing catalog document positions and inverted postings stay intact.
Models retain their existing exact name → noun binding in the archetype registry.
Unfilled structural nouns can inherit without becoming filled UI specifiers.

The worker first retains exact model/subject matches. It then considers the
nearest compatible parent and more distant ancestors, requiring the remaining
part/symptom words and literal numeric tokens throughout. Ancestor traversal is
bounded and stays within the resolved lexical domain. General inherited answers
carry a scope label so a broad procedure is not presented as a model-specific one.
Unknown numeric model codes are not guessed or fuzzy-matched to another model.
Inherited part/symptom evidence must occur in the actual title/device or the
source-backed v2 symptoms/materials exported as sparse `inverted.structuredTerms`.
Generated noun aliases and category names cannot establish that evidence.
The inheritance seam reads a leading subject followed by a part or symptom.
A complete known noun/model stays on the ordinary search path; reordered title
words still use the existing title/posting ranking. Inherited candidates are
intersected inside a bounded, cached subject scope before any source-evidence
checks. Six exact visible answers need no wider ancestor search.

Common person senses are seeded through the same pinned WordNet pipeline.
Top-level WordNet roots are retained as structural nodes; a canonical root name
matching a lexical domain carries that domain, so the root `person` is not lost
merely because OEWN places it in `noun.Tops`.
Universal health articles can name a condition instead of a person. Their
fallback uses exported domain/category policy and primary noun memberships;
animal and artifact senses do not become human clinical matches. There is no
curated emergency pack or fixed subject subset.

The publisher rejects missing noun references, cycles, dangling guide links, and
incomplete hierarchy metadata before activating a release. Source bodies are
unchanged: a metadata repack hardlinks the existing guides/media and rebuilds
the noun registry and search index from compact rows.
