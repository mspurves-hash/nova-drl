# Validation — v1.4.8

Validated before packaging:

- Python compilation passed.
- Shell wrapper syntax passed.
- Synthetic unified-index build passed.
- Exact and partial product/model lookup passed (`BRD-1526990`, `1526990`).
- Serial-text lookup through indexed paths passed (`S07211`).
- RMA lookup passed (`53434`).
- Digi-Key order lookup passed (`DGK52102`).
- Mouser order lookup passed (`MSR 56889`).
- Partial manufacturer-PN lookup passed (`314`).
- Procurement contamination rule passed: `MSR 56889` was not indexed as manufacturer PN `56889`.
- Strict evidence rule passed: unsupported DGK association was rejected and the visibly supported Digi-Key reference was recovered from evidence.
- Product-part knowledge aggregation passed.
- 180,000-file synthetic scale check: index build completed in about 10.5 seconds on the packaging environment; representative identifier searches were sub-2 ms and a worst-case long filename substring query was under 100 ms. Actual DRL server timings may differ and should be measured with `--self-check`.
- No LLM calls are made by build/search.
- No NAS traversal is made by build/search; file metadata comes from the persistent v1.4.2 index.
