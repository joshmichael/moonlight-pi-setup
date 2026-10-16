# moonlight-pc-setup.ps1
#
# Sets up the gaming PC to go with moonlight-pi-setup:
#   - USB sharing (optional): USB devices plugged into the Pi, like a wired
#     controller, come to this PC while you stream, as if they were plugged in
#     here. Installs usbip-win2 (the Windows side of USB/IP), finds the Pi, and
#     installs a small helper that starts hidden when you log in.
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

$PcSetupVersion = '2.0.0'
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
$StartupLink = Join-Path ([Environment]::GetFolderPath('Startup')) 'Moonlight Pi USB sharing.lnk'
$Conhost = Join-Path $env:SystemRoot 'System32\conhost.exe'
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

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

# Runs usbip.exe and returns what it printed. (Windows PowerShell turns a
# program's error output into errors, which would stop this script.)
function Invoke-Usbip([string[]]$Arguments) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        return ((& $UsbipExe @Arguments 2>&1 | ForEach-Object { "$_" }) -join "`n")
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

function Install-Usbip {
    $current = Get-UsbipInstalledVersion
    if ($current -eq $UsbipVersion) {
        Write-Ok "usbip-win2 $UsbipVersion is installed"
        return
    }
    $arch = Get-WindowsArchitecture
    $pkg = $UsbipInstallers[$arch]
    if ($current) {
        Write-Info "usbip-win2 $current is installed; this setup is tested with $UsbipVersion, so it updates it."
    }
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
    $script:RestartNeeded = $true
    Write-Ok "Installed usbip-win2 $UsbipVersion"
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
function Test-UsbipServer([string]$Address) {
    if (-not (Test-UsbipPort $Address)) { return $false }
    return ((Invoke-Usbip @('list', '-r', $Address)) -match 'Exportable USB devices|no exportable devices')
}

# The home network's addresses to look through: this PC's own network, but not
# Tailscale or virtual networks (WSL, Hyper-V). At most the 254 addresses
# around this PC's own one.
function Get-LocalSubnets {
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.PrefixOrigin -in 'Dhcp', 'Manual' -and $_.IPAddress -notlike '169.254.*' -and
                       $_.InterfaceAlias -notmatch 'Tailscale|vEthernet|Loopback|Bluetooth' } |
        ForEach-Object { ($_.IPAddress -split '\.')[0..2] -join '.' } | Sort-Object -Unique
}

function Get-PiName([string]$Address, [string]$Mac) {
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
        foreach ($pi in $known) { Write-Ok "Using $($pi.name) ($($pi.address))" }
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

function Remove-UsbSharing {
    Write-Step 'Removing USB sharing from this PC'
    Stop-Helper
    Remove-Item -LiteralPath $StartupLink -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $AppDir) { Remove-Item -LiteralPath $AppDir -Recurse -Force }
    Write-Ok 'Removed the USB sharing helper'
    Uninstall-Usbip
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
