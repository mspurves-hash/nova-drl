# Git steps — v1.3.5.1

Extract this FLAT ZIP directly into the Windows Git working-copy root, then commit and push with GitHub Desktop.

On Ubuntu:

```bash
cd /opt/nova-drl && git pull
```

Run packaged test:

```bash
python3 tests/test_traveler_reader_v1_3_5_1.py
```

Inventory the rich GB8-MT serial folder before vision:

```bash
python3 ingest/nova_traveler_reader_v1_3_5_1.py "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN 80010732 UTI MICRON ERICH" --inventory-only --expect-travelers=10
```

Then acquire all 10 Travelers:

```bash
python3 ingest/nova_traveler_reader_v1_3_5_1.py "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN 80010732 UTI MICRON ERICH" --expect-travelers=10
```

Inspect summary:

```bash
cat /opt/nova-drl/output/whole_traveler_corpus_v1_3_5_1/corpus_summary_v1_3_5_1.txt
```

The command can be rerun safely; matching completed raw transcriptions are reused unless `--force` is supplied.
