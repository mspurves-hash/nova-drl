# Git deployment - v1.4.12

1. Copy the contents of this FLAT package into the Windows Nova DRL Git working directory, preserving folders.
2. Commit/push with GitHub Desktop.
3. On Ubuntu:

```bash
git pull
python3 tests/test_unified_drl_knowledge_index_v1_4_12.py
```

4. Existing `/usr/local/bin/nova-drl` wrapper may continue to call `/opt/nova-drl/bin/nova-drl`; the tracked launcher points to v1.4.12 after pull.
5. On a Windows engineer workstation, run the new installer from the checked-out package/repo:

```text
powershell.exe -ExecutionPolicy Bypass -File "<repo>\windows\Install-NOVA-DRL-Engineer-Client.ps1"
```

No unified knowledge-index rebuild is required for this presentation/client upgrade.
