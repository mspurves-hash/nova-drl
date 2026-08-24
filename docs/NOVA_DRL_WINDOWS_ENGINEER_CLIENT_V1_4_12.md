# NOVA DRL Windows Engineer Client v1.4.12

## Engineer experience
Double-click **NOVA DRL** on the Windows desktop. The client opens a local PowerShell prompt:

```text
NOVA-DRL> 1526990
NOVA-DRL> S07211
NOVA-DRL> 53434
NOVA-DRL> DGK52102
```

Search remains retrieval-only and runs against the local SQLite unified knowledge index on the Nova server. An LLM is not invoked for simple lookup.

## Printable reports
After any search:

```text
NOVA-DRL> :pdf
```

The client:
1. asks the Nova server to generate the current-search PDF;
2. copies that PDF via SCP to `Z:\NOVA DRL Reports` by default;
3. opens the copied file using the Windows workstation's default PDF application.

If `Z:` is unavailable, the client uses the workstation's `Documents\NOVA DRL Reports` folder instead.

`:print` attempts the Windows default PDF application's Print verb. If the installed PDF handler does not support that verb, the report is opened normally and the engineer can use Ctrl+P.

`:open` reopens the most recent PDF created in the current client session.

## Why Windows owns the final save
The Nova server intentionally treats the DRL share as read-only. v1.4.12 preserves that safety policy. Nova generates the report under `/opt/nova-drl/reports`; the Windows client, which already has normal user write access to the mapped DRL share, copies the PDF into the shared report folder.

## SSH authentication
The installer creates a dedicated Ed25519 key at:

```text
%USERPROFILE%\.ssh\nova_drl_ed25519
```

If the public key has not yet been authorized, the installer asks for the Ubuntu password once and appends the public key to `~/.ssh/authorized_keys`. The password is never stored in the shortcut, client, configuration, or key file.

## Installation
From Windows PowerShell, with the v1.4.12 package available locally:

```text
powershell.exe -ExecutionPolicy Bypass -File "<path>\windows\Install-NOVA-DRL-Engineer-Client.ps1"
```

Defaults:
- server: `192.168.86.32`
- user: `drladmin`
- reports: `Z:\NOVA DRL Reports`

They may be overridden with installer parameters.

## Server commands used by the client
The Windows client uses machine-oriented modes:

```text
--search-b64 <base64-query> --no-actions
--pdf-file-b64 <base64-query>
```

The PDF-file mode prints exactly one machine-readable report marker:

```text
NOVA_DRL_REPORT_PATH=/opt/nova-drl/reports/NOVA_DRL_....pdf
```

The Windows client copies that file using SCP and opens it locally.
