# Changelog — v1.4.7

- Preserves frozen v1.4.6 benchmark corpus unchanged.
- Adds first-class RMA extraction and lookup.
- Adds first-class procurement/distributor order-reference extraction and lookup.
- Separates distributor order references from manufacturer part numbers.
- Adds DRL-known DGK/MSR/NWK/DSK order-reference handling.
- Reclassifies matching v1.4.6 replacement PN entries in a new enriched output only.
- Adds indexed local SQLite lookup by RMA and order reference.
- Uses all frozen event documents, including v1.4.6 supporting cards, for tracking metadata.
- No NAS rediscovery/rescan; no 14B event rerun; no Qdrant; accepted facts remain 0.
