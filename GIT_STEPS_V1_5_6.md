# Git steps - v1.5.6

Windows workflow:
1. Extract the FLAT package over the local Nova-DRL Git working folder.
2. Commit/push with GitHub Desktop.
3. On Ubuntu:

```bash
git pull
python3 tests/test_unified_drl_knowledge_index_v1_5_6.py
```

No knowledge database rebuild is required.

Test locally on Ubuntu:

```bash
nova-drl
```

Then search a known product such as:

```text
MR-J2S-40A
```

For Windows Engineer Client deployment, extract the v1.5.6 Windows ZIP and rerun `Install-NOVA-DRL-Engineer-Client.ps1`; the existing SSH key is reused.
