# Git Steps - Nova DRL v1.5.7

Copy the FLAT package contents into the Windows Git working directory, commit/push with GitHub Desktop, then on Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_unified_drl_knowledge_index_v1_5_7.py
```

Expected:

```text
PASS: Nova DRL Minimal Product Report + Standard Repair Kit View v1.5.7 tests
```

No knowledge-index rebuild is required. Test with:

```bash
nova-drl
```

Search `MR-J2S-40A` and compare the simplified product/parts/failure view.
