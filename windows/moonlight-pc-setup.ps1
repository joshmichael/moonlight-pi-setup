# moonlight-pc-setup.ps1
#
# Sets up the gaming PC to go with moonlight-pi-setup:
#   - USB sharing (optional): USB devices plugged into the Pi, like a wired
#     controller, come to this PC while you stream, as if they were plugged in
#     here. Installs usbip-win2 (the Windows side of USB/IP), finds the Pi, and
#     installs a small helper that starts hidden when you log in.
#   - Sign-in screens (optional): every controller on the Pi works on Windows'
#     lock screen, sign-in screen and UAC prompts, which only take Xbox
#     controllers: while one is up, the Pi adds an Xbox copy of each other
#     controller. Installs a small Windows service that tells the Pi when.
#   - Tailscale (optional), to stream when you're away from home.
#
# Run it in PowerShell on the gaming PC as your normal user (not as admin):
#   irm https://raw.githubusercontent.com/joshmichael/moonlight-pi-setup/main/windows/moonlight-pc-setup.ps1 | iex
#
# Safe to run again: it checks and repairs what's set up, and can remove USB
# sharing from this PC.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'      # Windows PowerShell downloads slowly while showing progress
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$PcSetupVersion = '2.1.0'
$IssuesUrl = 'https://github.com/joshmichael/moonlight-pi-setup/issues'
$RawUrl = 'https://raw.githubusercontent.com/joshmichael/moonlight-pi-setup/main'

# usbip-win2 (https://github.com/vadimgrn/usbip-win2): a tested version, checked
# against its SHA-256 before it's run. Newer versions are only used once tested.
$UsbipVersion = '0.9.8.1'
$UsbipInstallers = @{
    'x64'   = @{ Url = 'https://github.com/vadimgrn/usbip-win2/releases/download/v.0.9.8.1/USBip-0.9.8.1-x64.exe'
                 Sha256 = '38CAD6D4432B52D5BB9409D9AD03B72FDFFC4ADA4CD3A48FBECA1A2752A8518A' }
    'arm64' = @{ Url = 'https://github.com/vadimgrn/usbip-win2/releases/download/v.0.9.8.1/USBip-0.9.8.1-arm64.exe'
                 Sha256 = 'CAB7FF97F79275EEB5C5B8BB2EB3111FF6B6C4EEAD07D9BEC9BE5BD1E3A35800' }
}
$UsbipSigner = 'Cloudyne Systems (Scheibling Consulting AB)'
$UsbipDir = Join-Path $env:ProgramFiles 'USBip'
$UsbipExe = Join-Path $UsbipDir 'usbip.exe'
$UsbipPort = 3240

$AppDir = Join-Path $env:LOCALAPPDATA 'Programs\moonlight-pi-setup'
$HelperName = 'moonlight-usb-helper.ps1'
$HelperPath = Join-Path $AppDir $HelperName
$ConfigPath = Join-Path $AppDir 'usb-sharing.json'
$HelperLog = Join-Path $AppDir 'usb-helper.log'
$InstalledMarker = Join-Path $AppDir 'installed-usbip-win2'    # this setup installed usbip-win2
$StartupLink = Join-Path ([Environment]::GetFolderPath('Startup')) 'Moonlight Pi USB sharing.lnk'
$Conhost = Join-Path $env:SystemRoot 'System32\conhost.exe'
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

# Sign-in screens: a Windows service, compiled here from its C# source with the
# compiler that comes with Windows (.NET Framework 4). It runs as SYSTEM, so it
# and its folders can only be changed by administrators.
$AgentService = 'MoonlightPiSecureScreens'
$AgentSourceName = 'moonlight-secure-screen.cs'
$AgentDir = Join-Path $env:ProgramFiles 'moonlight-pi-setup'
$AgentExe = Join-Path $AgentDir 'moonlight-secure-screen.exe'
$AgentDataDir = Join-Path $env:ProgramData 'moonlight-pi-setup'
$AgentConfig = Join-Path $AgentDataDir 'secure-screen.conf'
$AgentLog = Join-Path $AgentDataDir 'secure-screen.log'
$AgentPort = 3241

$TailscaleInstallerUrl = 'https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe'
$TailscaleSigner = 'Tailscale Inc.'
$TailscaleExe = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'

# Raspberry Pi network cards start with these (to name a Pi found by its address).
$RaspberryPiMacPrefixes = @('28-CD-C1', '2C-CF-67', '88-A2-9E', 'B8-27-EB', 'D8-3A-DD', 'DC-A6-32', 'E4-5F-01')

$script:RestartNeeded = $false

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
function Write-Step([string]$Text) { Write-Host ''; Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Info([string]$Text) { Write-Host "    $Text" }
function Write-Ok([string]$Text)   { Write-Host '    [OK] ' -ForegroundColor Green -NoNewline; Write-Host $Text }
function Write-Warn([string]$Text) { Write-Host '    [WARNING] ' -ForegroundColor Yellow -NoNewline; Write-Host $Text }

function Read-YesNo([string]$Question, [bool]$Default) {
    $hint = if ($Default) { '[Y/n]' } else { '[y/N]' }
    while ($true) {
        $reply = (Read-Host "    $Question $hint").Trim().ToLower()
        if ($reply -eq '') { return $Default }
        if ($reply -in 'y', 'yes') { return $true }
        if ($reply -in 'n', 'no') { return $false }
        Write-Info 'Please answer y or n.'
    }
}

function Test-Signature([string]$Path, [string]$Signer) {
    $sig = Get-AuthenticodeSignature -LiteralPath $Path
    return ($sig.Status -eq 'Valid' -and $sig.SignerCertificate.Subject -like "CN=$Signer,*")
}

# Runs usbip.exe: (exit code, what it printed). (Windows PowerShell turns a
# program's error output into errors, which would stop this script.)
function Invoke-Usbip([string[]]$Arguments) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = (& $UsbipExe @Arguments 2>&1 | ForEach-Object { "$_" }) -join "`n"
        return $LASTEXITCODE, $out
    } finally {
        $ErrorActionPreference = $old
    }
}

# ---------------------------------------------------------------------------
# usbip-win2
# ---------------------------------------------------------------------------
function Get-UsbipInstalledVersion {
    $keys = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    $app = Get-ItemProperty $keys -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like 'USBip *' } |
        Select-Object -First 1
    if ($app) { return "$($app.DisplayVersion)" }
    return $null
}

function Get-WindowsArchitecture {
    $arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    switch ($arch) {
        'AMD64' { return 'x64' }
        'ARM64' { return 'arm64' }
        default { throw "USB sharing needs 64-bit Windows (this PC is $arch)." }
    }
}

function Test-WindowsVersion {
    $build = [Environment]::OSVersion.Version.Build
    if ($build -lt 18362) {
        throw "USB sharing needs Windows 10 version 1903 or later, or Windows 11 (this PC is build $build)."
    }
}

# Whether version $A is at least $B (False if either isn't a version number).
function Test-VersionAtLeast([string]$A, [string]$B) {
    try { return [version]$A -ge [version]$B } catch { return $false }
}

# Other programs can install usbip-win2 too (Vibepollo uses it for its DualSense
# emulation), so one that's already there is only ever updated, never replaced
# by an older version, and only with your OK.
function Install-Usbip {
    $current = Get-UsbipInstalledVersion
    if ($current -and (Test-Path -LiteralPath $UsbipExe)) {
        if (Test-VersionAtLeast $current $UsbipVersion) {
            Write-Ok "usbip-win2 $current is installed"
            return
        }
        Write-Info "usbip-win2 $current is installed. USB sharing is tested with $UsbipVersion."
        if (-not (Read-YesNo "Update usbip-win2 to $UsbipVersion?" $true)) {
            throw "USB sharing needs usbip-win2 $UsbipVersion or newer."
        }
    }
    $arch = Get-WindowsArchitecture
    $pkg = $UsbipInstallers[$arch]
    Write-Info "Downloading usbip-win2 $UsbipVersion ($arch) from GitHub..."
    $tmp = Join-Path $env:TEMP "USBip-$UsbipVersion-$arch.exe"
    Invoke-WebRequest -Uri $pkg.Url -OutFile $tmp -UseBasicParsing
    $hash = (Get-FileHash -LiteralPath $tmp -Algorithm SHA256).Hash
    if ($hash -ne $pkg.Sha256 -or -not (Test-Signature $tmp $UsbipSigner)) {
        Remove-Item -LiteralPath $tmp -Force
        throw "The downloaded usbip-win2 installer isn't the expected file, so it wasn't run."
    }
    Write-Ok 'Download checked (SHA-256 and signature)'
    Write-Info 'While it installs, USB devices on this PC (your keyboard and mouse too) stop for a'
    Write-Info 'moment. Windows asks for permission to install it.'
    Read-Host '    Press Enter to install usbip-win2' | Out-Null
    $log = Join-Path $env:TEMP "USBip-$UsbipVersion-install.log"
    try {
        $p = Start-Process -FilePath $tmp -ArgumentList '/SILENT', '/NORESTART', "/LOG=`"$log`"" -Verb RunAs -Wait -PassThru
    } catch {
        throw "usbip-win2 wasn't installed (Windows didn't get permission to install it)."
    } finally {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
    if ($p.ExitCode -ne 0 -or (Get-UsbipInstalledVersion) -ne $UsbipVersion) {
        throw "usbip-win2 didn't install (exit code $($p.ExitCode)). Its log is at $log"
    }
    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    Set-Content -LiteralPath $InstalledMarker -Value 'usbip-win2 was installed by the moonlight-pi-setup PC setup.'
    $script:RestartNeeded = $true
    Write-Ok "Installed usbip-win2 $UsbipVersion"
}

# Programs other than this setup that use usbip-win2 (their folders), such as
# Vibepollo, which bundles its drivers.
function Get-OtherUsbipUsers {
    @(Get-ChildItem -Path "$env:ProgramFiles\*\drivers\usbip-win2" -Directory -ErrorAction SilentlyContinue |
        ForEach-Object {
            $root = $_.Parent.Parent
            $product = Get-ChildItem -LiteralPath $root.FullName -Filter *.exe -ErrorAction SilentlyContinue |
                ForEach-Object { $_.VersionInfo.ProductName } | Where-Object { $_ } | Select-Object -First 1
            if ($product) { $product } else { $root.Name }
        })
}

function Uninstall-Usbip {
    $uninstaller = Join-Path $UsbipDir 'unins000.exe'
    if (-not (Test-Path -LiteralPath $uninstaller)) { return }
    Write-Info 'Uninstalling usbip-win2 (Windows asks for permission)...'
    try {
        $p = Start-Process -FilePath $uninstaller -ArgumentList '/SILENT', '/NORESTART' -Verb RunAs -Wait -PassThru
    } catch {
        Write-Warn "usbip-win2 wasn't uninstalled (Windows didn't get permission). Remove it in Settings > Apps."
        return
    }
    # The uninstaller hands over to a copy of itself and returns straight away.
    for ($i = 0; $i -lt 120 -and (Get-UsbipInstalledVersion); $i++) { Start-Sleep -Seconds 1 }
    if (Get-UsbipInstalledVersion) {
        Write-Warn "usbip-win2 is still installed (exit code $($p.ExitCode)). Remove it in Settings > Apps."
    } else {
        $script:RestartNeeded = $true
        Write-Ok 'Uninstalled usbip-win2'
    }
}

# ---------------------------------------------------------------------------
# Finding the Pi
# ---------------------------------------------------------------------------
function Test-UsbipPort([string]$Address, [int]$TimeoutMs = 800) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        return ($client.ConnectAsync($Address, $UsbipPort).Wait($TimeoutMs) -and $client.Connected)
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

# Whether a USB/IP server answers at this address (the Pi's USB sharing).
# Between streams it shares nothing, and usbip.exe then prints nothing at all.
function Test-UsbipServer([string]$Address) {
    if (-not (Test-UsbipPort $Address)) { return $false }
    $rc, $out = Invoke-Usbip @('list', '-r', $Address)
    return ($rc -eq 0)
}

# The home network's addresses to look through: the networks this PC reaches
# the internet through (so not virtual networks like VMware's, VirtualBox's or
# WSL's, nor Tailscale). At most the 254 addresses around this PC's own one.
function Get-LocalSubnets {
    Get-NetIPConfiguration -ErrorAction SilentlyContinue |
        Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' -and
                       $_.InterfaceAlias -notmatch 'Tailscale' } |
        ForEach-Object { $_.IPv4Address } | Where-Object { $_.IPAddress -notlike '169.254.*' } |
        ForEach-Object { ($_.IPAddress -split '\.')[0..2] -join '.' } | Sort-Object -Unique
}

# Reads a name from a DNS message: its labels, following compression pointers.
function Read-DnsName([byte[]]$Data, [int]$Index) {
    $labels = @()
    for ($guard = 0; $guard -lt 64 -and $Index -lt $Data.Length; $guard++) {
        $len = $Data[$Index]
        if ($len -eq 0) { break }
        if ($len -ge 192) { $Index = (($len -band 63) * 256) + $Data[$Index + 1]; continue }
        $labels += [Text.Encoding]::ASCII.GetString($Data, $Index + 1, $len)
        $Index += $len + 1
    }
    return , $labels
}

function Skip-DnsName([byte[]]$Data, [int]$Index) {
    while ($Index -lt $Data.Length -and $Data[$Index] -ne 0) {
        if ($Data[$Index] -ge 192) { return $Index + 2 }
        $Index += $Data[$Index] + 1
    }
    return $Index + 1
}

# The Pi's own name (like moonlight-pi), asked from the Pi itself: Raspberry Pi
# OS answers name questions sent straight to it (mDNS), when routers often
# don't know the name.
function Get-MdnsName([string]$Address) {
    $parts = $Address.Split('.')
    if ($parts.Count -ne 4) { return $null }
    [array]::Reverse($parts)
    $query = New-Object System.Collections.Generic.List[byte]
    $query.AddRange([byte[]](0x4d, 0x50, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0))
    foreach ($label in @($parts) + 'in-addr', 'arpa') {
        $query.Add([byte]$label.Length)
        $query.AddRange([Text.Encoding]::ASCII.GetBytes($label))
    }
    $query.AddRange([byte[]](0, 0, 12, 0, 1))         # PTR record, internet class
    $udp = New-Object System.Net.Sockets.UdpClient
    try {
        $udp.Client.ReceiveTimeout = 1000
        [void]$udp.Send($query.ToArray(), $query.Count, $Address, 5353)
        $from = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
        $r = $udp.Receive([ref]$from)
        $questions = $r[4] * 256 + $r[5]
        if ($r[6] * 256 + $r[7] -lt 1) { return $null }
        $i = 12
        for ($q = 0; $q -lt $questions; $q++) { $i = (Skip-DnsName $r $i) + 4 }
        $i = Skip-DnsName $r $i
        if ($r[$i] * 256 + $r[$i + 1] -ne 12) { return $null }
        $labels = Read-DnsName $r ($i + 10)
        if ($labels.Count -gt 0) { return $labels[0] }
    } catch {
    } finally {
        $udp.Close()
    }
    return $null
}

function Get-PiName([string]$Address, [string]$Mac) {
    $name = Get-MdnsName $Address
    if ($name) { return $name }
    try {
        $task = [System.Net.Dns]::GetHostEntryAsync($Address)
        if ($task.Wait(1500) -and $task.Result.HostName -and $task.Result.HostName -ne $Address) {
            return ($task.Result.HostName -split '\.')[0]
        }
    } catch {}
    if ($Mac -and $RaspberryPiMacPrefixes -contains $Mac.Substring(0, 8).ToUpper()) { return 'Raspberry Pi' }
    return 'USB/IP server'
}

function Get-Mac([string]$Address) {
    $n = Get-NetNeighbor -IPAddress $Address -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($n -and $n.LinkLayerAddress -and $n.LinkLayerAddress -ne '00-00-00-00-00-00') { return $n.LinkLayerAddress }
    return $null
}

# Every USB/IP server on the home network, as objects with name, address and mac.
function Find-Pis {
    $found = @()
    foreach ($base in Get-LocalSubnets) {
        Write-Info "Looking for your Pi on $base.0/24..."
        $clients = foreach ($i in 1..254) {
            $c = New-Object System.Net.Sockets.TcpClient
            [pscustomobject]@{ Ip = "$base.$i"; Client = $c; Task = $c.ConnectAsync("$base.$i", $UsbipPort) }
        }
        try { [System.Threading.Tasks.Task]::WaitAll(@($clients.Task), 2000) | Out-Null } catch {}
        $open = @($clients | Where-Object { $_.Task.Status -eq 'RanToCompletion' } | ForEach-Object { $_.Ip })
        $clients | ForEach-Object { $_.Client.Close() }
        foreach ($ip in $open) {
            if (Test-UsbipServer $ip) {
                $mac = Get-Mac $ip
                $found += [pscustomobject]@{ name = (Get-PiName $ip $mac); address = $ip; mac = $mac }
            }
        }
    }
    return $found
}

# The Pi's Tailscale address, if Tailscale on this PC knows it (for USB sharing
# over Tailscale, which is off unless it's turned on on the TV).
function Find-TailscaleAddress($Pi) {
    if (-not (Test-Path -LiteralPath $TailscaleExe)) { return $null }
    try {
        $status = & $TailscaleExe status --json 2>$null | Out-String | ConvertFrom-Json
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

function Read-UsbConfig {
    try {
        $c = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
        return @($c.pis | Where-Object { $_ -and $_.address })
    } catch {
        return @()
    }
}

function Save-UsbConfig($Pis) {
    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    ([pscustomobject]@{ pis = @($Pis) } | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath $ConfigPath -Encoding UTF8
}

function Read-PiAddress {
    while ($true) {
        $reply = (Read-Host "    Type the Pi's address (on the TV: Settings > USB sharing), or press Enter to skip").Trim()
        if ($reply -eq '') { return $null }
        if (Test-UsbipServer $reply) {
            $mac = Get-Mac $reply
            return [pscustomobject]@{ name = (Get-PiName $reply $mac); address = $reply; mac = $mac }
        }
        Write-Warn "Nothing answered at $reply. Check the address, and that USB sharing is on (Settings > USB sharing)."
    }
}

# Which Pi(s) this PC takes USB devices from: the ones it already knows if they
# still answer, otherwise the ones found on the network (confirmed by you).
function Select-Pis {
    $known = @(Read-UsbConfig | Where-Object { Test-UsbipServer $_.address })
    if ($known.Count -gt 0) {
        foreach ($pi in $known) {
            $mac = Get-Mac $pi.address
            if ($mac) { $pi | Add-Member -NotePropertyName mac -NotePropertyValue $mac -Force }
            $pi | Add-Member -NotePropertyName name -NotePropertyValue (Get-PiName $pi.address $pi.mac) -Force
            Write-Ok "Using $($pi.name) ($($pi.address))"
        }
        return $known
    }
    $found = @(Find-Pis)
    if ($found.Count -eq 1) {
        $pi = $found[0]
        if (Read-YesNo "Found $($pi.name) ($($pi.address)). Use it?" $true) { return @($pi) }
    } elseif ($found.Count -gt 1) {
        Write-Info 'Found more than one:'
        for ($i = 0; $i -lt $found.Count; $i++) {
            Write-Info ('  {0}. {1} ({2})' -f ($i + 1), $found[$i].name, $found[$i].address)
        }
        while ($true) {
            $reply = (Read-Host '    Which one? Type its number, several separated by commas, or press Enter for all').Trim()
            if ($reply -eq '') { return $found }
            $picked = @($reply -split '[,\s]+' | Where-Object { $_ -match '^\d+$' -and [int]$_ -ge 1 -and [int]$_ -le $found.Count } |
                ForEach-Object { $found[[int]$_ - 1] })
            if ($picked.Count -gt 0) { return $picked }
            Write-Info "Please type a number from 1 to $($found.Count)."
        }
    } else {
        Write-Warn "Couldn't find your Pi on this network. Check it's switched on, set up, and that USB"
        Write-Warn 'sharing is on (on the TV: Settings > USB sharing).'
    }
    $pi = Read-PiAddress
    if ($pi) { return @($pi) }
    return @()
}

# ---------------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------------
function Get-HelperProcesses {
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$HelperName*" }
}

function Stop-Helper {
    foreach ($p in Get-HelperProcesses) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Get-HelperArguments {
    "--headless `"$PowerShellExe`" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$HelperPath`""
}

function Start-Helper {
    Start-Process -FilePath $Conhost -ArgumentList (Get-HelperArguments) -WindowStyle Hidden
}

function Install-Helper {
    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    Stop-Helper
    Remove-Item -LiteralPath $HelperLog -Force -ErrorAction SilentlyContinue
    # From next to this script when it's run from a copy of the project, otherwise from GitHub.
    $local = if ($PSScriptRoot) { Join-Path $PSScriptRoot $HelperName } else { $null }
    if ($local -and (Test-Path -LiteralPath $local)) {
        Copy-Item -LiteralPath $local -Destination $HelperPath -Force
    } else {
        Invoke-WebRequest -Uri "$RawUrl/windows/$HelperName" -OutFile $HelperPath -UseBasicParsing
    }
    # Started hidden when you log in: conhost --headless shows no window at all.
    $shell = New-Object -ComObject WScript.Shell
    $s = $shell.CreateShortcut($StartupLink)
    $s.TargetPath = $Conhost
    $s.Arguments = Get-HelperArguments
    $s.WorkingDirectory = $AppDir
    $s.WindowStyle = 7
    $s.Description = 'Moonlight Pi USB sharing (moonlight-pi-setup)'
    $s.Save()
    Start-Helper
    Write-Ok 'The USB sharing helper starts hidden when you log in, and is running now'
}

# Gives back any devices attached from the Pi(s) right away (the Pi would take
# them back by itself anyway, 30 seconds after the helper stops asking).
function Disconnect-PiDevices {
    if (-not (Test-Path -LiteralPath $UsbipExe)) { return }
    $addresses = @(Read-UsbConfig | ForEach-Object { $_.address; $_.tailscale } | Where-Object { $_ })
    $rc, $out = Invoke-Usbip @('port')
    foreach ($m in [regex]::Matches($out, '(?s)Port (\d+):.*?usbip://(\[[^\]]+\]|[^:/\s]+):\d+/(\S+)')) {
        $port, $addr, $id = $m.Groups[1].Value, $m.Groups[2].Value.Trim('[', ']'), $m.Groups[3].Value
        if ($addresses -contains $addr) {
            Invoke-Usbip @('detach', '-p', [string][int]$port) | Out-Null
            Invoke-Usbip @('attach', '-r', $addr, '-b', $id, '--stop') | Out-Null
        }
    }
}

function Remove-UsbSharing {
    Write-Step 'Removing USB sharing from this PC'
    $installedHere = Test-Path -LiteralPath $InstalledMarker
    Stop-Helper
    Disconnect-PiDevices
    Remove-Item -LiteralPath $StartupLink -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $AppDir) { Remove-Item -LiteralPath $AppDir -Recurse -Force }
    Write-Ok 'Removed the USB sharing helper'
    if (-not (Get-UsbipInstalledVersion)) { return }
    $others = Get-OtherUsbipUsers
    if (-not $installedHere) {
        Write-Info "usbip-win2 was on this PC before USB sharing was set up, so it's left installed."
    } elseif ($others) {
        Write-Info "usbip-win2 stays installed, because $($others -join ' and ') uses it too."
    } else {
        Uninstall-Usbip
    }
}

# Waits for the helper to reach the Pi (it logs when it does).
function Test-HelperConnected($Pis) {
    for ($i = 0; $i -lt 16; $i++) {
        if (Test-Path -LiteralPath $HelperLog) {
            $log = Get-Content -LiteralPath $HelperLog -Raw
            $done = @($Pis | Where-Object { $log -match [regex]::Escape("Connected to $($_.name) at") })
            if ($done.Count -eq @($Pis).Count) { return $true }
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

# ---------------------------------------------------------------------------
# USB sharing
# ---------------------------------------------------------------------------
function Set-UpUsbSharing {
    Write-Step 'USB sharing'
    if (Test-Path -LiteralPath $HelperPath) {
        Write-Info 'USB sharing is set up on this PC.'
        $reply = (Read-Host '    Press Enter to check and repair it, or type R to remove it from this PC').Trim().ToLower()
        if ($reply -in 'r', 'remove') {
            Remove-UsbSharing
            return
        }
    } else {
        Write-Info 'USB devices plugged into the Pi, like a wired controller, can come to this PC while'
        Write-Info "you stream, as if they were plugged in here. This installs usbip-win2 (free and open"
        Write-Info 'source), which needs administrator permission once.'
        if (-not (Read-YesNo 'Share USB devices plugged into the Pi with this PC?' $true)) {
            Write-Info 'Skipped. Run this script again any time to set it up.'
            return
        }
    }
    Test-WindowsVersion
    Install-Usbip
    $pis = @(Select-Pis)
    if ($pis.Count -eq 0) {
        Write-Warn 'No Pi chosen, so USB sharing is not set up yet. Run this script again when the Pi is on.'
        return
    }
    foreach ($pi in $pis) {
        $ts = Find-TailscaleAddress $pi
        if ($ts) { $pi | Add-Member -NotePropertyName tailscale -NotePropertyValue $ts -Force }
    }
    Save-UsbConfig $pis
    Install-Helper
    if (Test-HelperConnected $pis) {
        foreach ($pi in $pis) {
            Write-Ok "Connected to $($pi.name). Its USB devices come to this PC while you stream."
        }
        Write-Info 'Choose which devices go to the PC on the TV: Settings > USB sharing (controllers'
        Write-Info 'and headsets go by themselves).'
    } else {
        Write-Warn "The helper hasn't reached the Pi yet. Its log: $HelperLog"
    }
}

# ---------------------------------------------------------------------------
# Sign-in screens
# ---------------------------------------------------------------------------
function ConvertTo-Quoted([string]$Text) { return "'" + $Text.Replace("'", "''") + "'" }

# Runs PowerShell commands as administrator (Windows asks for permission once).
# Returns $true if they finished; otherwise what went wrong is in $script:ElevatedError.
function Invoke-Elevated([string]$Commands) {
    $result = Join-Path $env:TEMP ('moonlight-setup-{0}.txt' -f [guid]::NewGuid().ToString('N'))
    $q = ConvertTo-Quoted $result
    $script = "`$ErrorActionPreference = 'Stop'`ntry {`n$Commands`nSet-Content -LiteralPath $q -Value 'OK'`n} " +
              "catch {`nSet-Content -LiteralPath $q -Value `$_.Exception.Message`n}"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
    try {
        Start-Process -FilePath $PowerShellExe -Verb RunAs -WindowStyle Hidden -Wait `
            -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded
    } catch {
        $script:ElevatedError = "Windows didn't get permission."
        return $false
    }
    $text = 'It stopped before finishing.'
    if (Test-Path -LiteralPath $result) {
        $text = "$(Get-Content -LiteralPath $result -Raw)".Trim()
        Remove-Item -LiteralPath $result -Force -ErrorAction SilentlyContinue
    }
    $script:ElevatedError = $text
    return ($text -eq 'OK')
}

function Test-TailscaleIp([string]$Address) {
    return $Address -match '^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.'
}

# The Pis that answer the sign-in screens' "hello" (UDP port 3241, answered
# while Controllers > Sign-in screens is on): on the home network, and at the
# addresses given. Objects with name, address and tailscale.
function Find-PisByHello([string[]]$Known) {
    $udp = New-Object System.Net.Sockets.UdpClient(0)
    $found = @{}
    try {
        $udp.EnableBroadcast = $true
        try { [void]$udp.Client.IOControl(-1744830452, [byte[]](0, 0, 0, 0), $null) } catch {}
        $udp.Client.ReceiveTimeout = 250
        $hello = [Text.Encoding]::ASCII.GetBytes('moonlight-pi-setup hello 1')
        $targets = @(Get-LocalSubnets | ForEach-Object { "$_.255" }) + @($Known | Where-Object { $_ }) + '255.255.255.255'
        foreach ($t in ($targets | Sort-Object -Unique)) {
            try { [void]$udp.Send($hello, $hello.Length, $t, $AgentPort) } catch {}
        }
        $deadline = (Get-Date).AddMilliseconds(1500)
        while ((Get-Date) -lt $deadline) {
            $from = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
            try { $data = $udp.Receive([ref]$from) } catch { continue }
            $text = [Text.Encoding]::ASCII.GetString($data)
            $re = '^moonlight-pi-setup pi 1(?: name=([A-Za-z0-9._-]{1,63}))?(?: tailscale=(100\.[0-9.]{3,11}))?\s*$'
            if ($text -match $re) {
                $found[$from.Address.ToString()] = [pscustomobject]@{
                    name = "$($Matches[1])"; address = $from.Address.ToString(); tailscale = "$($Matches[2])" }
            }
        }
    } finally {
        $udp.Close()
    }
    return @($found.Values)
}

# The Pis the service starts with, the first time (it finds Pis, and keeps
# their addresses up to date, by itself): USB sharing's, and the ones that
# answered. Lines of the service's settings file.
function Get-AgentPis($Answered) {
    $pis = [ordered]@{}
    foreach ($pi in @(Read-UsbConfig) + @($Answered)) {
        $name = ("$($pi.name)" -replace '[^A-Za-z0-9._-]', '-').Trim('-')
        $pis["$($pi.address)"] = '{0} {1} {2}' -f $(if ($name) { $name } else { $pi.address }), $pi.address,
            $(if ($pi.tailscale) { $pi.tailscale } else { '-' })
    }
    return @($pis.Values)
}

function Get-AgentSource {
    $dir = Join-Path $env:TEMP ('moonlight-agent-{0}' -f [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $dir | Out-Null
    $path = Join-Path $dir $AgentSourceName
    # From next to this script when it's run from a copy of the project, otherwise from GitHub.
    $local = if ($PSScriptRoot) { Join-Path $PSScriptRoot $AgentSourceName } else { $null }
    if ($local -and (Test-Path -LiteralPath $local)) {
        Copy-Item -LiteralPath $local -Destination $path
    } else {
        Invoke-WebRequest -Uri "$RawUrl/windows/$AgentSourceName" -OutFile $path -UseBasicParsing
    }
    return $path
}

function Install-SecureScreenAgent {
    $source = Get-AgentSource
    $known = @(Read-UsbConfig | ForEach-Object { $_.address; $_.tailscale } | Where-Object { $_ })
    $answered = @(Find-PisByHello $known | Where-Object { -not (Test-TailscaleIp $_.address) })
    $conf = Join-Path (Split-Path -Parent $source) 'secure-screen.conf'
    $lines = @('# The Moonlight Pis this PC tells about its sign-in and UAC screens:',
               '# name, home address, Tailscale address (- if none). Kept up to date by the service.')
    Set-Content -LiteralPath $conf -Value ($lines + @(Get-AgentPis $answered)) -Encoding ASCII
    Write-Info 'Installing the service (Windows asks for permission)...'
    $commands = @"
`$name = $(ConvertTo-Quoted $AgentService)
`$dir = $(ConvertTo-Quoted $AgentDir)
`$data = $(ConvertTo-Quoted $AgentDataDir)
`$exe = $(ConvertTo-Quoted $AgentExe)
`$cs = Join-Path `$dir $(ConvertTo-Quoted $AgentSourceName)
`$svc = Get-Service -Name `$name -ErrorAction SilentlyContinue
if (`$svc -and `$svc.Status -ne 'Stopped') { Stop-Service -Name `$name -Force; `$svc.WaitForStatus('Stopped', '00:00:20') }
try {
New-Item -ItemType Directory -Force -Path `$dir | Out-Null
Copy-Item -LiteralPath $(ConvertTo-Quoted $source) -Destination `$cs -Force
`$csc = Join-Path ([Runtime.InteropServices.RuntimeEnvironment]::GetRuntimeDirectory()) 'csc.exe'
`$ErrorActionPreference = 'Continue'
`$out = & `$csc /nologo /optimize /target:exe "/out:`$exe" /r:System.Management.dll /r:System.ServiceProcess.dll `$cs 2>&1 | Out-String
`$rc = `$LASTEXITCODE
`$ErrorActionPreference = 'Stop'
if (`$rc -ne 0) { throw "The service didn't compile: `$out" }
# Its data folder: only administrators and Windows itself may change it, as the service runs as SYSTEM.
if (Test-Path -LiteralPath `$data) {
    `$owner = (Get-Acl -LiteralPath `$data).GetOwner([Security.Principal.SecurityIdentifier]).Value
    if (`$owner -ne 'S-1-5-32-544' -and `$owner -ne 'S-1-5-18') { Remove-Item -LiteralPath `$data -Recurse -Force }
}
New-Item -ItemType Directory -Force -Path `$data | Out-Null
& icacls.exe `$data /setowner '*S-1-5-32-544' /T /C /Q | Out-Null
# The folder's own permissions (not inherited from ProgramData, where users may create files)...
& icacls.exe `$data /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' /C /Q | Out-Null
if (`$LASTEXITCODE -ne 0) { throw "Couldn't protect `$data" }
# ...which everything in it inherits, and nothing else.
if (Get-ChildItem -LiteralPath `$data -Force) { & icacls.exe (Join-Path `$data '*') /reset /T /C /Q | Out-Null }
# The Pis it knows (only the first time: the service keeps the list up to date).
`$known = Join-Path `$data 'secure-screen.conf'
if (-not (Test-Path -LiteralPath `$known)) { Copy-Item -LiteralPath $(ConvertTo-Quoted $conf) -Destination `$known }
if (-not `$svc) {
    New-Service -Name `$name -BinaryPathName ('"' + `$exe + '"') -DisplayName 'Moonlight Pi secure screens' -StartupType Automatic ``
        -Description 'Tells your Moonlight Pi when Windows shows its lock, sign-in or UAC screens, so every controller works on them (moonlight-pi-setup).' | Out-Null
}
& sc.exe failure `$name reset= 86400 actions= restart/60000/restart/60000/restart/60000 | Out-Null
} finally {
    # Running again even if something above failed (the service that was there before).
    if (Get-Service -Name `$name -ErrorAction SilentlyContinue) { Start-Service -Name `$name -ErrorAction SilentlyContinue }
}
if ((Get-Service -Name `$name).Status -ne 'Running') { throw "The service didn't start." }
"@
    $ok = Invoke-Elevated $commands
    Remove-Item -LiteralPath (Split-Path -Parent $source) -Recurse -Force -ErrorAction SilentlyContinue
    if (-not $ok) { throw "The sign-in screens service wasn't installed: $script:ElevatedError" }
    Write-Ok 'Installed the service. It runs from when Windows starts, before anyone signs in.'
    foreach ($pi in $answered) { Write-Ok "$($pi.name) ($($pi.address)) answered: it's ready" }
    if (-not $answered) {
        Write-Warn "No Pi answered on this network. On the TV, check Controllers > Sign-in screens is on:"
        Write-Warn 'the service finds the Pi by itself once it is.'
    }
}

function Remove-SecureScreenAgent {
    Write-Info 'Removing the service (Windows asks for permission)...'
    $commands = @"
`$name = $(ConvertTo-Quoted $AgentService)
`$svc = Get-Service -Name `$name -ErrorAction SilentlyContinue
if (`$svc) {
    if (`$svc.Status -ne 'Stopped') { Stop-Service -Name `$name -Force; `$svc.WaitForStatus('Stopped', '00:00:20') }
    & sc.exe delete `$name | Out-Null
}
foreach (`$d in $(ConvertTo-Quoted $AgentDir), $(ConvertTo-Quoted $AgentDataDir)) {
    if (Test-Path -LiteralPath `$d) { Remove-Item -LiteralPath `$d -Recurse -Force }
}
"@
    if (Invoke-Elevated $commands) {
        Write-Ok 'Removed the sign-in screens service'
    } else {
        Write-Warn "It wasn't removed: $script:ElevatedError"
    }
}

function Set-UpSignInScreens {
    Write-Step 'Controllers on sign-in screens'
    if (Get-Service -Name $AgentService -ErrorAction SilentlyContinue) {
        Write-Info 'Every controller on the Pi works on Windows'' lock, sign-in and UAC screens: this is set up.'
        $reply = (Read-Host '    Press Enter to check and repair it, or type R to remove it from this PC').Trim().ToLower()
        if ($reply -in 'r', 'remove') {
            Remove-SecureScreenAgent
            return
        }
    } else {
        Write-Info "Windows' lock screen, sign-in screen and UAC prompts only work with Xbox controllers."
        Write-Info 'With this, every other controller on the Pi (PlayStation, Switch and others) also shows'
        Write-Info 'up here as an Xbox controller while one of those screens is up during a stream. It'
        Write-Info 'installs a small Windows service, which needs administrator permission once.'
        if (-not (Read-YesNo 'Let every controller use these screens?' $true)) {
            Write-Info 'Skipped. Run this script again any time to set it up.'
            return
        }
    }
    Install-SecureScreenAgent
}

# ---------------------------------------------------------------------------
# Tailscale
# ---------------------------------------------------------------------------
function Test-TailscaleInstalled {
    return [bool](Get-Service -Name 'Tailscale' -ErrorAction SilentlyContinue) -or
        (Test-Path -LiteralPath $TailscaleExe)
}

function Install-Tailscale {
    Write-Step 'Tailscale'
    if (Test-TailscaleInstalled) {
        Write-Ok 'Tailscale is already installed'
        return
    }
    Write-Info 'Tailscale lets you stream when you are away from home. The Pi already has it;'
    Write-Info 'install it here too if you want to stream from somewhere else.'
    if (-not (Read-YesNo 'Install Tailscale?' $false)) { return }

    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Info 'Installing Tailscale with winget (Windows will ask for permission)...'
        winget install --id Tailscale.Tailscale --exact --source winget
    } else {
        Write-Info "Downloading Tailscale from $TailscaleInstallerUrl"
        $tmp = Join-Path $env:TEMP 'tailscale-setup-latest.exe'
        Invoke-WebRequest -Uri $TailscaleInstallerUrl -OutFile $tmp -UseBasicParsing
        if (-not (Test-Signature $tmp $TailscaleSigner)) {
            Remove-Item -LiteralPath $tmp -Force
            throw "The downloaded file isn't signed by $TailscaleSigner, so it wasn't run."
        }
        Start-Process -FilePath $tmp -Wait
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
    if (Test-TailscaleInstalled) {
        Write-Ok 'Tailscale installed'
        Write-Info 'Sign in from the Tailscale icon in the taskbar, using the same account as the Pi.'
    } else {
        Write-Warn 'Tailscale was not installed. You can get it from https://tailscale.com/download'
    }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
function Invoke-MoonlightPcSetup {
    Write-Host "Moonlight PC Setup $PcSetupVersion (for moonlight-pi-setup)" -ForegroundColor White
    try {
        Set-UpUsbSharing
        Set-UpSignInScreens
        Install-Tailscale
    } catch {
        Write-Host ''
        Write-Host "    [ERROR] $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "    If you think this is a bug, please report it at: $IssuesUrl"
        return
    }

    Write-Step 'All done'
    if ($script:RestartNeeded) {
        Write-Info 'Windows needs a restart to finish installing (or removing) the USB driver.'
        if (Read-YesNo 'Restart now?' $false) {
            Restart-Computer
        } else {
            Write-Info 'Restart when it suits you.'
        }
    }
}

Invoke-MoonlightPcSetup
