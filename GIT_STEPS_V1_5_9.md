# Git steps — v1.5.9

1. Copy/extract this FLAT package into the Windows Nova DRL Git working directory, preserving folders.
2. Commit/push with GitHub Desktop.
3. On Ubuntu:

```bash
git pull
python3 tests/test_drl_80_20_project_invariant.py
python3 tests/test_unified_drl_knowledge_index_v1_5_9.py
python3 tests/test_windows_stable_launcher_v1_5_9.py
```

Expected PASS for all three.

No knowledge-index rebuild is required.

Test directly:

```bash
nova-drl RCL1A
nova-drl MR-J2S-40A
```

Existing v1.5.8+ Windows Engineer Clients use the stable server launcher and do not require reinstalling for this update.
