# Nova DRL Full-Corpus Recurring Parts Gate v1.7.3

## Why this is the next stage

The v1.7.2 10-family audit validated the conservative architecture:

- 508 candidate buckets
- 451 one-offs (~89%)
- only 57 recurring candidates
- electronics boards showed strong recurring PN structure
- mechanical/robot/motor families were often descriptive and long-tailed
- fuzzy prefix/suffix similarity is useful for candidate discovery but unsafe as an automatic merge

The server inspection then confirmed that Nova already has the full v1.5.2 corpus:

```text
/opt/nova-drl/output/drl_full_corpus_v1_5_2/repair_events_v1_5_2.jsonl
/opt/nova-drl/output/drl_full_corpus_v1_5_2/replacement_mentions_v1_5_2.jsonl
```

So v1.7.3 does **not** re-extract Travelers. It gates the full existing Parts corpus.

## Safe behaviors

### Automatic
- whitespace/punctuation-only PN variants share an alphanumeric identity key;
- exact recurring descriptions are surfaced within each equipment family;
- recurring evidence is counted across distinct repair events;
- specs such as `47uF`, `33V`, and `15A 250V` are classified as component specs, not manufacturer PNs;
- numeric-only markings remain explicitly ambiguous.

### Supplier numbers
- Mouser `511-` and DigiKey `-ND` are detected before canonical handling;
- supplier aliases cannot create a manufacturer identity;
- a supplier alias attaches to a body only when that body is independently observed as a non-supplier PN.

### Never automatic
- missing prefixes;
- missing suffixes;
- weighted OCR similarity;
- model/package suffixes;
- numeric-model changes.

Those appear only in `fuzzy_audit_pairs_v1_7_3.jsonl`.

## 80/20 outputs

Normal Parts output requires recurrence.

Defaults:
- family output: 2+ repair events;
- global manufacturer-PN validation queue: 3+ repair events;
- one-offs are not copied into normal output; they remain preserved in frozen v1.5.2 evidence.

## First run

```bash
cd /opt/nova-drl

python3 test_nova_parts_full_corpus_gate_v1_7_3.py

python3 nova_parts_full_corpus_gate_v1_7_3.py --plan-only
```

The script uses Python's built-in `sqlite3` module to read the existing knowledge
index snapshot, so the missing `sqlite3` shell command does not matter.

## What to inspect

The plan-only summary should answer:

1. How many full-corpus replacement repair events exist?
2. How many equipment families have Parts history?
3. How many recurring family Parts survive the 2+ repair gate?
4. What percentage of replacement repairs are covered by recurring Parts?
5. How large is the manufacturer-PN validation queue?
6. How many supplier aliases resolve to an independently observed body?
7. How many fuzzy audit pairs remain after numeric-model vetoes?

Do **not** manually review the one-off long tail.
