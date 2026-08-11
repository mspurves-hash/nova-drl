# Nova DRL Terminology Review Queue v1.5.2.3

## Purpose

v1.5.2.3 makes terminology discovery **authority-aware**.

A lower-authority machine reading must not create vocabulary-review work after
a human has already approved corrected wording for that repair event.

Example from log `130813004`:

```text
Machine transcription:
X ADDED Flanged BCS X2 to A1+A3 WALK LINK

Human-approved repair action:
Added Flanges BERS x2 to A1 + A2 upper link
```

For terminology discovery, the approved action now shadows the structured
machine repair description and raw Repairs/Replacements OCR. The machine
reading remains fully preserved in its original audit artifacts.

Therefore:

```text
BCS  -> does not enter terminology queue from the superseded reading
WALK -> does not enter terminology queue from the superseded reading
BERS -> known terminology from the human-approved wording
```

Diagnostic notes are NOT shadowed because they may contain independent
diagnostic evidence.

## Human-confirmed technician metadata

These are metadata identities, not terminology:

```text
EF = Erich Franke
VT = Victor Thomas
SF = Stefen Franke
MP = Matt Purves
NP = Nate Purves
RB = Roger Bingham
AM = Anthony Moulazimis
MB = Mary Ann Bingham
BP = Barbara Purves
```

They remain exactly as initials in historical traveler evidence while the
technician name is stored separately.

## Human-confirmed site metadata

```text
MTV = Micron Technology Virginia
```

`MTV` is a site code and is suppressed from terminology review.

## Existing human-confirmed terminology

```text
FA      -> Failure Analysis
RPT     -> Report
FA RPT  -> Failure Analysis Report
BERS    -> bearings
Comm's  -> commutators
KEAL    -> KEAL shipping container
FE      -> Genmark home-sensor-to-encoder-index homing value
```

No expansion of the letters F-E is invented.

## Additional noise filtering

v1.5.2.3 also adds:

- expanded common-word suppression (`SHIP`, `WOOD`, `CRATE`, `AIR`, `LINK`,
  `WALK`, `ADDED`, etc.)
- low-support gate for 2-character OCR-only fragments
- low-support gate for 3-character OCR-only fragments
- audit output for authority-shadowed evidence
- audit output for low-support OCR suppressions

Short OCR fragments are only suppressed from the human review queue. The
underlying source evidence remains intact.

## New audit files

```text
terminology_shadowed_evidence.json
terminology_low_support_suppressions.json
```

These make the authority/noise decisions independently reviewable.

## Tests

```bash
cd /opt/nova-drl
python3 tests/test_terminology_review_queue_v1_5_2_3.py
python3 tests/test_drl_terminology_v1_5_2_3.py
```

## Rerun the same GB8 pilot

Use the same derived inputs so v1.5.2.3 can be compared directly with
v1.5.2.2:

```bash
python3 ingest/nova_terminology_review_queue_v1_5_2_3.py /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH /opt/nova-drl/output/evidence_fusion_v1_5_2 --output-root /opt/nova-drl/output/terminology_review_queue_v1_5_2_3
```

Review:

```bash
less /opt/nova-drl/output/terminology_review_queue_v1_5_2_3/terminology_review_queue.txt
```

Expected qualitative changes:

- `EF` and `VT` disappear from unresolved terminology and are suppressed as
  technician metadata
- `SF`, `MP`, `NP`, `RB`, `AM`, `MB`, `BP` are also recognized as technician
  initials
- `BCS` and `WALK` disappear when they come only from the superseded machine
  repair transcription for an event with human-approved repair actions
- common words such as `SHIP`, `WOOD`, `CRATE`, `AIR`, `LINK`, `ADDED` are
  suppressed
- weak 2-character OCR fragments require at least 3 unique repair events
- weak 3-character OCR fragments require at least 2 unique repair events
- consequential high-frequency terms across multiple serials still rise to
  `ask_now`

## Safety

- no historical traveler wording rewritten
- no approved human wording modified
- no lower-authority evidence deleted
- no unknown terminology meaning guessed
- no DRL source files modified
- no Qdrant writes
