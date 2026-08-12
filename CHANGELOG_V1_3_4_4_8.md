# Changelog v1.3.4.4.8

- Simplified Traveler repair-region logic around technician handwriting.
- Removed all dependence on internal Repaired/Replaced/Description/Initials/Date semantics.
- Complete printed outer Repairs/Replacements box is captured first.
- Repaired/Replaced marks and start marks are not required.
- Added optional whole-box MiniCPM-V literal handwriting transcription.
- Raw machine response is preserved separately from parsed lines.
- Machine output is never automatically accepted as a repair fact.
- No Qdrant writes; no DRL source modification.
