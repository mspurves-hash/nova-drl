# Git / Validation Steps — v1.3.4.4.5

Extract this ZIP directly into the Nova DRL repository root.

On Ubuntu:

```bash
cd /opt/nova-drl && git pull
```

Run tests:

```bash
python3 tests/test_traveler_reader_v1_3_4_4_5.py
```

Third-event validation, no expected-entry hint:

```bash
python3 ingest/nova_traveler_reader_v1_3_4_4_5.py /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH --log=150622005 --detect-only
```

Open the complete printed-outline crop:

```bash
xdg-open /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH/150622005/vision_extraction_v1_3_4_4_5/repairs_replacements_full_outline.png
```

Review report:

```bash
cat /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH/150622005/vision_extraction_v1_3_4_4_5/traveler_relevance_review_v1_3_4_4_5.txt
```

Do not proceed to transcription or downstream fusion until the outline and row review are visually validated.
