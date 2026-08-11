# Nova DRL Terminology Review Queue v1.5.2.1

## Why this exists

DRL travelers contain decades of technician shorthand. Constant human prompts
would be disruptive, so Nova now uses a **frequency-weighted review queue**.

An unknown term seen once can wait. A term appearing across many repair events,
serial numbers, or models rises in priority because one human definition can
improve a large amount of historical repair knowledge.

## Ranking principle

The primary frequency measure is **unique repair events**, not raw OCR hit
count.

Priority also considers:

- unique serial numbers
- unique models
- years spanned
- the technical importance of the field
- whether the evidence is structured/human-approved or OCR-only

Repeated appearances in the same repair event do not inflate the unique-event
count.

## Human intervention policy

Nova does **not** interrupt processing for every unknown acronym.

Default:

```text
LOW / MEDIUM unknown -> add to queue
HIGH unknown in a consequential field -> mark ask_now
```

Consequential fields currently include:

- approved repair actions
- approved customer complaints
- structured traveler repair actions
- diagnostic notes

The queue creates the recommendation; a future UI can turn `ask_now` into an
actual popup.

## Human decisions

Supported decisions:

```text
Define
Defer
Ignore
```

Definitions create a **derived effective glossary**. The original glossary and
historical traveler wording are not modified.

## Human-confirmed terms now included

### BERS

```text
BERS -> bearings
scope: DRL_shop
```

### Comm's

```text
Comm's -> commutators
scope: DRL_shop
```

### KEAL

```text
KEAL -> KEAL shipping container
scope: DRL_shop
```

### FE

```text
FE -> numeric homing value between home-sensor detection by the home flag
      and subsequent encoder-index detection
scope: OEM=GENMARK;equipment=ROBOT
```

No expansion of the letters `F-E` is invented.

## Test

```bash
cd /opt/nova-drl
python3 tests/test_terminology_review_queue_v1_5_2_1.py
python3 tests/test_drl_terminology_v1_5_2_1.py
```

## First DRL pilot

Start with the derived history we have already processed:

```bash
python3 ingest/nova_terminology_review_queue_v1_5_2_1.py /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH /opt/nova-drl/output/evidence_fusion_v1_5_2 --output-root /opt/nova-drl/output/terminology_review_queue_v1_5_2_1
```

Review:

```bash
less /opt/nova-drl/output/terminology_review_queue_v1_5_2_1/terminology_review_queue.txt
```

Important output files:

- `terminology_review_queue.json`
- `terminology_review_queue.txt`
- `terminology_occurrences.json`
- `known_term_usage.json`
- `terminology_review_decisions.json`
- `effective_glossary.json`

## Define a term

Example only:

```bash
python3 ingest/nova_terminology_review_queue_v1_5_2_1.py <same scan roots> --output-root /opt/nova-drl/output/terminology_review_queue_v1_5_2_1 --decision=define --term="XYZ" --meaning="human confirmed meaning" --scope="OEM=GENMARK" --category="technician_abbreviation" --reviewer="Matt Purves" --note="Defined from DRL repair history."
```

## Defer a term

```bash
... --decision=defer --term="XYZ" --reviewer="Matt Purves"
```

## Ignore a false term / OCR artifact

```bash
... --decision=ignore --term="XYZ" --reviewer="Matt Purves" --note="OCR artifact."
```

## Retroactive annotation

After a definition is saved, `effective_glossary.json` contains the merged
human-confirmed glossary. Pass it to the terminology layer when re-annotating
an approved repair event:

```bash
python3 ingest/nova_drl_terminology_v1_5_2_1.py <approved-event-directory> --glossary /opt/nova-drl/output/terminology_review_queue_v1_5_2_1/effective_glossary.json
```

This creates new derived annotations without changing the original approved
repair values.

## Safety

- derived outputs only
- no production DRL file changes
- no silent acronym expansion
- no fuzzy guessing of unknown terminology
- no Qdrant writes
