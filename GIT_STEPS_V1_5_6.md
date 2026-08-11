# Windows -> GitHub -> Ubuntu

1. Extract this ZIP on Windows.
2. Merge it into the existing Nova DRL Git working directory.
3. Commit in GitHub Desktop:

`Add validated repair event record v1.5.6`

4. Push.

Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_validated_repair_event_record_v1_5_6.py
```
