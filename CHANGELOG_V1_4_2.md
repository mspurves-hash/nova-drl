# Changelog — v1.4.2

## Nova DRL File Index

- Added one persistent SQLite file index for the complete `/mnt/drl` share.
- Added Everything-style case-insensitive AND token searching across the full indexed path.
- Added explicit validation that `RCL1A LINE` may match `RCL1A` in a parent folder and `LINE` in a filename.
- Added initial full metadata crawl.
- Added safe refresh: only new/changed rows are rewritten; vanished rows are removed only after an error-free complete scan.
- Added DRL `YYMMDD###` log-number detection from path context.
- Added extension and file-kind metadata and filters.
- Added JSON search output for downstream Nova pipelines.
- Deliberately excluded file-content reading and whole-file hashing from the index layer.
- Added optional systemd refresh examples; not enabled automatically.
- RCL1A v1.4.1 remains frozen; v1.4.2 establishes the discovery foundation before RCL1A development resumes.
