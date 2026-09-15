# Nova DRL Validated Parts Cache Impact Audit v1.7.6

This is an 80/20 measurement stage, not another cleanup stage.

Before validating more Tier-A identities, measure what the first validated cache
already covers across the full v1.5.2 Parts corpus.

## Required first step

Write v1.7.5 for real:

```bash
cd /opt/nova-drl
python3 nova_parts_tier_a_validation_cache_v1_7_5.py
```

Then run:

```bash
python3 test_nova_parts_validated_cache_impact_v1_7_6.py
python3 nova_parts_validated_cache_impact_v1_7_6.py
```

The audit is read-only and writes no output files.

Key result:
- distinct repair events touched by validated identities
- percentage of all replacement repairs
- percentage of recurring-output repair events
- equipment families benefiting
- top family coverage

If the first 17 validated identities already deliver useful coverage, stop
validating for now and integrate/reuse the cache. Do not chase 100% Tier-A completion.
