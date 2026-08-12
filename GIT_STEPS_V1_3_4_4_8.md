# Git / Validation Steps v1.3.4.4.8

After merging this flat ZIP into the repository and pushing from Windows:

```bash
cd /opt/nova-drl && git pull
python3 tests/test_traveler_reader_v1_3_4_4_8.py
python3 ingest/nova_traveler_reader_v1_3_4_4_8.py /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH --log=150622005 --detect-only
```

Inspect:

```bash
xdg-open /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH/150622005/vision_extraction_v1_3_4_4_8/repairs_replacements_outer_box.png
```

Do not run transcription until the complete outer box is visually confirmed.
