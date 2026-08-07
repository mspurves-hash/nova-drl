# Nova DRL Surveyor v1

Surveyor v1 is the first read-only DRL knowledge-discovery module.

It borrows the proven safety ideas from the earlier Nova File Organizer v4:
recursive scanning, SHA-256 hashing, conservative classification, and detailed reporting.
Unlike the organizer, Surveyor NEVER moves, copies, renames, or deletes source files.

## First Test

Use a COPY of one GB8 repair folder.

Example folder:

`RBT - GB8-MT GENMARK SN 80050608 UTI MICRON MTV ERICH`

Example traveler:

`100831011 Line Card Original.jpg`

## Install into Nova DRL

Copy these files into the existing repository so the paths become:

- `/opt/nova-drl/ingest/nova_surveyor_v1.py`
- `/opt/nova-drl/config/oems.json`
- `/opt/nova-drl/config/technicians.json`
- `/opt/nova-drl/config/site_codes.json`

## Run

From `/opt/nova-drl`:

```bash
python3 ingest/nova_surveyor_v1.py "/path/to/copied/GB8 repair folder"
```

For a faster first test without hashing:

```bash
python3 ingest/nova_surveyor_v1.py "/path/to/copied/GB8 repair folder" --no-hash
```

## Output

Surveyor creates:

- `nova_survey_summary.txt`
- `nova_survey.json`
- `nova_survey_files.csv`

The reports are written under `./output/<folder-name>/` by default.

## Safety

Surveyor is read-only against the source folder. It only writes reports to the output directory.
