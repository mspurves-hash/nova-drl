# Nova DRL v1.5.10 — Global Resolver Consolidation

- Fixes product suffix grouping globally by repair-event coverage rather than product-specific formats.
- `PRE-200`, `PRE-200-B`, `PRE-200B-CE`, and `PRE-200-CE` now resolve through the same generic rule used for every product.
- Prevents pure numeric base extensions without a delimiter from being merged automatically.
- Removes the prior production component-specific normalization and replaces it with generic PN shape/digit/alpha/recurrence clustering.
- Reference PN selection remains corpus-only and keeps complete recurring alphanumeric identities when supported, while allowing truly dominant numeric cores.
- Adds global-regression tests across unrelated product families and multiple suffix styles.
- Hard 80/20 policy remains unchanged; expert/user knowledge is not injected into counts or mappings.
- No re-ingestion, AI call, NAS scan, knowledge-index rebuild, or Windows-client reinstall required.
