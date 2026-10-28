# HF Hub helper for Windows.
# Answers HF Hub's page - on this computer only (127.0.0.1) - with a signed snapshot:
# computer name, LAN IP, network link (cable speed / Wi-Fi) and SSD usage + health.
# Reads nothing else: no files, screens, keystrokes or browsing.
# Runs hidden as SYSTEM from the "HF Hub Helper" scheduled task (starts with Windows).

$Dir = Join-Path $env:ProgramData 'HFHub'
$Log = Join-Path $Dir 'helper.log'

function Write-Log([string]$msg) {
    try {
        if ((Test-Path $Log) -and (Get-Item $Log).Length -gt 1MB) { Remove-Item $Log -Force }
        Add-Content -LiteralPath $Log -Value ("{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg)
    } catch { }
}

function Read-Conf {
    $c = @{}
    Get-Content -LiteralPath (Join-Path $Dir 'helper.conf') | ForEach-Object {
        if ($_ -match '^\s*(\w+)\s*=\s*(.*?)\s*$') { $c[$Matches[1]] = $Matches[2] }
    }
    if (-not $c.device_id -or -not $c.secret) { throw 'helper.conf is missing device_id/secret' }
    return $c
}

function Read-Network {
    $ErrorActionPreference = 'SilentlyContinue'
    $net = [ordered]@{ ip = $null; iface = $null; link_type = 'none'; link_mbps = $null }
    # The adapter the internet goes through (lowest-metric default route on an adapter that is up)
    $route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' |
        Where-Object { (Get-NetAdapter -InterfaceIndex $_.ifIndex).Status -eq 'Up' } |
        Sort-Object RouteMetric | Select-Object -First 1
    if (-not $route) { return $net }
    $a = Get-NetAdapter -InterfaceIndex $route.ifIndex
    $ip = Get-NetIPAddress -InterfaceIndex $route.ifIndex -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1
    if (-not $a -or -not $ip) { return $net }
    $net.ip = [string]$ip.IPAddress
    $net.iface = [string]$a.InterfaceDescription
    if ($a.PhysicalMediaType -match '802\.11|Wireless' -or $a.NdisPhysicalMedium -eq 9) {
        $net.link_type = 'wifi'
    } else {
        $net.link_type = 'cable'
        $bps = if ($a.ReceiveLinkSpeed) { $a.ReceiveLinkSpeed } else { $a.Speed }
        if ($bps) { $net.link_mbps = [int]([double]$bps / 1000000) }   # e.g. 1000000000 -> 1000
    }
    return $net
}

function Read-Disks {
    $ErrorActionPreference = 'SilentlyContinue'
    $phys = @{}
    Get-PhysicalDisk | ForEach-Object { $phys[[string]$_.DeviceId] = $_ }
    $disks = @()
    Get-Volume | Where-Object { $_.DriveType -eq 'Fixed' -and $_.DriveLetter -and $_.Size -gt 0 } |
        Sort-Object DriveLetter | ForEach-Object {
            $letter = [string]$_.DriveLetter
            $used = [int][math]::Round(100 * ($_.Size - $_.SizeRemaining) / $_.Size)
            $health = $null; $wear = $null; $temp = $null
            $part = Get-Partition -DriveLetter $letter | Select-Object -First 1
            $pd = if ($part) { $phys[[string]$part.DiskNumber] } else { $null }
            if ($pd) {
                $health = [string]$pd.HealthStatus                      # Healthy / Warning / Unhealthy
                if ($health -eq 'Unknown' -or $health -eq '') { $health = $null }
                $rc = $pd | Get-StorageReliabilityCounter
                if ($rc) {
                    if ($null -ne $rc.Wear) { $wear = [int]$rc.Wear }
                    if ($rc.Temperature) { $temp = [int]$rc.Temperature }
                }
            }
            $disks += [ordered]@{ name = "${letter}:"; used_pct = $used; health = $health; wear_pct = $wear; temp_c = $temp }
        }
    return ,$disks
}

function Send-Reply($stream, [string]$status, [string]$headers, [string]$body) {
    $b = [Text.Encoding]::UTF8.GetBytes($body)
    $head = [Text.Encoding]::ASCII.GetBytes(
        "HTTP/1.1 $status`r`n$headers" + "Content-Length: $($b.Length)`r`nConnection: close`r`n`r`n")
    $stream.Write($head, 0, $head.Length)
    if ($b.Length) { $stream.Write($b, 0, $b.Length) }
    $stream.Flush()
}

function Handle-Client($client) {
    $client.ReceiveTimeout = 5000; $client.SendTimeout = 5000
    $stream = $client.GetStream()
    $reader = New-Object System.IO.StreamReader($stream, [Text.Encoding]::ASCII, $false, 4096, $true)
    $line = $reader.ReadLine()
    if (-not $line) { return }
    $parts = $line -split ' '
    $method = $parts[0]; $path = if ($parts.Length -gt 1) { $parts[1] } else { '' }
    $h = @{}
    while ($true) {
        $l = $reader.ReadLine()
        if ($null -eq $l -or $l -eq '') { break }
        if ($l -match '^([^:]+):\s*(.*)$') { $h[$Matches[1].ToLower()] = $Matches[2] }
    }
    $origin = [string]$h['origin']
    $allowed = $origin -and ($script:Origins -ccontains $origin)
    $cors = "Vary: Origin`r`n"
    if ($allowed) {
        $cors += "Access-Control-Allow-Origin: $origin`r`nAccess-Control-Allow-Methods: GET`r`n" +
                 "Access-Control-Allow-Headers: Content-Type`r`nAccess-Control-Allow-Private-Network: true`r`n"
    }
    if ($method -eq 'OPTIONS') { return Send-Reply $stream '204 No Content' $cors '' }
    if ($method -ne 'GET' -or $path -notmatch '^/snapshot') { return Send-Reply $stream '404 Not Found' $cors '' }
    if (-not $allowed) { return Send-Reply $stream '403 Forbidden' $cors '' }   # only HF Hub's own page

    $snap = [ordered]@{
        device_id = [int]$script:cfg.device_id
        ts        = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        hostname  = $env:COMPUTERNAME
        platform  = 'windows'
        ip        = $script:net.ip
        iface     = $script:net.iface
        link_type = $script:net.link_type
        link_mbps = $script:net.link_mbps
        disks     = $script:disks
    }
    $payload = [Text.Encoding]::UTF8.GetBytes(($snap | ConvertTo-Json -Compress -Depth 4))
    $sig = -join ($script:hmac.ComputeHash($payload) | ForEach-Object { $_.ToString('x2') })
    $body = '{"payload":"' + [Convert]::ToBase64String($payload) + '","sig":"' + $sig + '"}'
    Send-Reply $stream '200 OK' ($cors + "Content-Type: application/json`r`nCache-Control: no-store`r`n") $body
}

function Start-Helper {
    $script:cfg = Read-Conf
    $script:Origins = @($script:cfg.origins -split '\s*,\s*' | Where-Object { $_ })
    $script:hmac = New-Object System.Security.Cryptography.HMACSHA256 (, [Text.Encoding]::UTF8.GetBytes($script:cfg.secret))
    $port = if ($script:cfg.port) { [int]$script:cfg.port } else { 47600 }

    # Readings are refreshed in the background so a request is answered instantly:
    # network every 30 seconds, SSD every 5 minutes (it changes slowly and is slower to read).
    $script:net = Read-Network;  $netAt = Get-Date
    $script:disks = Read-Disks;  $diskAt = Get-Date

    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $port)
    $listener.Start()
    Write-Log "listening on 127.0.0.1:$port"
    try {
        while ($true) {
            $pending = $listener.AcceptTcpClientAsync()
            while (-not $pending.Wait(1000)) {
                if (((Get-Date) - $netAt).TotalSeconds -ge 30) { $script:net = Read-Network; $netAt = Get-Date }
                if (((Get-Date) - $diskAt).TotalMinutes -ge 5) { $script:disks = Read-Disks; $diskAt = Get-Date }
            }
            $client = $pending.Result
            try { Handle-Client $client } catch { Write-Log "request failed: $($_.Exception.Message)" }
            finally { $client.Close() }
        }
    } finally { $listener.Stop() }
}

while ($true) {
    try { Start-Helper } catch { Write-Log "helper stopped: $($_.Exception.Message)" }
    Start-Sleep -Seconds 10       # e.g. port busy or network not ready yet; try again
}
