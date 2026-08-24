# Git steps — v1.5.2

Copy the flat package contents into the Windows Nova-DRL Git working directory, commit/push with GitHub Desktop, then on Ubuntu:

```bash
git pull
python3 tests/test_drl_full_corpus_ingester_v1_5_2.py
python3 analysis/nova_drl_full_corpus_ingester_v1_5_2.py --status
python3 analysis/nova_drl_full_corpus_ingester_v1_5_2.py --plan-only
```

The default v1.5.2 output root is:
`/opt/nova-drl/output/drl_full_corpus_v1_5_2`

The frozen corpus membership remains the v1.5.1 manifest.
