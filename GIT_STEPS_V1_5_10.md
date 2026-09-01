# Git steps — v1.5.10

Copy the FLAT package into the Windows Git working tree, commit/push with GitHub Desktop, then on Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_drl_80_20_project_invariant.py
python3 tests/test_global_resolver_consolidation_v1_5_10.py
python3 tests/test_unified_drl_knowledge_index_v1_5_10.py
```

Then test:

```bash
nova-drl PRE-200
nova-drl MR-J2S-40A
nova-drl RCL1A
```

The v1.5.8+ Windows client uses `/usr/local/bin/nova-drl`; no workstation reinstall is required.
