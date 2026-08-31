# Git steps - v1.5.5

1. Extract the FLAT ZIP into the Windows Nova DRL Git working directory, preserving folders.
2. Commit/push with GitHub Desktop.
3. On Ubuntu:

   git pull

4. Run:

   python3 tests/test_unified_drl_knowledge_index_v1_5_5.py

5. No knowledge DB rebuild is required. Test:

   nova-drl

   NOVA-DRL> MR-J2S-40A

6. For Windows Engineer Client workstations, rerun the v1.5.5 Windows installer package so the client calls the v1.5.5 remote presentation script.
