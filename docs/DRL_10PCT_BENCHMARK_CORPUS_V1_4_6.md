# Nova DRL v1.4.6 — 10% Benchmark Corpus Ingester

This release scales the proven DRL-index + Traveler workflow from selected model families to a fixed benchmark sample of the whole historical repair-folder universe.

## Why a fixed 10% sample

The full tech-scan corpus currently contains thousands of top-level equipment/repair folders. v1.4.6 uses a deterministic SHA256-ranked sample and freezes it on the first real run. This gives Nova a repeatable benchmark corpus for future code/model comparisons without processing the entire archive on every development iteration.

## 80/20 scope

Line Cards/Travelers are treated primarily as repair-history and parts-used evidence. The ingester does not require perfect OCR and does not invent detailed fixes, calibration, or test procedures when they are absent. Later Operations Checklist and manual ingestion layers will add richer procedural knowledge.

## Data flow

`DRL SQLite index -> fixed sampled folders -> Line Card/Traveler selection -> repair-event grouping -> Roger-only (2) priority -> 8B high-signal evidence -> 14B structured event record -> local JSONL/CSV benchmark corpus`

## Important behaviors

- No recursive NAS discovery scan.
- No writes to the DRL share.
- Sample folders are frozen after first persisted run unless `--force-sample` is explicitly used.
- Folders where filename detection does not find a supported Traveler are reported as exceptions rather than assumed empty.
- Unrelated equipment families remain separate.
- No corpus-wide LLM clustering in v1.4.6; ingestion first, intelligence later.
- Accepted facts remain 0 and Qdrant is off.
