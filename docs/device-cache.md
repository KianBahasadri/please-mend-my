# On-device catalog cache

The browser cache covers the complete published search catalog uniformly.
Guide storage is bounded by recency rather than by a handpicked collection.
Searches continue using the resident worker index; cache and freshness requests
are outside the per-keystroke path.

CacheStorage preserves HTTP responses but requires application-managed freshness
and eviction. Its contents can be removed by browser storage policy; storage
errors must fall back to the network without blocking the page.
[MDN Cache reference](https://developer.mozilla.org/en-US/docs/Web/API/Cache).

The namespaces are `pmm-catalog-v1` for the catalog,
`pmm-guides-v1` for up to 500 recent/prefetched guides, and versioned
`pmm-shell-v1-<hash>` shell data managed by the service worker. Content-addressed art
continues using its immutable HTTP cache identity.

A saved catalog can start the worker without waiting for a network handshake.
An online session checks its ETag with a conditional HEAD request. An unchanged
response needs no replacement body; a changed release is downloaded into a
candidate entry. A replacement worker validates and prepares it while the active
worker continues searching. Reconnecting an offline tab triggers a coalesced
freshness check too. Only a ready candidate can replace the active
catalog, invalidate guide caches, and become the persisted default. Failed or
malformed candidates preserve the last good catalog.

Guide responses are fenced by catalog generation, including late in-flight
requests. Native Web Locks serialize publication and guide eviction across tabs
where available; other environments retain local serialization and version
checks. A candidate carries its base ETag so a delayed tab cannot knowingly
replace a newer accepted catalog. Updating a catalog invalidates persisted guide entries and
the page's in-memory detail cache. Offline use retains full local search and
previously cached guide content; a guide never fetched is not promised offline.

The shell manifest is derived from the embedded site assets. Service-worker
installation prepares a complete shell version before activation, and offline
navigation uses that version's entry page and assets. Activation does not reload
the user's tab or replace native input state.

Reading from disk still requires JSON parsing and index preparation. The existing
73 MB catalog takes roughly three seconds to prepare in the local Node benchmark;
CacheStorage alone cannot establish a ≤20 ms warm-start guarantee. Cache read,
parse, preparation, network time, and query computation must be measured
separately. A background freshness check permits the cached release to be used
until validation completes; offline or failed checks cannot prove freshness.
