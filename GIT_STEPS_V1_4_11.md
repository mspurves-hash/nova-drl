# Git / install steps — v1.4.11

Normal Nova DRL code workflow:

1. Extract this FLAT ZIP into the Windows Nova DRL Git working directory.
2. Commit/push with GitHub Desktop.
3. On Ubuntu:

       cd /opt/nova-drl
       git pull

4. Test:

       python3 tests/test_unified_drl_knowledge_index_v1_4_11.py

5. Start:

       nova-drl

No knowledge DB rebuild is required for this presentation-only update.

## Windows desktop shortcut

On each engineer Windows workstation, from the Nova DRL Git working directory run:

    powershell -ExecutionPolicy Bypass -File .\windows\Install-NOVA-DRL-Shortcut.ps1

This creates a `NOVA DRL` Desktop shortcut that SSHes directly to `/usr/local/bin/nova-drl`. No password is stored.
