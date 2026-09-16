# NOVA DRL Technician Console v1.9.3

## Purpose

Keep DOCX overwrite protection and edit review pointed at the same working file.

When v1.9.2 DOCX generation protects an existing technician report and creates a timestamped `_NEW_...docx`, the console records the exact generated path. `:review` then reviews that exact file during the same session.

For standalone `--review <DRL_PART>`, the console selects the most recently modified matching DOCX in `/mnt/drl-reports`.

## Frozen layers

- Search/report knowledge: v1.5.16
- DOCX generator: v1.9.2
- DOCX reviewer: v1.9.1
- Evidence audit: v1.8.1

No repair knowledge is changed by this console.
