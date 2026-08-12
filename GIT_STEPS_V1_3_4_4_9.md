# Git / Run Steps v1.3.4.4.9

```bash
cd /opt/nova-drl && git pull
python3 tests/test_traveler_reader_v1_3_4_4_9.py
python3 ingest/nova_traveler_reader_v1_3_4_4_9.py /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH --log=150622005 --detect-only
```
