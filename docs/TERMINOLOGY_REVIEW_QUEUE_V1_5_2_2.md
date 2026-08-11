# Nova DRL Terminology Review Queue v1.5.2.2

## Purpose

v1.5.2.2 makes the frequency-weighted terminology queue practical enough for
real DRL history by filtering OCR noise and metadata before it reaches human
review.

The v1.5.2.1 live pilot correctly elevated repeated terms, but also surfaced
ordinary uppercase words such as:

```text
INSIDE
UNIT
DO
AS
ON
GOES
TO
IS
```

It also placed the known site code `MTV` in the terminology queue.

v1.5.2.2 corrects those behaviors.

## Human-confirmed definitions added

```text
FA      -> Failure Analysis
RPT     -> Report
FA RPT  -> Failure Analysis Report
```

Historical wording remains unchanged.

`MTV` is stored separately as metadata:

```text
MTV -> Micron Technology Virginia
type: site code
```

It is suppressed from terminology review.

Existing human-confirmed terminology remains:

```text
BERS    -> bearings
Comm's  -> commutators
KEAL    -> KEAL shipping container
FE      -> Genmark home-sensor-to-encoder-index homing value
```

## Noise filtering

The queue now suppresses:

- common English words, even when OCR returns them in uppercase
- known site codes and metadata identifiers
- configured technician/OEM identifiers when available
- pure-alpha candidates longer than normal acronym/shorthand length
- model/OEM/equipment identifiers already known from the event folder

Suppressed terms are not deleted. They are recorded in:

```text
terminology_suppressions.json
```

with a suppression reason.

## Metadata discovery

The queue uses its own confirmed metadata file and also reads existing project
files when present:

```text
config/site_codes.json
config/technicians.json
config/oems.json
```

This lets the terminology queue improve as DRL metadata dictionaries improve.

## Template repetition correction

A term repeated in OCR-only customer instructions across many repair events
for one serial is not treated the same as a term used across many independent
serial numbers.

The queue now records:

```text
unique repair events
unique serials
unique models
template repetition
template priority penalty
```

A one-serial repeated OCR/template term receives a priority penalty while
remaining visible for review.

Serial diversity is weighted more strongly than in v1.5.2.1.

## Human intervention

Default remains:

```text
LOW / MEDIUM -> queue silently
HIGH + consequential field -> ask_now
```

`ask_now` is a recommendation for the future UI. The command-line queue does
not pop up dialogs.

## Tests

```bash
cd /opt/nova-drl
python3 tests/test_terminology_review_queue_v1_5_2_2.py
python3 tests/test_drl_terminology_v1_5_2_2.py
```

## Rerun the same GB8 pilot

```bash
python3 ingest/nova_terminology_review_queue_v1_5_2_2.py /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH /opt/nova-drl/output/evidence_fusion_v1_5_2 --output-root /opt/nova-drl/output/terminology_review_queue_v1_5_2_2
```

Review:

```bash
less /opt/nova-drl/output/terminology_review_queue_v1_5_2_2/terminology_review_queue.txt
```

Expected qualitative changes from v1.5.2.1:

- `FA` removed from unresolved queue; shown under known terminology
- `RPT` removed from unresolved queue; shown under known terminology
- `MTV` suppressed as site-code metadata
- common words such as `INSIDE`, `UNIT`, `DO`, `AS`, `ON`, `GOES`, `TO`,
  and `IS` suppressed
- unresolved queue substantially smaller than 163 terms
- remaining terms are more likely to be genuine DRL shorthand or OCR artifacts
  worth classifying

## Safety

- no source wording rewritten
- no production DRL files changed
- no unknown acronym meaning guessed
- no Qdrant writes
