# Git / Ubuntu steps

Merge this ZIP into the Windows `Nova-DRL-Starter` Git working copy, commit, and push with GitHub Desktop. Then on Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_drl_terminology_v1_5_2_5.py
python3 tests/test_parts_replaced_fusion_v1_5_3_2.py
python3 tests/test_terminology_parts_hardening_v1_5_2_5_v1_5_3_2.py
```

Run terminology on the second validation event:

```bash
python3 ingest/nova_drl_terminology_v1_5_2_5.py /opt/nova-drl/output/evidence_fusion_v1_5_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH/events/130130006
```

Run parts extraction:

```bash
python3 ingest/nova_parts_replaced_fusion_v1_5_3_2.py /opt/nova-drl/output/evidence_fusion_v1_5_2_5/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130130006
```

Inspect before approving anything:

```bash
cat /opt/nova-drl/output/evidence_fusion_v1_5_3_2/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130130006/parts_replaced_review.txt
```

Do not approve parts until the candidate output is reviewed. No Qdrant write is enabled.
