# NOVA DRL Partial Part # Safety Fix

## Server v1.5.18
Hierarchy remains DRL Part # first.

Resolution order:
1. exact DRL Part # token;
2. unique Part # prefix only;
3. otherwise no product repair-knowledge resolution.

Example:
- `SVO DRV - ELA-B014CFT-03` -> exact.
- `SVO DRV - ELA-B014` -> resolves only if one full PN token starts with ELA-B014.
- if ELA-B014 matched two different full PNs, NOVA does not guess or combine them.
- `CNTL - Genmark` never falls back to the old `M`/MR-J2S-like global resolver.

No DB/corpus/Qdrant changes.

## DOCX v1.9.3
- valid exact families with zero known Parts can still generate a DOCX;
- unresolved searches fail cleanly with an error code, not a traceback.

## Windows v1.9.5
- detects whether search resolved an EQUIPMENT / PRODUCT;
- refuses `:docx` on unresolved/ambiguous searches;
- catches DOCX/SSH errors and keeps the client open.

## Ubuntu installation
Repo root:
- nova_drl_hard_part_first_unique_prefix_v1_5_18.py
- test_nova_drl_hard_part_first_unique_prefix_v1_5_18.py
- nova_drl_docx_report_v1_9_3.py
- test_nova_drl_docx_report_v1_9_3.py

Repo bin:
- `nova-drl-v1_5_18` -> rename to `nova-drl`
- `nova-drl-docx-v1_9_3` -> rename to `nova-drl-docx`

Then:
```bash
cd /opt/nova-drl
git pull
chmod +x bin/nova-drl bin/nova-drl-docx
python3 test_nova_drl_hard_part_first_unique_prefix_v1_5_18.py
python3 test_nova_drl_docx_report_v1_9_3.py
```

Live regression:
```bash
bin/nova-drl --search "SVO DRV - ELA-B014CFT-03"
bin/nova-drl --search "SVO DRV - ELA-B014"
bin/nova-drl --search "CNTL - Genmark"
```

## Shared Windows client
Publish `NOVA-DRL-Windows-Tech-v1.9.5.ps1` to the stable shared filename:
`Z:\NOVA_CLIENT\NOVA-DRL-Windows-Tech.ps1`

After that every workstation gets the fix on next launch.
