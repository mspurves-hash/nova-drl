# Git steps — v1.5.11

On the Ubuntu NOVA server after the Windows/GitHub Desktop push:

```bash
git pull
```

Run the current invariant and regression gates:

```bash
python3 tests/test_drl_80_20_project_invariant.py
python3 tests/test_global_resolver_consolidation_v1_5_11.py
python3 tests/test_global_evidence_rollup_v1_5_11.py
python3 tests/test_unified_drl_knowledge_index_v1_5_11.py
python3 tests/test_windows_stable_launcher_v1_5_11.py
```

Then test the real product:

```bash
nova-drl PRE-200
```

Expected structural behavior: base product PRE-200, 62 resolved repair events, a Parts list containing recurring PN **and/or component names**, and a Recurring Repair Actions section when structured technician history contains recurring work-performed evidence.

No re-ingestion, index rebuild, or Windows-client reinstall is required.
