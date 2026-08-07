# Nova DRL Surveyor v1.1

## Purpose

v1.1 narrows the first Nova DRL pilot to the organized Traveler Database:

`/mnt/drl/000 folder for tech scans`

The Operations Check List is intentionally deferred until the Traveler workflow is proven.

## New v1.1 Workflow

### 1. Discover GB8 repair folders

From `/opt/nova-drl`:

```bash
python3 ingest/nova_surveyor_v1_1.py \
  "/mnt/drl/000 folder for tech scans" \
  --discover --type RBT --oem GENMARK --model GB8
```

This reads immediate folder names only. It does not traverse every repair folder.

For a short first look:

```bash
python3 ingest/nova_surveyor_v1_1.py \
  "/mnt/drl/000 folder for tech scans" \
  --discover --type RBT --oem GENMARK --model GB8 --limit 20
```

### 2. Survey one real repair folder

Use one exact path returned by Discovery:

```bash
python3 ingest/nova_surveyor_v1_1.py \
  "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN 80050608 UTI MICRON MTV ERICH"
```

Hashing is OFF by default because photos over the NAS can make hashing slow.

To calculate SHA-256 hashes:

```bash
python3 ingest/nova_surveyor_v1_1.py \
  "/mnt/drl/000 folder for tech scans/<repair folder>" --hash
```

## Reports

Discovery creates:

- `nova_domain_discovery.txt`
- `nova_domain_discovery.json`
- `nova_domain_discovery.csv`

Single-repair survey creates:

- `nova_survey_summary.txt`
- `nova_survey.json`
- `nova_survey_files.csv`

## Safety

Surveyor never moves, renames, edits, or deletes DRL source files.

The NAS should remain mounted read-only during the pilot.
