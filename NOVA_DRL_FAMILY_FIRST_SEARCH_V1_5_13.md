# Nova DRL Family-First Search Wrapper v1.5.13

## Why this exists

Testing showed the foundational problem is upstream of Parts:

- `BM23995` resolved to `ENG SERVICES - M P586 VITRIUM TECH`
- `XU-RCM7231` resolved to that same unrelated family
- therefore Parts, failures, and repair actions were cross-contaminated

## Restored hierarchy

1. DRL Part # first
2. Exact equipment family second
3. Only that family's repair events
4. Parts / failures / repair actions only inside that family

The wrapper leaves v1.5.11 aggregation and rendering intact. It replaces only
`resolve_base_product()` at runtime.

## Important guardrails

- exact family lookup is used only for part-number-like queries containing digits;
- `ASYST` is not treated as a Part # merely because it occurs in a family name;
- a query such as `BM23995` may never silently resolve to base part `M`;
- if no exact DRL Part # family exists, a bad tiny fallback is rejected;
- no fuzzy family merge is introduced.

## No data mutation

- SQLite rebuild: NO
- frozen corpus changes: NO
- Parts canonicalization changes: NO
- Qdrant: OFF

## Run

```bash
cd /opt/nova-drl

python3 test_nova_drl_family_first_search_v1_5_13.py

python3 nova_drl_family_first_search_v1_5_13.py --search "BM23995"
python3 nova_drl_family_first_search_v1_5_13.py --search "XU-RCM7231"
python3 nova_drl_family_first_search_v1_5_13.py --search "MR-J2S-40A"
```

Expected equipment families:

- `BRD - BM23995 EXEC CAR ASYST`
- `RBT - XU-RCM7231 YASKAWA`
- `SVO DRV - MR-J2S-40A MITSUBISHI`

Do not change `bin/nova-drl` yet.

If these resolve correctly and produce distinct family-scoped Parts lists, fold
family-first resolution permanently into the unified search tool. Parts validation
remains parked until the family boundary is trustworthy.
