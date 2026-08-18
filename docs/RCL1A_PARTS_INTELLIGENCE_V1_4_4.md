# Nova DRL RCL1A 80/20 Parts Intelligence v1.4.4

## Purpose
v1.4.4 is a post-processing intelligence layer over the frozen v1.4.3 RCL1A replacement evidence. It is intentionally **not another OCR pass**.

The production chain is now:

`DRL index -> v1.4.3 real Line Card evidence -> 457-ish replacement mentions -> v1.4.4 80/20 parts intelligence`

v1.4.4 answers two different shop questions without forcing them into the same representation:

1. **What commonly fails / gets replaced?**  A functional-family view such as MOSFETs, 15 A / 600 V fuses, rectifiers, smart-board components, etc.
2. **What exact parts are we actually using?**  A separate likely-PN ranking that preserves all observed handwritten/OCR variants underneath each provisional best-guess PN.

## Why two layers matter
A useful repair family does not have to be one catalog part number.

- Different exact MOSFET PNs can correctly live under a broader `MOSFET Family`, while the individual PNs remain separately ranked.
- Multiple fuse PNs may be intentional cost/availability substitutes when their electrical/mechanical specification is equivalent. v1.4.4 may therefore aggregate them into one functional fuse family while still showing which individual PN was used most often.

This follows Nova DRL's 80/20 rule: use recurrence and volume to produce useful technician/purchasing intelligence rather than chasing perfect OCR on every handwritten line.

## Inputs
Default source root:

```text
/opt/nova-drl/output/rcl1a_indexed_focused_recovery_v1_4_3
```

Required:

```text
replacement_mentions_v1_4_3.jsonl
```

Optional but normally present and used only as **soft grouping hints**:

```text
part_family_map_v1_4_3.json
part_frequency_v1_4_3.json
rcl1a_indexed_focused_manifest_v1_4_3.json
```

The prior hosted benchmark reports are never read by the v1.4.4 runtime.

## Processing
### 1. PN observation rollup
Python gathers every non-empty v1.4.3 `part_number` string and records its repair-event frequency, explicit pieces, descriptions, examples and prior provisional-family hints.

### 2. Provisional PN consolidation
Python creates candidate blocks using lexical similarity and v1.4.3 family membership as a **candidate-only hint**. The 14B reason model separates or combines the raw strings into likely actual PN groups.

A PN label may normalize spaces/slashes/hyphens and recurrence-supported OCR variation, but it must remain strongly grounded in supplied observed strings. An ungrounded invented PN is rejected by Python and replaced by a supported observed form.

The exact raw PN variants remain stored under every group.

### 3. Replacement signals
Each replacement mention becomes exactly one signal:

- a PN-group signal when a part number was present, or
- a description-only signal when no PN was stated.

### 4. Functional-family consolidation
14B groups those signals into technician-useful families. A second compact merge pass consolidates batch boundaries.

This layer is intentionally broader than exact PN identity.

### 5. Python counts
Python alone calculates:

- distinct repairs containing a functional family,
- distinct repairs containing a likely PN,
- explicit recorded pieces,
- mentions where quantity was not stated.

No unstated quantity is converted into a number.

## Outputs
Default output root:

```text
/opt/nova-drl/output/rcl1a_parts_intelligence_v1_4_4
```

Human-readable summary:

```text
rcl1a_parts_intelligence_summary_v1_4_4.txt
```

Functional family ranking:

```text
functional_family_frequency_v1_4_4.csv
functional_family_frequency_v1_4_4.json
```

Likely actual PN ranking:

```text
part_number_usage_v1_4_4.csv
part_number_usage_v1_4_4.json
```

Evidence/provenance maps:

```text
pn_group_map_v1_4_4.json
functional_family_map_v1_4_4.json
rcl1a_parts_intelligence_v1_4_4.json
rcl1a_parts_intelligence_manifest_v1_4_4.json
```

## Commands
Test:

```bash
python3 tests/test_rcl1a_parts_intelligence_v1_4_4.py
```

Status:

```bash
python3 analysis/nova_rcl1a_parts_intelligence_v1_4_4.py --status
```

Plan only:

```bash
python3 analysis/nova_rcl1a_parts_intelligence_v1_4_4.py --plan-only
```

Full run:

```bash
python3 analysis/nova_rcl1a_parts_intelligence_v1_4_4.py
```

Re-run model grouping while ignoring v1.4.4 caches:

```bash
python3 analysis/nova_rcl1a_parts_intelligence_v1_4_4.py --force
```

## Safety / authority boundary
- v1.4.3 evidence is not modified.
- Original Line Card images are not opened by v1.4.4.
- `/mnt/drl` is not scanned or written.
- Accepted facts remain 0.
- Qdrant remains off.
- All family and likely-PN labels remain provisional until a human chooses to approve something later.
