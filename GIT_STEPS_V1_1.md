# Nova Surveyor v1.1 Git Steps

After placing the v1.1 files into `/opt/nova-drl`:

```bash
cd /opt/nova-drl
python3 tests/test_surveyor_v1_1.py

git status
git add ingest/nova_surveyor_v1_1.py \
        config/oems.json \
        config/technicians.json \
        config/site_codes.json \
        docs/SURVEYOR_V1_1.md \
        tests/test_surveyor_v1_1.py

git commit -m "Add Nova Surveyor v1.1 traveler discovery"
git push
```

Then perform the first read-only GB8 discovery:

```bash
python3 ingest/nova_surveyor_v1_1.py \
  "/mnt/drl/000 folder for tech scans" \
  --discover --type RBT --oem GENMARK --model GB8 --limit 20
```
