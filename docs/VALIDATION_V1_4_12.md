# Validation - v1.4.12

Validated in the build environment:

- Python compilation passes.
- Synthetic unified-index regression suite passes.
- Strict identifier/search rules from v1.4.11 remain intact.
- Base64 query round-trip tested, including invalid input rejection.
- Machine PDF-file mode creates a valid PDF without starting the HTTP report server or invoking CUPS.
- Windows client and installer are packaged and statically checked for expected server path, report-share path, SCP transfer, SSH-key setup, and auto-open behavior.
- Generated PDF rendered to PNG using the PDF validation workflow; no clipping/overlap observed.

Windows-specific PowerShell execution must be validated on an actual DRL workstation after Git deployment because Windows PowerShell is not available in the Linux build container.
