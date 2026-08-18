# Nova DRL RCL1A Indexed Focused Recovery v1.4.3

## Purpose
v1.4.3 reconnects the RCL1A power-supply analysis pipeline to the real DRL share through the persistent Nova DRL File Index v1.4.2.

The production acquisition path is now:

`/mnt/drl -> persistent SQLite file index -> Everything-style query "RCL1A LINE" -> source selector -> actual Line Card documents -> focused vision -> repair-event extraction -> provisional part normalization -> Python counts`

The RCL1A runtime does **not** recursively walk `/mnt/drl` to discover files. Discovery is performed from `/opt/nova-drl/index/drl_file_index.sqlite`, then only selected source files are opened from the read-only share.

## Source selector
The raw index query intentionally behaves broadly like Everything. v1.4.3 then applies a production source selector:

- filename must contain `Line Card` (case-insensitive),
- supported native image types are accepted,
- individual `Line Card ... .pdf` documents are accepted and rendered page-by-page at 300 DPI,
- `.picasaoriginals` backup paths are excluded,
- a manually combined `All Line Cards.pdf` benchmark file is excluded,
- unrelated documents that matched only because `LINE` occurred elsewhere in the filename are excluded,
- selected index rows are verified to still exist on the mounted share before use.

The exact selection is written to `source_selection_v1_4_3.json` **before model analysis begins**.

## Repair-event identity
The index is a file index, not a repair-event database. v1.4.3 keeps those concepts separate.

- multiple legitimate Line Card source files with the same detected 9-digit DRL log remain preserved as evidence,
- they count as one repair event for frequency calculations,
- duplicate scans are handled separately by exact/perceptual/text evidence,
- a filename that does not contain a valid 9-digit log is not silently corrected.

## Analysis behavior
The focused evidence, duplicate, extraction, provisional normalization, and Python counting behavior is carried forward from v1.4.1. The prior hosted benchmark is never read by the runtime. Accepted facts remain 0 and Qdrant remains off.

## Primary commands
Status against the live index:

```bash
python3 analysis/nova_rcl1a_indexed_focused_recovery_v1_4_3.py --status
```

Plan and freeze the discovery boundary before model work:

```bash
python3 analysis/nova_rcl1a_indexed_focused_recovery_v1_4_3.py --plan-only
```

Full run after status/plan validation:

```bash
python3 analysis/nova_rcl1a_indexed_focused_recovery_v1_4_3.py
```

The default production query is `RCL1A LINE`. An alternate query may be supplied explicitly with `--index-query`.

## Compatibility adapters
`--source-images-root` remains as a legacy/manual fallback. `--source-pdf` remains only as the benchmark adapter. Neither is the preferred production discovery path.
