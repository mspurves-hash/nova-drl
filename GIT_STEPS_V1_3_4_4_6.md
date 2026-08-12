# Git / Validation Steps — v1.3.4.4.6

Extract this flat ZIP directly into the Nova DRL repository root, commit, and push from Windows.

On Ubuntu:

```bash
cd /opt/nova-drl && git pull
```

Run tests:

```bash
python3 tests/test_traveler_reader_v1_3_4_4_6.py
```

Run 150622005 with no expected-entry hint:

```bash
python3 ingest/nova_traveler_reader_v1_3_4_4_6.py /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH --log=150622005 --detect-only
```

Open the recovered complete printed table:

```bash
xdg-open /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH/150622005/vision_extraction_v1_3_4_4_6/repairs_replacements_full_outline.png
```

Review report:

```bash
cat /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH/150622005/vision_extraction_v1_3_4_4_6/traveler_relevance_review_v1_3_4_4_6.txt
```
