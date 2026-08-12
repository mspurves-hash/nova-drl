# Git / Validation Steps v1.3.5.0

After merging the flat ZIP into the Windows Git working copy, commit and push.

On Ubuntu:

```bash
cd /opt/nova-drl && git pull
```

```bash
python3 tests/test_traveler_reader_v1_3_5_0.py
```

Detect-only validation for 150622005:

```bash
python3 ingest/nova_traveler_reader_v1_3_5_0.py /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH --log=150622005 --detect-only
```

Open the complete evidence image:

```bash
xdg-open /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH/150622005/whole_traveler_evidence_v1_3_5_0/whole_traveler_evidence.png
```

Review the report:

```bash
cat /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH/150622005/whole_traveler_evidence_v1_3_5_0/whole_traveler_evidence_v1_3_5_0.txt
```

Do not run transcription until the full-page image is visually confirmed intact.
