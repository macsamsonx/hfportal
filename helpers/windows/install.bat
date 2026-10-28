<# : HF Hub helper - installer for Windows (install once; it starts by itself after every restart).
@echo off
REM Downloaded from HF Hub -> Office PCs. Double-click it, then click Yes when Windows asks.
set "HF_SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "iex ((Get-Content -LiteralPath '%~f0') -join [Environment]::NewLine)"
exit /b
: end of the batch part - everything below is PowerShell #>

$ErrorActionPreference = 'Stop'
$Portal   = '__PORTAL__'
$Code     = '__CODE__'
$Origins  = '__ORIGINS__'
$Dir      = Join-Path $env:ProgramData 'HFHub'
$TaskName = 'HF Hub Helper'

function Finish([string]$msg, [bool]$ok) {
    Write-Host ''
    Write-Host $msg -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' })
    Read-Host 'Press Enter to close' | Out-Null
    exit
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host 'Installing the HF Hub helper - click Yes when Windows asks.'
    try { Start-Process -FilePath $env:HF_SELF -Verb RunAs } catch { Finish 'X Windows did not allow the install (Yes was not clicked).' $false }
    exit
}

Write-Host "Computer: $env:COMPUTERNAME"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$uid = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography').MachineGuid
$body = @{ code = $Code; device_uid = $uid; hostname = $env:COMPUTERNAME; platform = 'windows' } | ConvertTo-Json
try {
    $resp = Invoke-RestMethod -Method Post -Uri "$Portal/api/pc/enroll" -ContentType 'application/json' `
        -Body $body -TimeoutSec 20 -UseBasicParsing
} catch {
    Finish ("X Could not register with HF Hub ($Portal): $($_.Exception.Message)`n" +
            "  Check the network, or download a fresh installer (codes last 24 hours).") $false
}
if (-not $resp.device_id -or $resp.secret -notmatch '^[0-9a-f]+$') { Finish "X HF Hub refused the installer: $resp" $false }

# Stop an older copy (reinstall) before replacing its files
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Start-Sleep -Seconds 2
}

New-Item -ItemType Directory -Force -Path $Dir | Out-Null
$helper = @'
__HELPER__
'@
Set-Content -LiteralPath (Join-Path $Dir 'hf-helper.ps1') -Value $helper -Encoding ASCII
Set-Content -LiteralPath (Join-Path $Dir 'helper.conf') -Encoding ASCII -Value @(
    "device_id=$($resp.device_id)", "secret=$($resp.secret)", "origins=$Origins", 'port=47600')
# Only Windows itself (SYSTEM) and administrators can read the helper's secret
& icacls.exe $Dir /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' /T /Q | Out-Null

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Dir\hf-helper.ps1`""
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description 'HF Hub: answers HF Hub with this PC''s name, IP, network speed and SSD status.' -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

# Let Chrome reach the helper without asking each person (Chrome "local network access")
$pol = 'HKLM:\SOFTWARE\Policies\Google\Chrome\LocalNetworkAccessAllowedForUrls'
if (-not (Test-Path $pol)) { New-Item -Path $pol -Force | Out-Null }
$props = (Get-Item $pol).Property
$have = @($props | ForEach-Object { (Get-ItemProperty -Path $pol).$_ })
foreach ($o in ($Origins -split ',')) {
    if ($have -notcontains $o) {
        $n = 1; while ($props -contains "$n") { $n++ }
        New-ItemProperty -Path $pol -Name "$n" -Value $o -PropertyType String -Force | Out-Null
        $props = @($props) + "$n"
    }
}

$first = ($Origins -split ',')[0]
$ok = $false
for ($i = 0; $i -lt 8 -and -not $ok; $i++) {
    Start-Sleep -Seconds 2
    $r = & curl.exe -s -m 5 -H "Origin: $first" http://127.0.0.1:47600/snapshot 2>$null
    if ($r -match '"sig"') { $ok = $true }
}
if ($ok) {
    Finish ("OK - Installed. This PC now shows up in HF Hub -> Office PCs.`n" +
            "  Use Chrome for HF Hub. If Chrome asks about local devices, click Allow.") $true
}
Finish "X Installed, but the helper isn't answering yet. See $Dir\helper.log" $false
