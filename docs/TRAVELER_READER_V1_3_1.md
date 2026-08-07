# Nova DRL Traveler Reader v1.3.1

DRL Traveler Form Mode uses six fixed form regions instead of whole-page OCR.

It crops and preprocesses:
1. Identity / RMA / Customer / Serial / Warranty
2. Packaging Status
3. Detailed Repairs / Replacements
4. Special Notes
5. Final Unit Test Results
6. Shipping / Hours / Final O.K.

Each crop is grayscale, auto-contrasted, enlarged 2x, sharpened, then OCR'd with multiple Tesseract PSM modes. Every pass is preserved; the most readable pass is selected by a simple heuristic.

Install Pillow if needed:

```bash
sudo apt install python3-pil
```

Test:

```bash
cd /opt/nova-drl
python3 tests/test_traveler_reader_v1_3_1.py
```

Live run:

```bash
python3 ingest/nova_traveler_reader_v1_3_1.py "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN 80050477 UTI MTV ERICH"
```

Inspect newest repair:

```bash
less "/opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80050477_UTI_MTV_ERICH/230809002/traveler_regions.txt"
```

Cropped region images are stored under the same log-number folder in `crops/`.
