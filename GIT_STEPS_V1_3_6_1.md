# Git / Run Steps — v1.3.6.1

After extracting this flat ZIP into the Windows `Nova-DRL-Starter` working folder, commit and push with GitHub Desktop.

On Ubuntu, pull the update:

```bash
cd /opt/nova-drl && git pull
```

Run the deterministic unit test:

```bash
python3 tests/test_traveler_corpus_sorter_v1_3_6_1.py
```

Run the complete existing 10-Traveler pilot:

```bash
python3 analysis/nova_traveler_corpus_sorter_v1_3_6_1.py
```

View the human-readable result:

```bash
cat /opt/nova-drl/output/traveler_corpus_sort_v1_3_6_1/provisional_sort_summary_v1_3_6_1.txt
```

Quickly confirm the difficult pilot terms survived:

```bash
grep -RniE "Turkey fat|Blue Schmoo|Sugar Cube|Y-FE|R8ZZ|BR6ZZ|R6ZZ|BERS|Flanged" /opt/nova-drl/output/traveler_corpus_sort_v1_3_6_1
```

Inspect the OCR recheck queue:

```bash
python3 -m json.tool /opt/nova-drl/output/traveler_corpus_sort_v1_3_6_1/ocr_recheck_queue_v1_3_6_1.json | less
```

Inspect what sanitation removed from one repair, for example log `130813004`:

```bash
find /opt/nova-drl/output/traveler_corpus_sort_v1_3_6_1/records/130813004 -name sanitation_audit.json -print -exec python3 -m json.tool {} \;
```

The normal run uses the same `qwen3-vl-drl:8b-q8-16k` prospector and `qwen25-drl:32b-16k` reasoning model. It still creates zero approved facts and zero Qdrant entries.
