# Nova DRL Traveler Reader v1.3

## Purpose

Surveyor v1.2.1 proved the structural layer:

`Model -> Serial Number -> Repair Event -> Evidence`

v1.3 begins reading the actual traveler contents.

## First-pass architecture

1. Find `######### Line Card Original/Warranty` traveler files.
2. Preserve exact source path and log number.
3. Extract text locally.
4. Save raw extracted text before any interpretation.
5. Optionally send the extracted text to local Ollama for structured repair fields.
6. Never modify the DRL source archive.

## Extraction dependencies

### Image travelers

v1.3 uses the local `tesseract` command if installed.

Check:

```bash
which tesseract
```

If missing:

```bash
sudo apt install tesseract-ocr
```

### PDF travelers

Preferred extractor:

```bash
sudo apt install poppler-utils
```

This provides `pdftotext`.

## First live test — extraction only

Use the known GB8 folder:

```bash
cd /opt/nova-drl

python3 ingest/nova_traveler_reader_v1_3.py "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN 80050477 UTI MTV ERICH"
```

Do NOT use `--ollama` on the first run.

This lets us inspect OCR quality before asking the LLM to interpret anything.

## Second test — local AI structuring

After raw extraction looks useful:

```bash
python3 ingest/nova_traveler_reader_v1_3.py "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN 80050477 UTI MTV ERICH" --ollama
```

Default local model:

`qwen2.5:32b`

## Structured fields

- customer_complaint
- incoming_condition
- technician_findings
- diagnosis
- root_cause
- repair_actions
- parts_replaced
- testing_performed
- final_result
- technician_notes

Missing information remains `null`.

## Output

Per log number:

- `traveler_raw.txt`
- `traveler_reader.json`

Serial-level:

- `traveler_reader_summary.txt`
- `traveler_reader_summary.json`

## Safety

The NAS remains read-only. v1.3 writes only under `/opt/nova-drl/output`.
