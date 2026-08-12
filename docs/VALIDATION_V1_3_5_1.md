# Validation — v1.3.5.1

## Required packaged test

```bash
python3 tests/test_traveler_reader_v1_3_5_1.py
```

Expected:

```text
PASS: Nova Whole Traveler Corpus Collector v1.3.5.1 tests
```

## First DRL inventory validation

The rich GB8-MT serial folder has 11 repair events and one event without a Traveler, so the expected Traveler-image count is 10:

```bash
python3 ingest/nova_traveler_reader_v1_3_5_1.py "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN 80010732 UTI MICRON ERICH" --inventory-only --expect-travelers=10
```

This performs discovery and hashes only. It makes no Qwen3-VL calls.

## First acquisition pass

After inventory count is confirmed:

```bash
python3 ingest/nova_traveler_reader_v1_3_5_1.py "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN 80010732 UTI MICRON ERICH" --expect-travelers=10
```

The collector is sequential by design because one 22 GB GPU is used. It writes each Traveler record immediately and updates the corpus manifest after every completion.

## Success criteria

- all 10 Traveler source images represented;
- original source SHA-256 preserved;
- raw Qwen3-VL transcription saved for each completed source;
- zero classifications performed;
- zero accepted repair facts;
- zero Qdrant entries;
- no DRL source modifications;
- exact duplicate hashes flagged, not suppressed.
