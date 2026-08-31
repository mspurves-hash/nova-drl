# NOVA DRL Full-Corpus Unified Knowledge Index v1.5.3

v1.5.3 connects the completed v1.5.2 full-corpus ingestion to the existing instant NOVA-DRL retrieval interface.

## Inputs
- `/opt/nova-drl/index/drl_file_index.sqlite` — full DRL file metadata index.
- `/opt/nova-drl/output/drl_full_corpus_v1_5_2/repair_events_v1_5_2.jsonl`
- `replacement_mentions_v1_5_2.jsonl`
- `rma_refs_v1_5_2.jsonl`
- `customer_po_refs_v1_5_2.jsonl`
- `procurement_refs_v1_5_2.jsonl`

## Output
- `/opt/nova-drl/index/drl_knowledge_index.sqlite`

The output path is intentionally the same as the previous unified knowledge index, so the engineer interface automatically begins using the full corpus after a successful build.

## Retrieval policy
Known information belongs in the local index: filenames, folders, equipment/model, DRL log, RMA, Customer PO, manufacturer PN, procurement references, repair history, and product-specific parts usage. AI is reserved for interpretive questions such as troubleshooting a GB8 Y-axis drift.

## Safety / 80-20 policy
- Existing v1.5.2 corpus is read only.
- DRL share is not rescanned or modified.
- Tracking identifiers require literal evidence.
- General repair/parts knowledge keeps the fixed 80/20 approach.
- No Qdrant writes.
