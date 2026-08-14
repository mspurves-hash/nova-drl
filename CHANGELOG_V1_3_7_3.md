# CHANGELOG — Nova DRL v1.3.7.3

## Technician Signal Cleaner

- Added Python-only technician/reference routing.
- Technician ranking now focuses on repairs, diagnostics, components, and testing.
- Customer requirements, terminology, other/admin, identity, and clear form noise remain preserved in reference outputs.
- Service-area rollups now use concept label/key only instead of scanning all representative raw evidence.
- Stocking attention now uses component/repair labels or at least two independent logs with explicit repair-action evidence.
- Mixed-group evidence fallback counts only the matching evidence rows instead of the entire group.
- No LLM calls.
- No approved facts.
- Qdrant remains OFF.
