# NOVA DRL Windows Engineer Client v1.4.13

## Engineer workflow
1. Double-click **NOVA DRL** on the Windows desktop.
2. Type any full or partial search: PN, model, serial, RMA, DRL log, Digi-Key/Mouser order ref, etc.
3. Type `:pdf` to create the current search report, copy it to `Z:\NOVA DRL Reports`, and open it with the Windows default PDF viewer.
4. Type `:print` to invoke the Windows PDF print handler (or open the PDF when no direct Print verb exists).

## v1.4.13 print layout
The standard project report is intentionally concise:
- Equipment/Product identity first.
- Query.
- RMA/DRL tracking.
- Procurement/Reorder when present.
- Repair History.
- Part Occurrences (replacement parts tied to repair events).
- Source Files.

The PDF omits search timing/coverage, Customer PO, duplicate product statistics/top-parts summary, and the redundant Indexed Parts section. Those facts remain available in the interactive unified search when useful.

## Existing workstation upgrades
Re-run `Install-NOVA-DRL-Engineer-Client.ps1`. The installer reuses the existing `%USERPROFILE%\.ssh\nova_drl_ed25519` key and updates the local Windows client/desktop shortcut. No password is stored.

## New workstation setup
If the dedicated key does not exist, Windows PowerShell 5.1-safe setup asks `ssh-keygen` interactively. Press Enter twice when prompted for the key passphrase, then enter the Nova server account password once to authorize the public key.
