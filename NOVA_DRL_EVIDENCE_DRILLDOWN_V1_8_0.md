# Nova DRL Evidence Drill-Down v1.8.0

## Purpose

v1.5.16 remains frozen as the normal technician summary.

v1.8.0 adds a separate, read-only evidence drill-down capability:

DRL Part # -> exact equipment family -> recurring knowledge item ->
supporting repair events -> evidence text -> original Traveler path

It does **not** change the search/index architecture.

## Examples

### Part evidence

```bash
python3 nova_drl_evidence_drilldown_v1_8_0.py \
  --search "MR-J2S-40A" \
  --item "7800"
```

### Repair-action evidence

```bash
python3 nova_drl_evidence_drilldown_v1_8_0.py \
  --search "XU-RCM7231" \
  --item "REPLACE BEARING"
```

### Failure evidence

```bash
python3 nova_drl_evidence_drilldown_v1_8_0.py \
  --search "MR-J2S-40A" \
  --item "LOW VOLTAGE" \
  --type failure
```

The item is exact-matched against the same v1.5.16 displayed knowledge.

## Pagination

Large recurring Parts such as `7800` can have many supporting repairs.

Default:

```text
--limit 12 --offset 0
```

Next page:

```bash
python3 nova_drl_evidence_drilldown_v1_8_0.py \
  --search "MR-J2S-40A" --item "7800" \
  --offset 12 --limit 12
```

Newest DRL logs are shown first.

## Evidence shown

For each support event, v1.8.0 displays as available:

- DRL log number
- exact equipment family
- matching structured replacement evidence for Parts
- technician repair evidence for repair actions
- reported problem/failure
- repair history
- test/outcome
- original Traveler/source path

## Guardrails

- family resolution = frozen v1.5.13 family-first rule
- repair actions = frozen v1.5.15 conservative semantic veto
- Parts = frozen v1.5.16 structured generic-component gate
- SQLite opened read-only
- no accepted facts
- no Qdrant
- no corpus rewrite

## Install/test

```bash
cd /opt/nova-drl

python3 test_nova_drl_evidence_drilldown_v1_8_0.py

python3 nova_drl_evidence_drilldown_v1_8_0.py \
  --search "MR-J2S-40A" --item "7800"

python3 nova_drl_evidence_drilldown_v1_8_0.py \
  --search "XU-RCM7231" --item "REPLACE BEARING"

python3 nova_drl_evidence_drilldown_v1_8_0.py \
  --search "MR-J2S-40A" --item "LOW VOLTAGE" --type failure
```

Do not change `bin/nova-drl` for this first validation. Once the drill-down output is
useful, add a separate `nova-drl-evidence` launcher or integrate an evidence command
into the main launcher without changing v1.5.16 search semantics.
