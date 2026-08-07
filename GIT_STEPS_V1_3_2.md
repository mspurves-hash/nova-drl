# Windows → GitHub → Ubuntu

1. Extract on Windows.
2. Copy/merge into the existing Windows Nova DRL Git directory.
3. Commit in GitHub Desktop: `Add Nova Traveler Reader v1.3.2 vision transcriber`
4. Push.

On Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_traveler_reader_v1_3_2.py
```
