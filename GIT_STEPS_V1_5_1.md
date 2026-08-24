# Git steps — v1.5.1

Copy the FLAT package contents into the Nova DRL Git working tree, commit/push from Windows, then on Ubuntu:

```bash
git pull
python3 tests/test_drl_full_corpus_ingester_v1_5_1.py
python3 analysis/nova_drl_full_corpus_ingester_v1_5_1.py --status
python3 analysis/nova_drl_full_corpus_ingester_v1_5_1.py --plan-only
```

Do not start the full model run until status/plan are reviewed.
