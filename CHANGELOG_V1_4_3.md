# Changelog v1.4.3

- Made Nova DRL File Index v1.4.2 the primary RCL1A discovery layer.
- Added default Everything-style `RCL1A LINE` indexed query.
- Removed the need for a recursive NAS walk in the production RCL1A source-discovery path.
- Added production Line Card selector with explicit exclusion reasons.
- Added native support for individual indexed Line Card PDFs.
- Added index/share-root binding verification and stale-entry checks.
- Added `source_selection_v1_4_3.json` to freeze the exact selected source corpus before model analysis.
- Preserved full source paths, index metadata, hashes, and detected DRL logs.
- Preserved same-log grouping as one repair event without collapsing legitimate evidence files.
- Kept v1.4.1 focused evidence/extraction/normalization principles, accepted facts 0, prior hosted benchmark input off, and Qdrant off.
