param(
    [string]$Server = "192.168.86.32",
    [string]$User = "drladmin"
)

$ErrorActionPreference = "Stop"

$ssh = Join-Path $env:WINDIR "System32\OpenSSH\ssh.exe"
$key = Join-Path $env:USERPROFILE ".ssh\nova_drl_ed25519"
$reportsUNC = "\\192.168.86.25\Public\NOVA_REPORTS"

function BashQuote([string]$Text) {
    if ($null -eq $Text) { return "''" }
    return "'" + ($Text -replace "'", "'""'""'") + "'"
}

function Invoke-NovaCapture([string]$RemoteCommand) {
    $lines = & $ssh -i $key -o BatchMode=yes "$User@$Server" $RemoteCommand 2>&1
    $rc = $LASTEXITCODE
    $text = ($lines | Out-String).TrimEnd()
    return [pscustomobject]@{
        ExitCode = $rc
        Text = $text
    }
}

function Show-Search([string]$Query) {
    $q = BashQuote $Query
    $r = Invoke-NovaCapture "/opt/nova-drl/bin/nova-drl --search $q"

    if ($r.Text) {
        # Replace only the old frozen-engine action hint with the technician workflow.
        $text = $r.Text -replace `
            'Actions:\s*:pdf create/open printable PDF\s+:print send current report to printer', `
            'Actions: :docx create/open editable Word report   :review review saved edits   :new new search'
        Write-Host $text
    }

    if ($r.ExitCode -ne 0) {
        Write-Host ""
        Write-Host "NOVA search failed with exit code $($r.ExitCode)." -ForegroundColor Red
        return $false
    }
    return $true
}

function New-Docx([string]$Query) {
    $q = BashQuote $Query
    $r = Invoke-NovaCapture "/opt/nova-drl/bin/nova-drl-tech --docx $q"

    if ($r.Text) {
        Write-Host $r.Text
    }

    if ($r.ExitCode -ne 0) {
        Write-Host ""
        Write-Host "DOCX generation failed with exit code $($r.ExitCode)." -ForegroundColor Red
        return $null
    }

    $matches = [regex]::Matches($r.Text, '(?m)^DOCX:\s*(/mnt/drl-reports/[^\r\n]+\.docx)\s*$')
    if ($matches.Count -eq 0) {
        Write-Host ""
        Write-Host "NOVA created no parseable DOCX path." -ForegroundColor Yellow
        return $null
    }

    # Use the last DOCX line in case the generator printed more than one status line.
    $linuxPath = $matches[$matches.Count - 1].Groups[1].Value.Trim()
    $fileName = [System.IO.Path]::GetFileName($linuxPath)
    $windowsPath = Join-Path $reportsUNC $fileName

    Write-Host ""
    Write-Host "Opening Word report:" -ForegroundColor Cyan
    Write-Host "  $windowsPath"

    # The old Windows-client pattern: act on the exact report returned by the server.
    # No watcher and no folder polling.
    $ready = $false
    for ($i = 0; $i -lt 20; $i++) {
        if (Test-Path -LiteralPath $windowsPath) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 250
    }

    if (-not $ready) {
        Write-Host "DOCX exists on Ubuntu but is not visible from Windows yet:" -ForegroundColor Yellow
        Write-Host "  $windowsPath"
        return [pscustomobject]@{
            LinuxPath = $linuxPath
            WindowsPath = $windowsPath
        }
    }

    Start-Process -FilePath $windowsPath

    return [pscustomobject]@{
        LinuxPath = $linuxPath
        WindowsPath = $windowsPath
    }
}

function Review-Docx($CurrentDocx) {
    if ($null -eq $CurrentDocx) {
        Write-Host ""
        Write-Host "No current DOCX yet. Use :docx first." -ForegroundColor Yellow
        return
    }

    $p = BashQuote $CurrentDocx.LinuxPath
    Write-Host ""
    Write-Host "Reviewing DOCX:"
    Write-Host "  $($CurrentDocx.WindowsPath)"
    Write-Host ""

    # Reviewer is interactive, so give SSH a terminal and let its prompts display directly.
    & $ssh -i $key -o BatchMode=yes -t "$User@$Server" "/opt/nova-drl/bin/nova-drl-docx-review $p"
}

if (-not (Test-Path -LiteralPath $ssh)) {
    throw "Windows OpenSSH client was not found: $ssh"
}
if (-not (Test-Path -LiteralPath $key)) {
    throw "NOVA SSH key was not found: $key"
}
if (-not (Test-Path -LiteralPath $reportsUNC)) {
    throw "NOVA_REPORTS is not reachable from Windows: $reportsUNC"
}

Clear-Host
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "NOVA DRL WINDOWS TECHNICIAN CLIENT  |  v1.9.4" -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "Frozen search engine: v1.5.16"
Write-Host "DOCX output:          $reportsUNC"
Write-Host "Passwordless SSH:     ON"
Write-Host "DOCX auto-open:       EXACT PATH returned by NOVA"
Write-Host ""

while ($true) {
    $query = Read-Host "Enter DRL Part #  [Q=quit]"
    if ([string]::IsNullOrWhiteSpace($query)) { continue }
    if ($query.Trim().ToLowerInvariant() -in @("q","quit",":quit","exit")) { break }

    $query = $query.Trim()
    if (-not (Show-Search $query)) { continue }

    $currentDocx = $null

    while ($true) {
        Write-Host ""
        $action = Read-Host "Action [:docx / :review / :new / :quit] [Enter=:new]"
        if ([string]::IsNullOrWhiteSpace($action)) { $action = ":new" }

        switch ($action.Trim().ToLowerInvariant()) {
            ":docx" {
                $created = New-Docx $query
                if ($null -ne $created) { $currentDocx = $created }
            }
            ":review" {
                Review-Docx $currentDocx
            }
            ":new" {
                break
            }
            ":quit" {
                exit 0
            }
            "quit" {
                exit 0
            }
            "exit" {
                exit 0
            }
            default {
                Write-Host "Use :docx, :review, :new, or :quit." -ForegroundColor Yellow
                continue
            }
        }

        if ($action.Trim().ToLowerInvariant() -eq ":new" -or [string]::IsNullOrWhiteSpace($action)) {
            break
        }
    }
}
