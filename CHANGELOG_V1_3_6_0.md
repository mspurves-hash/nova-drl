# Changelog v1.3.6.0

- Added acquisition-after-the-fact Traveler corpus sorting stage.
- Qwen3-VL 8B is now explicitly the **high-recall prospector** over completed raw Traveler transcriptions.
- Qwen2.5 32B is explicitly the **cross-record reasoning** layer.
- Added Python evidence support verification for every 8B candidate quote.
- Added Python recurrence enforcement: at least 2 distinct logs and 2 distinct source hashes.
- Added deterministic repeated-line inventory.
- Added global sort-view suppression for `Hours in Final Testing` while preserving immutable raw evidence.
- Added audit outputs for unsupported 8B candidates and rejected 32B groups.
- No source mutation, no automatic approvals, no Qdrant writes.
