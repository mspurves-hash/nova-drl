#!/usr/bin/env python3
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
launcher = (ROOT/'bin'/'nova-drl').read_text(encoding='utf-8')
client = (ROOT/'windows'/'NOVA-DRL-Engineer.ps1').read_text(encoding='utf-8')
assert 'nova_drl_unified_knowledge_index_v1_5_8.py' in launcher
assert 'elif [[ "$1" == --* ]]' in launcher
assert 'exec python3 "$SCRIPT" "$@"' in launcher
assert '$remoteTool = "/usr/local/bin/nova-drl"' in client
assert '/opt/nova-drl/tools/nova_drl_unified_knowledge_index_v1_5_' not in client
print('PASS: Nova DRL v1.5.8 Windows client uses stable server launcher')
