# Git steps - v1.4.10

Use the normal Nova DRL workflow: extract/copy this FLAT ZIP into the Windows Git working directory, commit/push with GitHub Desktop, then on Ubuntu:

    cd /opt/nova-drl
    git pull

The recommended engineer command wrapper is the already-created `/usr/local/bin/nova-drl` wrapper:

    #!/bin/sh
    exec /bin/bash /opt/nova-drl/bin/nova-drl "$@"

This avoids changing the executable bit on the Git-tracked `bin/nova-drl` file and prevents future `git pull` permission conflicts.

Test:

    python3 tests/test_unified_drl_knowledge_index_v1_4_10.py

No knowledge-index rebuild is required for this presentation-only update unless source data has changed.

Start:

    nova-drl

Then search and try:

    NOVA-DRL> 1526990
    NOVA-DRL> :pdf
    NOVA-DRL> :print
