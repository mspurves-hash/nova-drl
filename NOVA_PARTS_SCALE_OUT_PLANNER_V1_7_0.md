# Nova DRL Parts Scale-Out Planner v1.7.0

## Strategic change

v1.7.0 stops treating RCL1A as a product that must be perfected.

RCL1A remains a benchmark / labeled seed, but the next question is:

> Which equipment families contain most of the useful Parts replacement activity?

The planner reads the frozen v1.6.0 repair-event corpus:

`/opt/nova-drl/output/drl_global_lossless_corpus_v1_6_0/repair_events_lossless_v1_6_0.jsonl`

The v1.6.0 repair-event rows already contain:
- `repair_event_id`
- `equipment_family`
- `facts.parts_replaced`
- `facts.part_references`
- source-record IDs

No re-OCR, NAS scan, LLM, web lookup, or canonicalization is needed.

## Primary 80/20 metric

Families are ranked by:

**distinct repair events containing `facts.parts_replaced` evidence**

Not by raw evidence-line count.

This prevents a verbose Traveler or many OCR variants from dominating the ranking.

`part_references` are reported as a secondary richness metric only. A reference does
not prove a replacement occurred.

## Important incomplete-corpus safeguard

v1.6.0 is resumable. The planner reports:

- frozen event rows
- events with populated `facts`
- parts-bearing processed events

If the corpus is not fully processed, the summary explicitly says the ranking is for
the **currently processed v1.6.0 prefix/subset**, not the entire DRL corpus.

That lets us make useful 80/20 decisions without pretending partial coverage is full.

## Outputs

- `family_volume_v1_7_0.jsonl`
- `scale_out_targets_80_20_v1_7_0.jsonl`
- `validation_sample_v1_7_0.jsonl`
- `family_label_ambiguities_v1_7_0.jsonl`
- `parts_scale_out_manifest_v1_7_0.json`
- `parts_scale_out_summary_v1_7_0.txt`

## Recommended first run

```bash
cd /opt/nova-drl

python3 test_nova_parts_scale_out_planner_v1_7_0.py

python3 nova_parts_scale_out_planner_v1_7_0.py --plan-only
```

The default input is:

```text
/opt/nova-drl/output/drl_global_lossless_corpus_v1_6_0/repair_events_lossless_v1_6_0.jsonl
```

RCL1A is excluded from the **validation sample** by default because it is already
our benchmark, but it remains in the volume ranking and 80% target calculation.

## What happens after the plan

Do not build family-specific canonical rules for every line.

Instead:
1. inspect how many families cover ~80% of Parts-bearing repairs;
2. take the generated 10-family diversity sample;
3. run conservative Parts candidate/resolver logic across those families;
4. measure generalization;
5. add a global rule only if it fixes a recurring pattern across families.

RCL1A-specific fuse rules stay RCL1A-specific.
