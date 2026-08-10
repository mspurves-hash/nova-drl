# Repair Evidence Bundle Schema v1.4.1

## Original-file states

Every original source file belongs to exactly one accounting state:

1. Meaningful repair-event evidence
2. Meaningful serial/unit-level evidence
3. System metadata
4. Explicitly unresolved

## System metadata

System metadata retains:

- exact source path;
- relative path;
- inherited assignment scope;
- inherited log number when present in a log-prefixed parent directory;
- file size and modification time;
- optional hash when requested.

It is assigned:

```json
{
  "role": "system_metadata",
  "authority": "excluded_system_metadata",
  "extraction": {
    "status": "excluded_system_metadata"
  }
}
```

System metadata never contributes to evidence completeness or repair conclusions.
