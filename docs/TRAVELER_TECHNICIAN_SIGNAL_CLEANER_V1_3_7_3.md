# Nova DRL Technician Signal Cleaner v1.3.7.3

## Purpose

v1.3.7.3 is a Python-only presentation layer over the completed v1.3.7.2 distill. It does not rerun Travelers, prospecting, or reasoning.

The goal is 80/20 technician usefulness:

- keep repairs, diagnostics, components, and testing in the technician ranking;
- route customer requirements, terminology, shipping/admin, and obvious form/identity noise to reference-only outputs;
- assign service areas from the recurring group's dominant label/key rather than incidental words buried in mixed evidence;
- make stocking attention more conservative.

## Preservation

v1.3.7.3 reconstructs all v1.3.7.2 recurring groups from the main and suppressed outputs and verifies the total against the v1.3.7.2 manifest. No recurring group is deleted or rewritten.

- v1.3.5.1 acquisition: untouched
- v1.3.6.1 evidence: untouched
- v1.3.7.1 recurring patterns: untouched
- v1.3.7.2 outputs: untouched
- new LLM calls: 0
- accepted facts: 0
- Qdrant: OFF

## Main outputs

- `gb8_technician_signal_report_v1_3_7_3.txt`
- `gb8_reference_patterns_v1_3_7_3.txt`
- `technician_patterns_v1_3_7_3.json`
- `reference_patterns_v1_3_7_3.json`
- `routing_audit_v1_3_7_3.json`
- `service_area_rollup_v1_3_7_3.json`
- `stocking_attention_v1_3_7_3.json`
- `technician_signal_manifest_v1_3_7_3.json`

## Default run

```bash
python3 analysis/nova_traveler_technician_signal_cleaner_v1_3_7_3.py
```

Default input:

`/opt/nova-drl/output/traveler_knowledge_distill_v1_3_7_2`

Default output:

`/opt/nova-drl/output/traveler_technician_signal_v1_3_7_3`
