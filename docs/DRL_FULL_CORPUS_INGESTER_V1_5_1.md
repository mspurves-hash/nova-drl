# DRL Full Corpus Ingester v1.5.1

## Goal
Ingest the full repair-folder universe under `000 folder for tech scans` using the persistent DRL file index and the validated 80/20 Traveler pipeline.

## Evidence model
Travelers/Line Cards are primarily repair-history and parts-used evidence. Detailed procedures/testing are not invented when absent. Operations Checklists and manuals will provide richer procedural knowledge later.

## Tracking fields
- DRL log: deterministic event identity when valid.
- RMA: strict literal first-class tracking field.
- Customer PO: strict literal field, separate from purchasing refs.
- Procurement/order refs: strict literal purchasing data.
  - DGK → Digi-Key
  - MSR → Mouser
  - NWK/DSK → procurement ref, supplier unknown unless visible
- Procurement refs are excluded from manufacturer-PN replacement ranking.

## Reuse
Exact-match events from the frozen v1.4.7 10% corpus are reused when current primary/supporting source path sets match exactly. Reused tracking is revalidated literally.

## Outputs
Default output root: `/opt/nova-drl/output/drl_full_corpus_v1_5_1`

Key outputs:
- `repair_events_v1_5_1.jsonl`
- `replacement_mentions_v1_5_1.jsonl/csv`
- `rma_lookup_v1_5_1.csv`
- `customer_po_lookup_v1_5_1.csv`
- `procurement_refs_v1_5_1.csv`
- `drl_full_corpus_summary_v1_5_1.txt`

Full-corpus manifest:
`/opt/nova-drl/corpus/drl_full_corpus_v1_5_1/full_corpus_manifest_v1_5_1.json`
