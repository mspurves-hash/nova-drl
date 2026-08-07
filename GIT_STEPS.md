# Git update steps

After adding these files to the Nova DRL repository:

```bash
cd /opt/nova-drl
python3 tests/test_surveyor_v1.py
git status
git add ingest/nova_surveyor_v1.py config/oems.json config/technicians.json config/site_codes.json docs/SURVEYOR_V1.md tests/test_surveyor_v1.py
git commit -m "Add Nova Surveyor v1 for GB8 repair folders"
git push
```
