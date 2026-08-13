# Git / Run Steps — v1.3.6.0

After extracting this flat ZIP into the Windows `Nova-DRL-Starter` working folder, commit/push with GitHub Desktop.

On Ubuntu:

```bash
cd /opt/nova-drl && git pull
```

Run the unit test:

```bash
python3 tests/test_traveler_corpus_sorter_v1_3_6_0.py
```

Run the complete 10-record pilot using the existing v1.3.5.1 corpus:

```bash
python3 analysis/nova_traveler_corpus_sorter_v1_3_6_0.py
```

View the human-readable provisional result:

```bash
cat /opt/nova-drl/output/traveler_corpus_sort_v1_3_6_0/provisional_sort_summary_v1_3_6_0.txt
```

If you want to inspect only the 8B prospecting stage first:

```bash
python3 analysis/nova_traveler_corpus_sorter_v1_3_6_0.py --prospect-only
```

The normal run automatically reuses matching per-log prospect results, unloads the 8B model before the 32B phase, and reuses the 32B result when the candidate-ledger prompt is unchanged.
