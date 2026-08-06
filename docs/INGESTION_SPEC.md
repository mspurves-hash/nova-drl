# GB8 Pilot Ingestion Specification

The first module is a read-only Folder Observer.

It will:

1. Read one selected GB8 repair folder.
2. Preserve the original folder path and name.
3. Parse likely metadata from the folder name.
4. Inventory every contained file and subfolder.
5. Classify files by likely evidence role.
6. Calculate hashes to identify duplicates.
7. Produce JSON and human-readable reports.
8. Flag uncertain classifications.

## Folder Pattern

`[TYPE] - [MODEL] [OEM] SN [SERIAL] [CUSTOMER] [SITE] [TECHNICIAN]`

## Traveler Filename Pattern

`[LOG NUMBER] Line Card Original.[extension]`

Interpretation:

- leading number = log number
- role = current traveler
- printed title = Direct Repair Laboratories - Testing Traveler
- do not infer equipment type from the words Line Card
