# Git steps - v1.4.9

Use the normal Nova DRL workflow: extract/copy this FLAT ZIP into the Windows Git working directory, commit/push with GitHub Desktop, then on the Ubuntu Nova server run `git pull` from `/opt/nova-drl`.

The existing `/usr/local/bin/nova-drl` symlink points to `/opt/nova-drl/bin/nova-drl`; because the bin launcher is updated by Git, the symlink normally does not need to be recreated.

If executable permission is lost during transfer, run:

    chmod +x /opt/nova-drl/bin/nova-drl
    chmod +x /opt/nova-drl/tools/nova_drl_unified_knowledge_index_v1_4_9.py

Then test:

    python3 tests/test_unified_drl_knowledge_index_v1_4_9.py

Existing v1.4.8 knowledge DB can be used directly. A rebuild is optional unless source data changed:

    python3 tools/nova_drl_unified_knowledge_index_v1_4_9.py --status
    nova-drl
