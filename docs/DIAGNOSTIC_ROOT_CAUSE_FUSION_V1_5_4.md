# Nova DRL Diagnostic Hypothesis / Root Cause Fusion v1.5.4

## Purpose

v1.5.4 separates technician **diagnostic hypotheses** from **confirmed root
causes**.

For log `130813004`, the technician note says in substance that the high FE
value was suspected of causing the intermittent homing problem, while also
stating there was no concrete proof. That is useful diagnostic knowledge, but
it is not a confirmed root cause.

## Core rule

```text
uncertainty present
    -> diagnostic_hypothesis
    -> confirmed_root_cause = false
```

Even if the note also contains causal words such as `causing`.

A human-approved hypothesis never becomes a root cause automatically.

## Root cause rule

A note containing explicit causal/root-cause language without uncertainty may
become a `root_cause_candidate`, but still requires explicit human:

```text
confirm-root-cause
```

## FE terminology

The v1.5.2.3 human-confirmed definition is carried forward:

```text
FE = numeric homing value between home-sensor detection by the home flag
     and subsequent encoder-index detection
```

The letters F-E are not expanded or guessed.

## Test

```bash
cd /opt/nova-drl
python3 tests/test_diagnostic_root_cause_fusion_v1_5_4.py
```

## Pilot run

```bash
python3 ingest/nova_diagnostic_root_cause_fusion_v1_5_4.py /opt/nova-drl/output/evidence_fusion_v1_5_3/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004
```

Expected:

```text
Approved repair actions:     2
Diagnostic candidates:       1
Hypothesis candidates:       1
Root-cause candidates:       0
Approved hypotheses:         0
Confirmed root causes:       0
Root cause status:           not_established
Qdrant entries created:      0
```

## Review

```bash
cat /opt/nova-drl/output/evidence_fusion_v1_5_4/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004/diagnostic_root_cause_review.txt
```

The raw machine note is expected to contain the known `running` versus
`homing` transcription error. Do not approve it unchanged.

## Approve the human-verified hypothesis

```bash
python3 ingest/nova_diagnostic_root_cause_fusion_v1_5_4.py /opt/nova-drl/output/evidence_fusion_v1_5_3/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004 --decision=approve-hypothesis --candidate-number=1 --reviewer="Matt Purves" --value="High Y-FE may have caused the intermittent homing problem." --note="Verified visually against the traveler. Preserved as technician hypothesis; not a confirmed root cause."
```

After approval:

```text
Approved hypotheses:    1
Confirmed root causes:  0
Root cause status:      not_established
Qdrant entries created: 0
```

## Safety

- approved repair actions are never rewritten
- raw machine note remains attached
- uncertainty blocks root-cause confirmation
- hypotheses and root causes are stored separately
- no final repair summary is accepted
- no Qdrant writes
