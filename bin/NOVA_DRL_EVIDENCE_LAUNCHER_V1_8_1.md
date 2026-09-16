# NOVA DRL Evidence Launcher — v1.8.1

## Frozen status

- Normal summary/search command: `nova-drl` -> frozen v1.5.16
- Evidence drill-down command: `nova-drl-evidence` -> frozen v1.8.1
- Qdrant: OFF
- Accepted facts: 0
- Database/corpus writes: none

## Install

Copy the launcher into the repo as:

```text
bin/nova-drl-evidence
```

Then on Ubuntu:

```bash
cd /opt/nova-drl

chmod +x bin/nova-drl-evidence
git update-index --chmod=+x bin/nova-drl-evidence
```

## Test

```bash
bin/nova-drl-evidence \
  --search "MR-J2S-40A" \
  --item "7800" \
  --limit 3

bin/nova-drl-evidence \
  --search "XU-RCM7231" \
  --item "REPLACE BEARING"

bin/nova-drl-evidence \
  --search "MR-J2S-40A" \
  --item "LOW VOLTAGE" \
  --type failure \
  --limit 3
```

Once installed on PATH, the normal form is:

```bash
nova-drl-evidence --search "MR-J2S-40A" --item "7800"
```

## Architecture

```text
nova-drl
  -> v1.5.16 family-first technician summary

nova-drl-evidence
  -> v1.8.1 family-scoped evidence drill-down
  -> exact supporting repairs
  -> evidence text
  -> original Traveler path
```

The evidence launcher does not change v1.5.16 search semantics.
