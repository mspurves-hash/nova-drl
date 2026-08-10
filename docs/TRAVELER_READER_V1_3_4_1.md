# Nova DRL Traveler Reader v1.3.4.1

## Anchor Detection Fix

v1.3.4.1 corrects the first live v1.3.4 detection test.

The v1.3.4 debug image found only three anchors because:

1. anchor scanning started at 30% of the repairs-table crop, below the first
   `EF / date` entry;
2. the first repair band was forced to start at 27% of the crop, cutting off
   the first two-line A1/A2 repair;
3. the v1.3.1 parent crop ended at 96% of page width, clipping part of the
   handwritten date column;
4. `--expected-entries` reported a mismatch but did not stop the vision run.

## Corrections

- Starts anchor detection immediately below the detected repairs-table header.
- Uses the detected description/initials/date vertical rules.
- Reconstructs an expanded repairs-table crop from the original traveler
  through the full right edge when the original image is available.
- Detects the top A1/A2 repair anchor.
- Uses actual horizontal form rules to build unequal-height repair bands.
- Extends full-row and date crops to the complete parent-image right edge.
- Treats `--expected-entries` as a safety interlock.
- Stops MiniCPM-V processing when expected and detected counts do not match.
- Adds an explicit debug status:
  `Expected / Detected / OK or REVIEW REQUIRED`.
- Keeps `accepted_as_fact` false for every extracted repair.

## Confirmed DRL terminology retained

- `Comm's` means motor **commutators**.
- `KEAL` is the **Keal shipping container** used for Genmark GB8 robots.

Literal traveler wording remains unchanged. The confirmed meaning is stored in
a separate glossary layer.

## Test

```bash
cd /opt/nova-drl
python3 tests/test_traveler_reader_v1_3_4_1.py
```

Expected:

```text
PASS: Nova Traveler Reader v1.3.4.1 tests
```

## First run: detection only

Use this one-line command:

```bash
python3 ingest/nova_traveler_reader_v1_3_4_1.py /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80050477_UTI_MTV_ERICH --log=230809002 --expected-entries=4 --detect-only
```

Open the output folder:

```bash
nautilus /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80050477_UTI_MTV_ERICH/230809002/vision_extraction_v1_3_4_1
```

Inspect:

```text
anchor_detection_debug.png
```

The desired result is:

```text
Expected: 4
Detected: 4
Status: OK
```

The four blue boxes should contain:

1. A1/A2 arm rebuild
2. all three Z motor rebuilds / machined Comm's
3. scanner-sensor set screws / re-alignment
4. cleaned and regreased Z lead screws

## Full vision run

Only after the detection image is correct:

```bash
python3 ingest/nova_traveler_reader_v1_3_4_1.py /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80050477_UTI_MTV_ERICH --log=230809002 --expected-entries=4
```

Inspect:

```bash
less /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80050477_UTI_MTV_ERICH/230809002/vision_extraction_v1_3_4_1/repair_entries_v1_3_4_1.txt
```

## Safety behavior

When the detected count does not equal `--expected-entries`, v1.3.4.1 writes
the debug image and reports:

```text
Status: review_required_anchor_count_mismatch
Vision processing stopped: YES
```

No MiniCPM-V request is made for that traveler.

The DRL NAS remains read-only. Nothing is written to Qdrant.
