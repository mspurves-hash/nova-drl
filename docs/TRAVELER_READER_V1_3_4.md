# Nova DRL Traveler Reader v1.3.4

## Anchored Repair-Entry Extraction

v1.3.4 replaces four equal repair bands with anchors detected from handwritten
activity in the initials/date columns.

For each detected entry it creates:

- full repair-row crop
- description crop
- initials crop
- date crop
- Tesseract output for every crop
- MiniCPM-V literal row transcription
- conditional field retries
- initials validation
- date plausibility relative to the DRL log date
- confirmed DRL glossary matches
- source coordinates and crop paths

No result is automatically accepted as a repair fact.

## Confirmed terminology

- `Comm's` means motor **commutators**.
- `KEAL` is the **Keal shipping container** used for GB8 robots.

Literal transcription remains unchanged. Confirmed meanings are stored in a
separate glossary-match layer.

## Test

```bash
cd /opt/nova-drl
python3 tests/test_traveler_reader_v1_3_4.py
```

Expected:

`PASS: Nova Traveler Reader v1.3.4 tests`

## Recommended first step: detection only

```bash
python3 ingest/nova_traveler_reader_v1_3_4.py /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80050477_UTI_MTV_ERICH --log=230809002 --expected-entries=4 --detect-only
```

Inspect:

```bash
nautilus /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80050477_UTI_MTV_ERICH/230809002/vision_extraction_v1_3_4
```

The key file is `anchor_detection_debug.png`.

## Full vision run

```bash
python3 ingest/nova_traveler_reader_v1_3_4.py /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80050477_UTI_MTV_ERICH --log=230809002 --expected-entries=4
```

Inspect:

```bash
less /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80050477_UTI_MTV_ERICH/230809002/vision_extraction_v1_3_4/repair_entries_v1_3_4.txt
```

## Safety

- DRL NAS remains read-only.
- No Qdrant ingestion occurs.
- `accepted_as_fact` remains false for every entry.
