# Nova DRL Unified Search v1.5.12 — Validated Parts Presentation Overlay

The first 17 validated global Parts identities already touch 562 repair events
across 175 equipment families. That is enough to integrate the cache rather than
validate more identities.

v1.5.12 is generated from the current v1.5.11 technician search tool.

## What changes

Only the technician-facing Parts label after existing v1.5.11 aggregation.

Examples:
- `Lm324n` -> `LM324N`
- `d45h11` -> `D45H11`
- `HCPL 7840` -> `HCPL-7840`

The original aggregated reference is retained in payload metadata as
`observed_reference`, along with manufacturer/confidence metadata.

## What does NOT change

- `drl_knowledge_index.sqlite`
- repair-event counts
- Parts recurrence counts
- event unions
- v1.5.11 aggregation/clustering logic
- failures
- recurring repair actions
- frozen evidence
- Qdrant

No fuzzy matching is used.

If the validated cache is missing or unreadable, the new tool behaves like
v1.5.11.

## Build safely

```bash
cd /opt/nova-drl

python3 test_nova_build_unified_search_v1_5_12.py
python3 nova_build_unified_search_v1_5_12.py --plan-only
python3 nova_build_unified_search_v1_5_12.py
python3 -m py_compile tools/nova_drl_unified_knowledge_index_v1_5_12.py
```

Test the new tool directly before changing the launcher:

```bash
python3 tools/nova_drl_unified_knowledge_index_v1_5_12.py --search "BM23995"
python3 tools/nova_drl_unified_knowledge_index_v1_5_12.py --search "XU-RCM7231"
```

Compare if useful:

```bash
python3 tools/nova_drl_unified_knowledge_index_v1_5_11.py --search "BM23995"
```

Do not rebuild the SQLite knowledge index.

Only after v1.5.12 direct searches look good should `bin/nova-drl` move from
v1.5.11 to v1.5.12.
