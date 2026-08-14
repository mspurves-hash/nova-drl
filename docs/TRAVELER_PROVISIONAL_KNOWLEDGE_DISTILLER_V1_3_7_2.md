# Nova DRL Provisional Knowledge Distiller v1.3.7.2

## Purpose

v1.3.7.2 turns completed v1.3.7.1 recurring-pattern output into readable GB8 technician knowledge. It is a **Python-only post-processing layer**. It does not rerun vision, prospecting, Stage-1 reasoning, or hierarchical merge reasoning.

The operating philosophy remains **FAST PROVISIONAL 80/20**: surface the useful patterns now, preserve the evidence trail, and amend interpretations later when technicians encounter something questionable.

## Input

Default input:

`/opt/nova-drl/output/traveler_large_scale_reason_v1_3_7_1/recurring_patterns_v1_3_7_1.json`

The current completed GB8 run contains 419 recurring groups produced from 7,309 reasoning-eligible candidates. Recurrence was already Python-counted by v1.3.7.1 using at least 2 distinct logs and 2 distinct source hashes.

## What v1.3.7.2 does

- Ranks recurring patterns by distinct robot serials, then distinct logs, then candidate count.
- Suppresses only clearly recognizable Traveler form/template remnants from the **main view**.
- Retains suppressed groups in a separate audit JSON; nothing is deleted from v1.3.7.1.
- Builds overlapping service-area rollups using deterministic keyword rules and unions distinct log/serial coverage within each area.
- Produces a provisional parts/stocking-attention view for repeated item families such as belts, bearings, brushes, vacuum lines, solenoids, filters, lubricants, shims, sensors, connectors, encoders, and motors.
- Produces a technician-readable GB8 report with representative raw Traveler evidence under each pattern.
- Produces CSV/JSON outputs for later UI or search integration.

## What it does NOT do

- No LLM calls.
- No OCR/vision calls.
- No automatic fact approval.
- No Qdrant writes.
- No approved SOP generation.
- No approved BOM or stocking quantities.
- No modification of v1.3.7.1 or earlier baselines.

## Default run

```bash
python3 analysis/nova_traveler_knowledge_distiller_v1_3_7_2.py
```

Default output root:

`/opt/nova-drl/output/traveler_knowledge_distill_v1_3_7_2`

## Primary outputs

- `gb8_provisional_knowledge_report_v1_3_7_2.txt`
- `gb8_provisional_knowledge_report_v1_3_7_2.md`
- `distilled_recurring_patterns_v1_3_7_2.json`
- `ranked_recurring_patterns_v1_3_7_2.csv`
- `service_area_rollup_v1_3_7_2.json`
- `stocking_attention_v1_3_7_2.json`
- `stocking_attention_v1_3_7_2.csv`
- `suppressed_template_noise_v1_3_7_2.json`
- `knowledge_distiller_manifest_v1_3_7_2.json`

The v1.3.7.1 recurring-pattern file remains the full provenance authority for this layer.
