# Nova DRL Traveler Reader v1.3.4.2

## Row Coverage and Boundary Fix

v1.3.4.1 successfully found four initials/date anchors, but a correct anchor
count did not guarantee that every blue repair-entry box contained all of its
handwriting. Some handwritten descriptions crossed the first horizontal form
line beneath an anchor.

v1.3.4.2 keeps the four-anchor detector and adds an independent description-
column coverage check.

## Corrections

- Detects the left edge of the description column when possible.
- Builds a form-grid-free row-ink profile from the handwritten description
  column.
- Tests each candidate bottom boundary for handwriting immediately below it.
- Advances to the next horizontal form line when handwriting crosses a
  candidate boundary.
- Pairs every initials/date anchor with the handwriting contained in its entry
  band.
- Requires every expected entry to contain description ink.
- Rejects boundaries that cross a detected description-ink run.
- Reports a numerical description-coverage ratio.
- Reconstructs the repairs crop through 100% of the original traveler width.
- Detects and reports dates that touch the source image's right edge.
- Stops MiniCPM-V when anchor count or row coverage fails.
- Keeps all outputs marked `accepted_as_fact: false`.

## Confirmed DRL terminology

The existing user-confirmed glossary remains active:

- `Comm's` means motor **commutators**.
- `KEAL` is the **Keal shipping container** used for Genmark GB8 robots.

Literal traveler wording remains separate from normalized terminology.

## Automated test

```bash
cd /opt/nova-drl
python3 tests/test_traveler_reader_v1_3_4_2.py
```

Expected:

```text
PASS: Nova Traveler Reader v1.3.4.2 tests
```

## First run: detection only

Copy this single-line command:

```bash
python3 ingest/nova_traveler_reader_v1_3_4_2.py /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80050477_UTI_MTV_ERICH --log=230809002 --expected-entries=4 --detect-only
```

Open the result folder:

```bash
nautilus /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80050477_UTI_MTV_ERICH/230809002/vision_extraction_v1_3_4_2
```

Inspect:

```text
anchor_detection_debug.png
```

The desired row result is:

```text
Expected: 4
Detected: 4
Coverage: OK
Boundary cuts: 0
Entries with text: 4/4
```

The date status may say:

```text
Date right edge: REVIEW
```

That means the source traveler itself reaches or clips the right edge of the
available image. It does **not** invalidate the repair descriptions, but every
date remains human-review-required.

The four blue boxes should contain:

1. A1/A2 arm rebuild, bearings, deep clean, belts, and vac line
2. all three Z motor rebuilds, bearings, and machined `Comm's`
3. scanner-sensor set screws, replacement, and re-alignment
4. cleaned and regreased Z lead screws

Orange boxes show the detected description-handwriting extent inside each blue
entry box.

## Full vision run

Only after the four row boxes are correct:

```bash
python3 ingest/nova_traveler_reader_v1_3_4_2.py /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80050477_UTI_MTV_ERICH --log=230809002 --expected-entries=4
```

Inspect:

```bash
less /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80050477_UTI_MTV_ERICH/230809002/vision_extraction_v1_3_4_2/repair_entries_v1_3_4_2.txt
```

## Safety interlocks

MiniCPM-V is stopped when:

- expected and detected anchor counts differ;
- any expected entry lacks description handwriting;
- a selected boundary crosses a description-ink run;
- a safe horizontal boundary cannot be found; or
- description coverage falls below 98%.

The DRL NAS remains read-only. Nothing is written to Qdrant.
