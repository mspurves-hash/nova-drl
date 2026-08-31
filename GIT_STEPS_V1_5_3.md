# Git / Deployment Steps v1.5.3

1. Copy the package contents into the Windows Nova DRL Git working directory.
2. Commit/push with GitHub Desktop.
3. On Ubuntu: `cd /opt/nova-drl && git pull`
4. Test: `python3 tests/test_unified_drl_knowledge_index_v1_5_3.py`
5. Status: `python3 tools/nova_drl_unified_knowledge_index_v1_5_3.py --status`
6. Plan: `python3 tools/nova_drl_unified_knowledge_index_v1_5_3.py --plan-only`
7. Build only after the plan is reviewed: `python3 tools/nova_drl_unified_knowledge_index_v1_5_3.py --build`
8. Self-check: `python3 tools/nova_drl_unified_knowledge_index_v1_5_3.py --self-check`
9. Test `nova-drl` searches.
10. Re-run the Windows Engineer Client installer to update the local client from remote tool v1.4.12 to v1.5.3. Existing SSH key is reused.
