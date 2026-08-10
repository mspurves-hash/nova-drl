# Nova DRL Traveler Reader v1.3.3

## Region-Specific Vision Extraction

v1.3.3 divides the large v1.3.1 crops into smaller overlapping bands before
sending them to MiniCPM-V.

### Repairs/Replacements

Four overlapping horizontal bands retain the description, initials, and date
columns. The model is instructed to return one repair line in this form:

`DESCRIPTION | INITIALS | DATE`

### Special Notes

Three overlapping bands separate:

- customer/template requirements
- unit-specific technical notes
- bottom handwritten notes

### Prompt-compliance controls

Responses are flagged when MiniCPM-V adds commentary such as:

- `Title:`
- `Table Columns:`
- `The image shows...`
- `This appears to...`
- `Could refer to...`

A flagged response is preserved but marked as ineligible for automatic fusion.

## Test

```bash
cd /opt/nova-drl
python3 tests/test_traveler_reader_v1_3_3.py
```

Expected:

`PASS: Nova Traveler Reader v1.3.3 tests`

## First live run

Use the local v1.3.1 serial output and one log only:

```bash
python3 ingest/nova_traveler_reader_v1_3_3.py /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80050477_UTI_MTV_ERICH --log=230809002
```

## Inspect the result

```bash
less /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80050477_UTI_MTV_ERICH/230809002/vision_extraction_v1_3_3/vision_extraction_v1_3_3.txt
```

Subcrop images are preserved under:

`vision_extraction_v1_3_3/crops/`

## Safety

- DRL NAS remains read-only.
- No Qdrant ingestion occurs.
- No transcription is silently accepted as a repair fact.
