# Nova DRL Unified Knowledge Search + Print v1.4.11

v1.4.11 is an engineer-facing presentation/Windows-launch update over the existing v1.4.8 unified SQLite knowledge index. No knowledge-index rebuild is required when upgrading from v1.4.10 unless source data has changed.

## Search

On the Nova server:

    nova-drl

Then type any full/partial identifier, model, serial, RMA, DRL log, manufacturer PN, customer PO, or procurement/order reference.

Ordinary search is local SQLite/FTS5 retrieval. No LLM is invoked.

## PDF and print actions

After a search the prompt shows blue action hints:

    Actions: :pdf create/open printable PDF   :print send current report to printer

`:pdf` creates the report and now always prints two access forms:

1. A blue OSC-8 clickable link for modern terminals.
2. A completely plain LAN-IP URL labeled `COPY/PASTE INTO CHROME OR EDGE` for classic Windows CMD/PowerShell consoles.

The LAN IPv4 address is preferred over the hostname because it is generally the most reliable browser path from DRL Windows workstations. The hostname is also displayed as an alternate address when available.

`:print` creates the same PDF and attempts to queue it to the configured/default server CUPS printer. The browser URL remains available regardless, so the engineer can print from Chrome/Edge.

## Picasa policy

All `.picasa.ini` files and `.picasaoriginals` backup content remain in the underlying DRL file index for completeness but are hidden from normal engineer search output and printable reports.

## Windows desktop shortcut

The build includes:

    windows/Install-NOVA-DRL-Shortcut.ps1

Run it from a Windows PowerShell prompt in the Nova DRL Git working directory:

    powershell -ExecutionPolicy Bypass -File .\windows\Install-NOVA-DRL-Shortcut.ps1

Default connection:

    user:   drladmin
    server: 192.168.86.32
    remote: /usr/local/bin/nova-drl

The installer creates `NOVA DRL.lnk` on the current Windows user's Desktop and a small launcher under `%LOCALAPPDATA%\NOVA-DRL`.

No password is stored. SSH prompts for credentials normally. The server can be overridden at install time:

    powershell -ExecutionPolicy Bypass -File .\windows\Install-NOVA-DRL-Shortcut.ps1 -Server 192.168.86.32 -User drladmin

Future SSH-key authentication can remove the password prompt without changing the shortcut interface.
