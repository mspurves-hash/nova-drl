# Git / validation steps v1.3.4.4.4

After extracting this ZIP into the repository root, commit and push from the Windows Git working copy.

On Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_traveler_reader_v1_3_4_4_4.py
```

First third-event run (no expected-entry hint):

```bash
python3 ingest/nova_traveler_reader_v1_3_4_4_4.py /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH --log=150622005 --detect-only
```

Review output only. Do not feed v1.3.4.4.4 into downstream fusion until the recovered crop and content rows have been human-reviewed.
