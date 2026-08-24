# CHANGELOG — v1.5.0

## Nova DRL Full Repair-History Corpus Ingester

- Scales from the frozen 10% benchmark to the full indexed tech-scan repair-folder universe.
- One unified Qwen3-VL pass captures repair-history evidence plus RMA, Customer PO and procurement/order metadata.
- RMA/PO/order identifiers require literal supporting evidence; 80/20 guessing is never used for tracking identifiers.
- DGK/MSR/NWK/DSK procurement references are kept out of manufacturer-PN replacement knowledge.
- Reuses matching frozen v1.4.6 technical evidence, v1.4.7 tracking evidence, and v1.4.7 structured event records when safe.
- Keeps `.picasa.ini` / `.picasaoriginals` out of repair knowledge.
- Preserves Roger-only typed `(2)` card optimization.
- No NAS rescan, no Qdrant, accepted facts remain 0.
