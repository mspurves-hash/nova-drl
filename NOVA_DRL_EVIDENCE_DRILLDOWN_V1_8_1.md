# Nova DRL Evidence Drill-Down v1.8.1

v1.8.0 correctly found the family-scoped support event IDs for Parts, actions,
and failures. Live testing exposed one presentation gap: the `REPLACE BEARING`
drill-down found the correct 8 XU-RCM7231 repairs, but did not show the actual
action evidence for those events.

v1.8.1 fixes only the evidence renderer. v1.5.16 remains frozen.

## Action evidence priority

For each action support event:

1. `REPLACE ...` checks matching structured `replacement_mentions`.
2. Direct action/object clauses are recovered from:
   - repair_history_text
   - repair_history
   - repair_action_text
   - repair_action
   - all_fact_text
   - test_outcome_text
3. Coordinated wording such as `Replaced bearings and belts` can support both
   `REPLACE BEARING` and `REPLACE BELT`.
4. If the exact clause cannot be reconstructed, object-bearing repair context
   is shown explicitly as `Supporting repair context`.

This changes no counts and no event membership.

## Test

```bash
cd /opt/nova-drl

python3 test_nova_drl_evidence_drilldown_v1_8_1.py

python3 nova_drl_evidence_drilldown_v1_8_1.py \
  --search "XU-RCM7231" \
  --item "REPLACE BEARING"

python3 nova_drl_evidence_drilldown_v1_8_1.py \
  --search "MR-J2S-40A" \
  --item "7800" \
  --limit 3

python3 nova_drl_evidence_drilldown_v1_8_1.py \
  --search "MR-J2S-40A" \
  --item "LOW VOLTAGE" \
  --type failure \
  --limit 3
```

Expected action output now includes one or more of:

- `Structured replacement: ...`
- `Direct repair clause: ...`
- `Replacement clause: ...`
- `Repair clause: ...`
- `Supporting repair context: ...`

No DB writes, no corpus changes, no accepted facts, Qdrant OFF.
