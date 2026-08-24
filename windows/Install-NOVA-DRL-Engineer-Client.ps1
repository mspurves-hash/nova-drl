param(
    [string]$Server = "192.168.86.32",
    [string]$User = "drladmin",
    [string]$SharedReportDirectory = "Z:\NOVA DRL Reports"
)

$ErrorActionPreference = "Stop"
$ssh = Join-Path $env:WINDIR "System32\OpenSSH\ssh.exe"
$scp = Join-Path $env:WINDIR "System32\OpenSSH\scp.exe"
$keygen = Join-Path $env:WINDIR "System32\OpenSSH\ssh-keygen.exe"
foreach ($p in @($ssh,$scp,$keygen)) {
    if (-not (Test-Path $p)) { throw "Windows OpenSSH component not found: $p. Install Windows OpenSSH Client first." }
}

$sourceClient = Join-Path $PSScriptRoot "NOVA-DRL-Engineer.ps1"
if (-not (Test-Path $sourceClient)) { throw "Missing Windows Engineer Client beside installer: $sourceClient" }

$installDir = Join-Path $env:LOCALAPPDATA "NOVA-DRL"
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
$client = Join-Path $installDir "NOVA-DRL-Engineer.ps1"
Copy-Item -Force $sourceClient $client

$sshDir = Join-Path $env:USERPROFILE ".ssh"
New-Item -ItemType Directory -Force -Path $sshDir | Out-Null
$key = Join-Path $sshDir "nova_drl_ed25519"

if (-not (Test-Path $key)) {
    Write-Host "Creating a dedicated NOVA DRL SSH key..." -ForegroundColor Cyan
    Write-Host "When prompted for the key passphrase, press Enter twice to leave it blank." -ForegroundColor Yellow
    # Windows PowerShell 5.1 drops an empty -N argument. Let ssh-keygen ask
    # interactively instead; this works consistently on legacy DRL stations.
    & $keygen -t ed25519 -C "nova-drl-$env:COMPUTERNAME" -f $key
    if ($LASTEXITCODE -ne 0) { throw "ssh-keygen failed." }
}

function Test-NovaKey {
    # Windows PowerShell 5.1 can promote native SSH stderr to a terminating
    # NativeCommandError while testing a not-yet-authorized key. Suppress that
    # expected first-test noise and decide only from SSH's exit code/output.
    $oldEap = $ErrorActionPreference
    try {
        $ErrorActionPreference = "SilentlyContinue"
        $out = & $ssh -i $key -o BatchMode=yes -o ConnectTimeout=8 "${User}@${Server}" "echo NOVA_DRL_KEY_OK" 2>$null
        $rc = $LASTEXITCODE
    } catch {
        $out = @()
        $rc = 1
    } finally {
        $ErrorActionPreference = $oldEap
    }
    return ($rc -eq 0 -and ($out -match "NOVA_DRL_KEY_OK"))
}

if (-not (Test-NovaKey)) {
    Write-Host ""
    Write-Host "One-time SSH key authorization is required." -ForegroundColor Yellow
    Write-Host "You will be asked for the Ubuntu NOVA DRL password once; the password is NOT stored."
    Write-Host ""
    $pub = (Get-Content "$key.pub" -Raw).Trim()
    if ($pub.Contains("'")) { throw "Unexpected quote character in public key comment; recreate key with a simple comment." }
    $remote = "umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; grep -qxF '$pub' ~/.ssh/authorized_keys || echo '$pub' >> ~/.ssh/authorized_keys; chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys"
    & $ssh "${User}@${Server}" $remote
    if ($LASTEXITCODE -ne 0) { throw "Could not authorize the NOVA DRL SSH key." }
}

if (-not (Test-NovaKey)) { throw "NOVA DRL SSH key verification failed after authorization." }
Write-Host "Passwordless NOVA DRL SSH key: OK" -ForegroundColor Green

try {
    if (Test-Path ([IO.Path]::GetPathRoot($SharedReportDirectory))) {
        New-Item -ItemType Directory -Force -Path $SharedReportDirectory | Out-Null
        Write-Host "Shared report folder: $SharedReportDirectory" -ForegroundColor Green
    } else {
        Write-Host "Mapped report drive is not currently available: $SharedReportDirectory" -ForegroundColor Yellow
        Write-Host "The client will fall back to Documents\NOVA DRL Reports if the mapped drive is unavailable."
    }
} catch {
    Write-Host "Could not pre-create shared report folder; runtime fallback remains enabled." -ForegroundColor Yellow
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "NOVA DRL.lnk"
$ws = New-Object -ComObject WScript.Shell
$shortcut = $ws.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$escapedClient = $client.Replace('"','\"')
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$client`" -Server `"$Server`" -User `"$User`" -KeyPath `"$key`" -ReportDirectory `"$SharedReportDirectory`""
$shortcut.WorkingDirectory = $installDir
$shortcut.Description = "NOVA DRL Windows Engineer Client - indexed search and auto-open reports"
$shortcut.IconLocation = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe,0"
$shortcut.Save()

Write-Host ""
Write-Host "NOVA DRL Windows Engineer Client installed." -ForegroundColor Green
Write-Host "Desktop shortcut: $shortcutPath"
Write-Host "Server:           $Server"
Write-Host "User:             $User"
Write-Host "Report folder:    $SharedReportDirectory"
Write-Host "SSH key:          $key"
Write-Host "No password is stored."
Write-Host ""
Write-Host "Double-click NOVA DRL. Type a search. Then type :pdf to save the report to the share and open it automatically."
