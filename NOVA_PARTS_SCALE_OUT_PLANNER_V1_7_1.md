# Nova DRL Clean Parts Scale-Out Planner v1.7.1

v1.7.0 proved the full lossless corpus is complete, but it also showed why that layer
is too high-recall to rank actual replacement work: 13,152 of 13,166 repair events
contained `facts.parts_replaced` candidates.

v1.7.1 therefore uses the cleaner replacement-mention corpus that already feeds the
unified knowledge index:

- `/opt/nova-drl/output/drl_10pct_tracking_enrichment_v1_4_7/repair_events_enriched_v1_4_7.jsonl`
- `/opt/nova-drl/output/drl_10pct_tracking_enrichment_v1_4_7/replacement_mentions_enriched_v1_4_7.jsonl`

This is a clean 10% development/validation corpus. It is not presented as the full
13,166-event Parts corpus.

## 80/20 interpretation

The script still prints 10/20/30/50/80% coverage checkpoints, but **80% is descriptive,
not a mandate**. If the distribution is long-tailed, Nova should not build custom logic
for hundreds of families.

The practical development focus defaults to:
- families with at least 10 clean replacement repair events;
- maximum 40 families.

The actual generalization sample is only 10 families:
- up to 5 high-volume families with 25+ repairs;
- 3 mid-volume families with 10–24 repairs;
- 2 lower-recurring families with 5–9 repairs;
- RCL1A excluded because it is already the benchmark.

No 1- or 2-event families consume validation effort.

## Run

```bash
cd /opt/nova-drl

python3 test_nova_parts_scale_out_planner_v1_7_1.py
python3 nova_parts_scale_out_planner_v1_7_1.py --plan-only
```

After reviewing that output, the next build should operate only on the generated
10-family generalization cohort—not on hundreds of long-tail families.
