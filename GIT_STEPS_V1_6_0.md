# Git / Run Steps — v1.6.0

After copying this FLAT package into the Windows Git working copy and pushing with GitHub Desktop, on Ubuntu:

```bash
git pull
```

Run the invariant test:

```bash
python3 tests/test_drl_global_lossless_corpus_v1_6_0.py
```

Check status:

```bash
python3 analysis/nova_drl_global_lossless_corpus_ingester_v1_6_0.py --status
```

Plan only:

```bash
python3 analysis/nova_drl_global_lossless_corpus_ingester_v1_6_0.py --plan-only
```

Run a 50-event smoke/resume prefix:

```bash
python3 analysis/nova_drl_global_lossless_corpus_ingester_v1_6_0.py --limit-events 50
```

Then resume into the full frozen corpus using the exact same cache/output root:

```bash
python3 analysis/nova_drl_global_lossless_corpus_ingester_v1_6_0.py
```

Do not use `--accept-unproven-baseline-change` unless a controlled benchmark has explicitly approved a model/prompt/role change.
