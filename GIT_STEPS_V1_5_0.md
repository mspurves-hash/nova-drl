# Git / Run Steps — v1.5.0

After copying the FLAT package into the Windows Nova-DRL Git working directory, commit/push with GitHub Desktop, then on Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_drl_full_corpus_ingester_v1_5_0.py
python3 analysis/nova_drl_full_corpus_ingester_v1_5_0.py --status
python3 analysis/nova_drl_full_corpus_ingester_v1_5_0.py --plan-only
```

Inspect status/plan before the full run. Then:

```bash
python3 analysis/nova_drl_full_corpus_ingester_v1_5_0.py
```

Interrupted runs are resumable; rerun the same command.

`--refresh-manifest` is intentional and should only be used when the full-corpus top-level folder snapshot should be updated from a newer DRL index.
