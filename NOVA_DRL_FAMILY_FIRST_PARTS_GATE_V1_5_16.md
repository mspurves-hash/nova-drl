# Nova DRL v1.5.16 — Family-First + Semantic Veto + Structured Component Parts Gate

## Finding

MR-J2S-40A `MOTOR 4` came from four events:

- two events had **no structured parts_replaced evidence** and were recovered from
  repair/test narrative;
- two events had structured generic `Motor` evidence, but both were weak/suspect
  and tied directly to motor test context.

The source corpus remains preserved. The normal technician-facing Parts list
needs a more conservative 80/20 contract for generic component names.

## v1.5.16 Parts policy

Explicit part-number and capacitance identities:
- existing v1.5.11 recurrence behavior stays unchanged.

Generic component names such as:
- MOTOR
- BELT
- BEARING
- RELAY
- CONNECTOR
- SCREW

must:
1. be supported by rows in `replacement_mentions`; and
2. recur in at least **3 independent repair events** to appear in the normal
   Parts list.

This is a display/presentation gate only. The raw evidence is not removed.

## Why 3?

Two-event generic no-PN evidence is too easy to create from OCR/extraction noise
and is low-value in normal recurring output. The threshold does not affect
explicit manufacturer PNs.

This follows the project 80/20 rule:
- preserve all evidence;
- surface the repeated/high-value generic components;
- do not spend development effort perfecting every singleton/two-off.

## Stack

v1.5.16 uses:
1. v1.5.13 family-first resolution
2. v1.5.15 conservative action semantic veto
3. v1.5.16 structured generic-component Parts gate

No DB rebuild. Qdrant OFF.

## Run

```bash
cd /opt/nova-drl

python3 test_nova_drl_family_first_parts_gate_v1_5_16.py

python3 nova_drl_family_first_parts_gate_v1_5_16.py --search "MR-J2S-40A"
python3 nova_drl_family_first_parts_gate_v1_5_16.py --search "XU-RCM7231"
python3 nova_drl_family_first_parts_gate_v1_5_16.py --search "BM23995"
```

Expected:
- MR-J2S-40A: `MOTOR` disappears from Parts.
- MR-J2S-40A explicit recurring PNs/cap values remain.
- XU-RCM7231 high-recurrence BELT/BEARING/SEAL/etc. remain.
- explicit PNs remain under their existing recurrence threshold.
- low-value two-event generic component labels may disappear by design.

Do not update the launcher until these three outputs are reviewed.
