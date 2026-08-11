# Nova DRL Terminology Layer v1.5.2

## Purpose

Nova DRL contains decades of technician shorthand. v1.5.2 introduces a
non-destructive terminology layer so Nova can understand shop language
without rewriting historical evidence.

Core rule:

```text
raw / approved wording: BERS
normalized meaning:     bearings
```

The approved wording remains exactly `BERS`.

## Initial human-confirmed terms

- `BERS` -> `bearings`
- `Comm's` -> `commutators`
- `KEAL` -> `KEAL shipping container`

`FE` is intentionally unresolved in v1.5.2. Nova preserves it exactly and
does not guess its meaning.

## Input

v1.5.1 `approved_repair_fields.json`.

## Output

- `approved_repair_fields_with_terminology.json`
- `terminology_matches.json`
- `terminology_review.txt`

No v1.5.1 file is modified.

## Test

```bash
cd /opt/nova-drl
python3 tests/test_drl_terminology_v1_5_2.py
```

Expected:

```text
PASS: Nova DRL Terminology Layer v1.5.2 tests
```

## Pilot

```bash
python3 ingest/nova_drl_terminology_v1_5_2.py /opt/nova-drl/output/evidence_fusion_v1_5_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH/events/130813004
```

Expected pilot shape:

```text
Approved field groups:     2
Approved repair actions:   2
Terminology matches:       1
Distinct DRL terms:        1
Approved wording modified: NO
Qdrant entries created:    0
```

The expected match is:

```text
BERS -> bearings
```

## Design for future slang

New terms belong in `config/drl_terminology_v1_5_2.json`. Each entry has a
scope and status. Nova uses exact/alias matching only; it does not fuzzy-guess
unknown technician abbreviations.
