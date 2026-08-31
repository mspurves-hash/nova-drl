# Git / Deployment — v1.5.8

1. Copy the FLAT package contents into the Windows Nova DRL Git working directory.
2. Commit/push with GitHub Desktop.
3. On Ubuntu:

   git pull

4. Run:

   python3 tests/test_drl_80_20_project_invariant.py
   python3 tests/test_unified_drl_knowledge_index_v1_5_8.py
   python3 tests/test_windows_stable_launcher_v1_5_8.py

5. No knowledge-index rebuild is required.
6. Test directly:

   nova-drl MR-J2S-40A

7. If updating the Windows Engineer Client once to the new stable launcher, run the included installer. After that, ordinary server presentation-version changes should not require reinstalling the Windows client.
