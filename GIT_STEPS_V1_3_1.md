# Windows -> GitHub -> Ubuntu

1. Extract on Windows.
2. Copy/merge into existing Windows Nova DRL Git folder.
3. Commit in GitHub Desktop: `Add Nova Traveler Reader v1.3.1 form OCR`
4. Push.

Ubuntu:

```bash
cd /opt/nova-drl
git pull
sudo apt install python3-pil
python3 tests/test_traveler_reader_v1_3_1.py
```
