# Nova DRL Traveler Reader v1.3.4.4.1

## Variable-Height Repair Block Detection

This release fixes an important assumption exposed by the 2013 GB8-MT
travelers: one logical repair action may span several printed table rows.

### Evidence model

A repair block starts at a handwritten mark in the **Repaired** or
**Replaced** columns. The block continues until the next repair-start mark or
the bottom of the repair table.

The initials/date columns are supporting fields. They are no longer used as
the main block-height boundary.

### 130813004 visual interpretation

The 2013 warranty traveler visually contains many handwritten lines, but they
belong to approximately two logical repair blocks:

1. A multi-line Y-FE adjustment entry with an explanatory note.
2. An added-flanges entry near the bottom of the repair table.

This is why `anchors=2` from the earlier detector was informative even though
the v1.3.4.2 row-coverage gate failed.

### Safety

- Read-only against DRL production files.
- Runs only against Traveler Reader derived output.
- `--detect-only` performs no MiniCPM-V calls.
- No action is accepted automatically.
- No Qdrant write is performed.
- Raw block crops, Tesseract text, and MiniCPM-V output are retained.

## Test

```bash
cd /opt/nova-drl
python3 tests/test_traveler_reader_v1_3_4_4.py
```

Expected:

```text
PASS: Nova Traveler Reader v1.3.4.4 tests
```

## First pilot: 130813004

Run detection only first:

```bash
python3 ingest/nova_traveler_reader_v1_3_4_4.py /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH --log=130813004 --expected-entries=2 --detect-only
```

Expected pilot shape:

```text
Detected start marks:          2
Detected repair blocks:        2
130813004 status=ok starts=2 blocks=2
```

Review the block-detection debug JSON:

```bash
cat /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH/130813004/vision_extraction_v1_3_4_4/block_detection_debug.json
```

Open the block crops:

```bash
nautilus /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH/130813004/vision_extraction_v1_3_4_4/blocks
```

Only after the two block crops look correct should MiniCPM-V be run:

```bash
python3 ingest/nova_traveler_reader_v1_3_4_4.py /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH --log=130813004 --expected-entries=2
```

The structured result is written both to:

- `vision_extraction_v1_3_4_4/repair_entries_v1_3_4_4.json`
- the log root as `repair_entries_v1_3_4_4.json` for downstream discovery.

The downstream v1.5.1 Repair Actions Fusion expects this structured JSON.

## v1.3.4.4.1 production-crop correction

The real Traveler Reader v1.3.1 crop for log 130813004 is 2798 x 2162 on
the Nova server. Its printed rows are about 110 pixels high, which exceeded
the v1.3.4.4 fixed 90-pixel row-height ceiling.

The crop also begins inside the left Repaired column, so the physical
table-left border is outside the crop. v1.3.4.4.1 now detects this clipped-left
layout and interprets the first two visible vertical lines as:

1. Repaired/Replaced divider
2. Description-column divider

Detection output is written under `vision_extraction_v1_3_4_4_1`, while the
stable downstream discovery file remains `repair_entries_v1_3_4_4.json`.
