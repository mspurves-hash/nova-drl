# Nova DRL Tier-A Global Validation Cache v1.7.5

This is the first actual validation/cache stage.

It does not try to validate all Tier A at once. The seed contains 20 high-value
identities researched outside the runtime. Clear authoritative matches are cached;
suffix/package-sensitive identities are explicitly deferred.

The seed is appendable. Future validation work adds rows to:

`tier_a_validation_seed_v1_7_5.jsonl`

The runtime never performs fuzzy canonicalization.

## First run

Write v1.7.4 outputs for real if needed:

```bash
cd /opt/nova-drl
python3 nova_parts_global_validation_worklist_v1_7_4.py
```

Copy/push/pull the three v1.7.5 files, then:

```bash
python3 test_nova_parts_tier_a_validation_cache_v1_7_5.py
python3 nova_parts_tier_a_validation_cache_v1_7_5.py --plan-only
```

Expected behavior:
- only Tier A is processed;
- the first seed batch validates/cache-classifies roughly 20 identities;
- clear package/suffix-sensitive strings remain deferred;
- all unmatched Tier A remains pending;
- no fuzzy merges, no family rules, no Qdrant.

Once the plan is clean, run without `--plan-only` and reuse the cache globally.
