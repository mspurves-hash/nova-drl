# Nova DRL v1.4.13 - Git / Deployment Steps

After copying these files into the Windows Git working directory and pushing with GitHub Desktop, on the Ubuntu Nova server run:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_unified_drl_knowledge_index_v1_4_13.py
```

Expected:

```text
PASS: Nova DRL Windows Engineer Client + Auto-Open Reports v1.4.13 tests
```

No unified knowledge-index rebuild is required. `bin/nova-drl` now points to the v1.4.13 presentation layer.

On Windows, rerun the v1.4.13 Engineer Client installer to update the local client used by the desktop shortcut. Existing SSH keys are reused.
