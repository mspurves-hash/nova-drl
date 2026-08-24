# Nova DRL Full Repair-History Corpus Ingester v1.5.0

This release moves DRL Nova from the fixed 10% benchmark to a full repair-history corpus.

## Production source
The persistent DRL File Index is the discovery authority. v1.5.0 snapshots the current top-level folders under `000 folder for tech scans`, then selects Line Card/Traveler image/PDF sources from the index. It never performs a recursive NAS discovery scan.

## One-pass enrichment
Each new/changed source is read once by Qwen3-VL for both high-signal repair evidence and literal tracking metadata: RMA, Customer PO, and procurement/order references. A 14B event call structures the technical repair record.

## Tracking versus 80/20
The 80/20 rule governs noisy repair/parts interpretation. It does **not** govern identifiers. RMA, Customer PO, and supplier/order references must be visibly grounded.

- `DGK...` = Digi-Key order reference.
- `MSR...` = Mouser order reference.
- `NWK...` / `DSK...` = procurement reference; supplier unknown unless visible.
- Procurement references do not become manufacturer PNs.

## Reuse
Matching v1.4.6/v1.4.7 evidence is reused by source path + image SHA where possible. Matching v1.4.7 structured event records may also be reused, so the successful frozen 10% benchmark does not needlessly rerun.

## Outputs
- `repair_events_v1_5_0.jsonl`
- `replacement_mentions_v1_5_0.jsonl/csv`
- `rma_refs_v1_5_0.jsonl/csv`
- `customer_po_refs_v1_5_0.jsonl/csv`
- `procurement_refs_v1_5_0.jsonl/csv`
- `drl_full_corpus_summary_v1_5_0.txt`

The existing 10% benchmark remains unchanged for regression testing.
