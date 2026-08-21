# Git / Install Steps — v1.4.8

Use the normal DRL Nova workflow: copy the package contents into the Windows Git working directory, commit/push with GitHub Desktop, then on Ubuntu:

```text
cd /opt/nova-drl
git pull
```

Run the tests:

```text
python3 tests/test_unified_drl_knowledge_index_v1_4_8.py
```

Inspect status and plan:

```text
python3 tools/nova_drl_unified_knowledge_index_v1_4_8.py --status
python3 tools/nova_drl_unified_knowledge_index_v1_4_8.py --plan-only
```

Build the unified local knowledge DB:

```text
python3 tools/nova_drl_unified_knowledge_index_v1_4_8.py --build
```

Measure local search speed:

```text
python3 tools/nova_drl_unified_knowledge_index_v1_4_8.py --self-check
```

Test one-shot searches before installing the short command:

```text
python3 tools/nova_drl_unified_knowledge_index_v1_4_8.py --search "RCL1A"
python3 tools/nova_drl_unified_knowledge_index_v1_4_8.py --search "DGK52102"
```

Install the engineer-facing command once:

```text
sudo ln -sf /opt/nova-drl/bin/nova-drl /usr/local/bin/nova-drl
```

Then:

```text
nova-drl
```

or:

```text
nova-drl 1526990
```

After a future v1.4.2 daily file-index refresh or after new AI-ingested knowledge is added, refresh v1.4.8 locally with:

```text
python3 tools/nova_drl_unified_knowledge_index_v1_4_8.py --refresh
```
