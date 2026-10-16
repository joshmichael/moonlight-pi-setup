# moonlight-usb-helper.ps1
#
# Installed by the moonlight-pi-setup PC setup (moonlight-pc-setup.ps1), and
# started hidden when you log in to Windows.
#
# Every 2 seconds it asks your Moonlight Pi which USB devices it's sharing,
# and attaches them with usbip-win2, so they work on this PC as if they were
# plugged into it. The Pi only shares them while it's streaming to this PC, and
# takes them back when the stream ends: they then disappear from this PC by
# themselves. Asking also tells the Pi this PC is ready, so the Pi never sends
# devices to a PC that can't take them.
#
# It only ever connects out to the Pi (TCP port 3240), and needs no admin
# rights. Its settings (which Pi) are in usb-sharing.json next to it, and it
# keeps a short log in usb-helper.log.

$ErrorActionPreference = 'Continue'

$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigPath = Join-Path $AppDir 'usb-sharing.json'
$LogPath = Join-Path $AppDir 'usb-helper.log'
$Usbip = Join-Path $env:ProgramFiles 'USBip\usbip.exe'
$Port = 3240
$IntervalSeconds = 2
$RefindAfterSeconds = 60       # look for a Pi again when it hasn't answered for this long...
$RefindEverySeconds = 300      # ...but not more often than this

function Write-Log([string]$Text) {
    try {
        Add-Content -LiteralPath $LogPath -Value ('{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Text)
        if ((Get-Item -LiteralPath $LogPath).Length -gt 256KB) {
            $keep = Get-Content -LiteralPath $LogPath -Tail 1000
            Set-Content -LiteralPath $LogPath -Value $keep
        }
    } catch {}
}

# Only one copy per user.
$mutex = New-Object System.Threading.Mutex($false, 'Local\MoonlightPiUsbHelper')
if (-not $mutex.WaitOne(0)) { exit }

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
function Read-Config {
    try {
        $c = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
        return @($c.pis | Where-Object { $_ -and $_.address })
    } catch {
        return @()
    }
}

function Save-Config($Pis) {
    $tmp = "$ConfigPath.tmp"
    ([pscustomobject]@{ pis = @($Pis) } | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination $ConfigPath -Force
}

# ---------------------------------------------------------------------------
# Talking to the Pi
# ---------------------------------------------------------------------------
# Whether something answers on the USB/IP port (quickly, unlike usbip.exe when
# the Pi is switched off or away).
function Test-UsbipPort([string]$Address, [int]$TimeoutMs = 500) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        return ($client.ConnectAsync($Address, $Port).Wait($TimeoutMs) -and $client.Connected)
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

# Runs usbip.exe: (exit code, what it printed).
function Invoke-Usbip([string[]]$Arguments) {
    $out = (& $Usbip @Arguments 2>&1 | ForEach-Object { "$_" }) -join "`n"
    return $LASTEXITCODE, $out
}

# The bus IDs of the devices the Pi shares now, or $null if it didn't answer.
function Get-SharedDevices([string]$Address) {
    $rc, $out = Invoke-Usbip @('list', '-r', $Address)
    if ($rc -ne 0 -and $out -notmatch 'no exportable devices') { return $null }
    return @([regex]::Matches($out, '(?m)^\s+(\d+-[\d.]+)\s+:') | ForEach-Object { $_.Groups[1].Value })
}

# "address/busid" of each device attached from a USB/IP server.
function Get-AttachedDevices {
    $rc, $out = Invoke-Usbip @('port')
    return @([regex]::Matches($out, 'usbip://(\[[^\]]+\]|[^:/\s]+):\d+/(\S+)') |
        ForEach-Object { '{0}/{1}' -f $_.Groups[1].Value.Trim('[', ']'), $_.Groups[2].Value })
}

# The Pi's Tailscale address, from Tailscale on this PC: the peer that has the
# Pi's home address among its endpoints, or the Pi's name.
function Find-TailscaleAddress($Pi) {
    $ts = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
    if (-not (Test-Path -LiteralPath $ts)) { return $null }
    try {
        $status = & $ts status --json 2>$null | Out-String | ConvertFrom-Json
    } catch {
        return $null
    }
    $short = ("$($Pi.name)" -split '\.')[0].ToLower()
    foreach ($p in $status.Peer.PSObject.Properties.Value) {
        $endpoints = @($p.Addrs) + @($p.CurAddr) | Where-Object { $_ }
        $match = ($endpoints | Where-Object { $_ -like "$($Pi.address):*" }) -or
            ($short -and "$($p.HostName)".ToLower() -eq $short)
        if ($match) {
            $ip = @($p.TailscaleIPs) | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' } | Select-Object -First 1
            if ($ip) { return $ip }
        }
    }
    return $null
}

# Finds a Pi that has moved to another address: by its name, then by its
# network card's hardware address on the home network.
function Find-PiAgain($Pi) {
    if ($Pi.name) {
        try {
            foreach ($a in [System.Net.Dns]::GetHostAddresses("$($Pi.name)")) {
                if ($a.AddressFamily -eq 'InterNetwork' -and (Test-UsbipPort $a.IPAddressToString)) {
                    return $a.IPAddressToString
                }
            }
        } catch {}
    }
    if (-not $Pi.mac) { return $null }
    $subnets = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.PrefixOrigin -in 'Dhcp', 'Manual' -and $_.IPAddress -notlike '169.254.*' -and
                       $_.InterfaceAlias -notmatch 'Tailscale|vEthernet|Loopback|Bluetooth' }
    foreach ($s in $subnets) {
        $base = ($s.IPAddress -split '\.')[0..2] -join '.'
        $clients = foreach ($i in 1..254) {
            $c = New-Object System.Net.Sockets.TcpClient
            [pscustomobject]@{ Ip = "$base.$i"; Client = $c; Task = $c.ConnectAsync("$base.$i", $Port) }
        }
        try { [System.Threading.Tasks.Task]::WaitAll(@($clients.Task), 1500) | Out-Null } catch {}
        $open = @($clients | Where-Object { $_.Task.Status -eq 'RanToCompletion' } | ForEach-Object { $_.Ip })
        $clients | ForEach-Object { $_.Client.Close() }
        foreach ($ip in $open) {
            $n = Get-NetNeighbor -IPAddress $ip -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($n -and $n.LinkLayerAddress -eq $Pi.mac) { return $ip }
        }
    }
    return $null
}

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
Write-Log 'Started'
$lastOk = @{}          # Pi -> when it last answered
$lastFind = @{}        # Pi -> when we last looked for it
$reachable = @{}       # Pi -> the address it last answered on
$lastTailscale = [datetime]::MinValue
$warnedNoUsbip = $false

while ($true) {
    if (-not (Test-Path -LiteralPath $Usbip)) {
        if (-not $warnedNoUsbip) { Write-Log "usbip-win2 isn't installed ($Usbip). Run the PC setup again." }
        $warnedNoUsbip = $true
        Start-Sleep -Seconds 60
        continue
    }
    $warnedNoUsbip = $false
    $pis = @(Read-Config)
    $changed = $false
    $now = Get-Date

    # Fill in the Pi's Tailscale address when it becomes known (checked once a minute).
    if (($now - $lastTailscale).TotalSeconds -gt 60) {
        $lastTailscale = $now
        foreach ($pi in $pis) {
            if (-not $pi.tailscale) {
                $ts = Find-TailscaleAddress $pi
                if ($ts) {
                    $pi | Add-Member -NotePropertyName tailscale -NotePropertyValue $ts -Force
                    Write-Log "Found $($pi.name)'s Tailscale address: $ts"
                    $changed = $true
                }
            }
        }
    }

    $attached = Get-AttachedDevices
    foreach ($pi in $pis) {
        $key = "$($pi.name)|$($pi.mac)"
        $answered = $null
        # The home address first: during a stream the Pi only answers the address
        # it's streaming to, so a device is never attached twice.
        foreach ($addr in @($pi.address, $pi.tailscale) | Where-Object { $_ }) {
            if (-not (Test-UsbipPort $addr)) { continue }
            $devices = Get-SharedDevices $addr
            if ($null -eq $devices) { continue }
            $answered = $addr
            foreach ($id in $devices) {
                if ($attached -contains "$addr/$id") { continue }
                $rc, $out = Invoke-Usbip @('attach', '-r', $addr, '-b', $id, '--once')
                Write-Log "Device $id from $($pi.name) ($addr): $($out.Trim())"
            }
            break
        }
        if ($answered) {
            if ($reachable[$key] -ne $answered) { Write-Log "Connected to $($pi.name) at $answered" }
            $reachable[$key] = $answered
            $lastOk[$key] = $now
            continue
        }
        if ($reachable[$key]) { Write-Log "$($pi.name) isn't answering (switched off, or away)" }
        $reachable[$key] = $null
        $since = if ($lastOk[$key]) { ($now - $lastOk[$key]).TotalSeconds } else { [double]::MaxValue }
        $looked = if ($lastFind[$key]) { ($now - $lastFind[$key]).TotalSeconds } else { [double]::MaxValue }
        if ($since -gt $RefindAfterSeconds -and $looked -gt $RefindEverySeconds) {
            $lastFind[$key] = $now
            $new = Find-PiAgain $pi
            if ($new -and $new -ne $pi.address) {
                Write-Log "$($pi.name) has moved from $($pi.address) to $new"
                $pi.address = $new
                $changed = $true
            }
        }
    }
    if ($changed) { Save-Config $pis }
    Start-Sleep -Seconds $IntervalSeconds
}
