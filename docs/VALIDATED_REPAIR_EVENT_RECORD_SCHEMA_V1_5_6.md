# Validated Repair Event Record Schema v1.5.6

## Top level

```text
record_schema_version
record_id
generated_at_utc
repair_identity
knowledge_fields
field_state_counts
field_state_digest
source_digest
source_manifest
consistency_checks
record_human_validation
record_is_human_validated
accepted_as_final_repair_summary
record_level_qdrant_eligible
qdrant_entry_created
policy
```

## Knowledge field state

Every field contains:

```text
field
state
source_layer
source_path
state_basis
```

Approved scalar fields can include:

```text
value
approved_record
```

Approved multi-item fields include:

```text
approved_items
approved_item_count
```

## State semantics

### approved

A human-approved upstream fact exists and its approved object is preserved.

### pending_review

A valid upstream candidate still requires human review. v1.5.6 does not turn
it into `not_established`.

### not_established

The appropriate upstream stage completed without establishing a human-approved
fact and without leaving a candidate pending.

### not_available

The required upstream source/provenance is unavailable or internally
incomplete. It is not treated as `not_established`.

## Record-level approval

A record-level review decision binds to:

```text
record_id
field_state_digest
source_digest
```

If upstream source content changes, the prior record-level approval becomes
stale automatically.

## Non-promotion invariant

```text
approved diagnostic hypothesis != confirmed root cause
```

## Qdrant invariant

```text
record_level_qdrant_eligible = false
qdrant_entry_created = false
```
