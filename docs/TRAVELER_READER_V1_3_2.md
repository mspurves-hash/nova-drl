# Nova DRL Traveler Reader v1.3.2

## Vision Transcriber

v1.3.2 sends selected v1.3.1 region crops to the local MiniCPM-V model and saves Tesseract and vision transcription side-by-side.

It does **not** perform final evidence fusion and does **not** write to Qdrant.

Default regions:

- `repairs_replacements`
- `special_notes`

## Test

```bash
cd /opt/nova-drl
python3 tests/test_traveler_reader_v1_3_2.py
```

Expected:

```text
PASS: Nova Traveler Reader v1.3.2 tests
```

## Recommended first live test — one log

```bash
python3 ingest/nova_traveler_reader_v1_3_2.py \
"/opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80050477_UTI_MTV_ERICH" \
--log 230809002
```

## Then process all six travelers

```bash
python3 ingest/nova_traveler_reader_v1_3_2.py \
"/opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80050477_UTI_MTV_ERICH"
```

## Inspect the newest log

```bash
less "/opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80050477_UTI_MTV_ERICH/230809002/vision_transcription_v1_3_2.txt"
```

Every output preserves the crop path, strict prompt, selected Tesseract text, raw MiniCPM-V response, model name, and Ollama metadata.
