param(
    [string]$Server = "192.168.86.32",
    [string]$User = "drladmin",
    [string]$KeyPath = "$env:USERPROFILE\.ssh\nova_drl_ed25519",
    [string]$ReportDirectory = "Z:\NOVA DRL Reports"
)

$ErrorActionPreference = "Stop"
$ssh = Join-Path $env:WINDIR "System32\OpenSSH\ssh.exe"
$scp = Join-Path $env:WINDIR "System32\OpenSSH\scp.exe"
$remoteTool = "/opt/nova-drl/tools/nova_drl_unified_knowledge_index_v1_4_12.py"
$script:LastQuery = $null
$script:LastReport = $null

function Write-ActionHint {
    Write-Host ""
    Write-Host -NoNewline "Actions: "
    Write-Host -NoNewline ":pdf" -ForegroundColor Cyan
    Write-Host -NoNewline " save/open PDF   "
    Write-Host -NoNewline ":print" -ForegroundColor Cyan
    Write-Host -NoNewline " send/open for printing   "
    Write-Host ":open reopen last PDF"
}

function Encode-Query([string]$Text) {
    return [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Text))
}

function Invoke-NovaRemote([string]$RemoteCommand, [switch]$Quiet) {
    $args = @(
        "-i", $KeyPath,
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "${User}@${Server}",
        $RemoteCommand
    )
    $out = & $ssh @args 2>&1
    $rc = $LASTEXITCODE
    if (-not $Quiet -and $out) {
        $out | ForEach-Object { Write-Host $_ }
    }
    if ($rc -ne 0) {
        throw "NOVA DRL remote command failed (SSH exit $rc)."
    }
    return @($out)
}

function Get-ReportDirectory {
    $preferred = $ReportDirectory
    try {
        if ($preferred -and (Test-Path ([IO.Path]::GetPathRoot($preferred)))) {
            New-Item -ItemType Directory -Force -Path $preferred | Out-Null
            return $preferred
        }
    } catch {}

    $fallback = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "NOVA DRL Reports"
    New-Item -ItemType Directory -Force -Path $fallback | Out-Null
    Write-Host "DRL mapped report folder is unavailable; using local fallback:" -ForegroundColor Yellow
    Write-Host "  $fallback"
    return $fallback
}

function New-NovaReport([string]$Query, [switch]$PrintMode) {
    if (-not $Query) {
        Write-Host "No previous search. Type a search first." -ForegroundColor Yellow
        return
    }

    $b64 = Encode-Query $Query
    Write-Host "Creating printable NOVA DRL report..." -ForegroundColor Cyan
    $lines = Invoke-NovaRemote "python3 $remoteTool --pdf-file-b64 $b64" -Quiet
    $marker = $lines | Where-Object { $_ -match '^NOVA_DRL_REPORT_PATH=(.+)$' } | Select-Object -Last 1
    if (-not $marker) {
        $lines | ForEach-Object { Write-Host $_ }
        throw "Nova server did not return a report path."
    }
    $serverPath = ([regex]::Match($marker, '^NOVA_DRL_REPORT_PATH=(.+)$')).Groups[1].Value.Trim()
    $name = Split-Path -Leaf $serverPath
    $destDir = Get-ReportDirectory
    $localPath = Join-Path $destDir $name

    $remoteSpec = "${User}@${Server}:$serverPath"
    $scpArgs = @(
        "-i", $KeyPath,
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        $remoteSpec,
        $localPath
    )
    & $scp @scpArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Could not copy NOVA DRL PDF from the server."
    }

    $script:LastReport = $localPath
    Write-Host ""
    Write-Host "REPORT SAVED:" -ForegroundColor Green
    Write-Host "  $localPath"

    if ($PrintMode) {
        try {
            Start-Process -FilePath $localPath -Verb Print
            Write-Host "Sent to the Windows default PDF print handler." -ForegroundColor Green
            return
        } catch {
            Write-Host "The default PDF application does not expose a direct Print action." -ForegroundColor Yellow
            Write-Host "Opening the report instead; use Ctrl+P in the PDF viewer." -ForegroundColor Yellow
        }
    }

    Start-Process -FilePath $localPath
    Write-Host "Opened with the Windows default PDF application." -ForegroundColor Green
}

if (-not (Test-Path $ssh)) { throw "Windows OpenSSH client not found: $ssh" }
if (-not (Test-Path $scp)) { throw "Windows SCP client not found: $scp" }
if (-not (Test-Path $KeyPath)) {
    throw "NOVA DRL SSH key not found: $KeyPath`nRe-run the NOVA DRL v1.4.12 Windows installer."
}

Clear-Host
Write-Host "Nova DRL Windows Engineer Client v1.4.12" -ForegroundColor Cyan
Write-Host "Fast indexed search; PDF reports are copied to the DRL Windows share and opened locally."
Write-Host "No AI call is used for simple lookups. Commands: :help  :status  :pdf  :print  :open  :quit"
Write-Host ""

try {
    $probe = Invoke-NovaRemote "echo NOVA_DRL_KEY_OK" -Quiet
    if (-not ($probe -match 'NOVA_DRL_KEY_OK')) { throw "SSH key verification failed." }
} catch {
    Write-Host "Unable to connect to NOVA DRL using the installed SSH key." -ForegroundColor Red
    Write-Host $_.Exception.Message
    Read-Host "Press Enter to close"
    exit 2
}

while ($true) {
    $q = Read-Host "NOVA-DRL"
    if ($null -eq $q) { break }
    $q = $q.Trim()
    if (-not $q) { continue }

    if ($q -in @(':q', ':quit', 'quit', 'exit')) { break }
    if ($q -eq ':help') {
        Write-Host "Search examples: BRD-1526990 | 1526990 | S07211 | RMA 53434 | DGK52102 | MSR 56889 | IXFX24N100 | RCL1A"
        Write-Host ":pdf              Save current search PDF to the DRL share and open it"
        Write-Host ":pdf <search>     Create/open a PDF for another search"
        Write-Host ":print            Send current report to the Windows default PDF print handler"
        Write-Host ":print <search>   Print another search"
        Write-Host ":open             Reopen the most recently copied PDF"
        Write-Host ":status           Show Nova DRL unified-index status"
        continue
    }
    if ($q -eq ':status') {
        Invoke-NovaRemote "python3 $remoteTool --status"
        continue
    }
    if ($q -eq ':open') {
        if ($script:LastReport -and (Test-Path $script:LastReport)) {
            Start-Process -FilePath $script:LastReport
        } else {
            Write-Host "No report has been created in this session." -ForegroundColor Yellow
        }
        continue
    }
    if ($q -eq ':pdf' -or $q.StartsWith(':pdf ')) {
        $target = if ($q.StartsWith(':pdf ')) { $q.Substring(5).Trim() } else { $script:LastQuery }
        try { New-NovaReport $target } catch { Write-Host $_.Exception.Message -ForegroundColor Red }
        continue
    }
    if ($q -eq ':print' -or $q.StartsWith(':print ')) {
        $target = if ($q.StartsWith(':print ')) { $q.Substring(7).Trim() } else { $script:LastQuery }
        try { New-NovaReport $target -PrintMode } catch { Write-Host $_.Exception.Message -ForegroundColor Red }
        continue
    }

    $script:LastQuery = $q
    try {
        $b64 = Encode-Query $q
        Invoke-NovaRemote "python3 $remoteTool --search-b64 $b64 --no-actions"
        Write-ActionHint
    } catch {
        Write-Host $_.Exception.Message -ForegroundColor Red
    }
}

Write-Host "NOVA DRL session ended."
