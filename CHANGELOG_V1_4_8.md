# Changelog v1.4.8

## Added

- Unified local DRL knowledge database.
- Everything-style partial search across file paths and ingested knowledge.
- Engineer-facing `nova-drl` interactive prompt wrapper.
- Unified retrieval for model/equipment, serial text, DRL log, RMA, manufacturer PN, supplier order ref, repair history, and source files.
- Product-family and product-part knowledge tables for immediate parts-list retrieval.
- Strict RMA/procurement evidence grounding.
- DRL-specific procurement protection: DGK/MSR/NWK/DSK references do not become manufacturer PNs solely from the order notation.
- Atomic rebuild/refresh.
- Source freshness reporting.
- Built-in search latency self-check.

## Preserved

- v1.4.2 full DRL file index remains authoritative for file discovery.
- v1.4.6 frozen 10% benchmark remains unchanged.
- v1.4.7 enriched corpus remains unchanged.
- 80/20 rule remains fixed.
- No accepted facts are created.
- No Qdrant writes.
- No source-share writes.
