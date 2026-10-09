# moonlight-pc-setup.ps1
#
# Sets up the gaming PC to go with moonlight-pi-setup:
#   - VirtualHere client: uses the one already installed, or downloads the
#     official one; makes it start with Windows; and makes it look for the Pi
#     every 5 seconds, so USB devices reach the PC within seconds of a stream
#     starting.
#   - Tailscale (optional), to stream when you're away from home.
#
# Run it in PowerShell on the gaming PC as your normal user (not as admin):
#   irm https://raw.githubusercontent.com/joshmichael/moonlight-pi-setup/main/windows/moonlight-pc-setup.ps1 | iex
#
# Safe to run again: it only changes what isn't set up yet.

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$PcSetupVersion = '1.0.0'
$IssuesUrl = 'https://github.com/joshmichael/moonlight-pi-setup/issues'

$VhDownloadUrl = 'https://www.virtualhere.com/sites/default/files/usbclient/vhui64.exe'
$VhSigner = 'VirtualHere Pty. Ltd.'
$VhInstallDir = Join-Path $env:LOCALAPPDATA 'Programs\VirtualHere'
$VhIni = Join-Path $env:APPDATA 'vhui.ini'
$VhRefreshSeconds = 5
$StartupDir = [Environment]::GetFolderPath('Startup')
$StartupLink = Join-Path $StartupDir 'VirtualHere Client.lnk'

$TailscaleInstallerUrl = 'https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe'
$TailscaleSigner = 'Tailscale Inc.'

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

# ---------------------------------------------------------------------------
# VirtualHere client
# ---------------------------------------------------------------------------

# Paths of vhui64.exe that Windows starts at login (Startup folder shortcuts
# and the Run key).
function Get-VhStartupTargets {
    $targets = @()
    $shell = New-Object -ComObject WScript.Shell
    foreach ($lnk in Get-ChildItem -Path $StartupDir -Filter '*.lnk' -ErrorAction SilentlyContinue) {
        $target = $shell.CreateShortcut($lnk.FullName).TargetPath
        if ($target -like '*\vhui64.exe') { $targets += $target }
    }
    $run = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -ErrorAction SilentlyContinue
    if ($run) {
        foreach ($p in $run.PSObject.Properties) {
            if ("$($p.Value)" -match '"?([^"]*\\vhui64\.exe)') { $targets += $Matches[1] }
        }
    }
    return $targets
}

# The VirtualHere client the user already has, if any: the one running now,
# then the one Windows starts at login, then the usual install places.
function Find-VhClient {
    $candidates = @()
    $candidates += Get-CimInstance Win32_Process -Filter "Name='vhui64.exe'" |
        Where-Object { $_.ExecutablePath } | ForEach-Object { $_.ExecutablePath }
    $candidates += Get-VhStartupTargets
    $candidates += 'C:\Program Files\VirtualHere Client\vhui64.exe'
    $candidates += Join-Path $VhInstallDir 'vhui64.exe'
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) { return (Resolve-Path -LiteralPath $c).Path }
    }
    return $null
}

function Test-Signature([string]$Path, [string]$Signer) {
    $sig = Get-AuthenticodeSignature -LiteralPath $Path
    return ($sig.Status -eq 'Valid' -and $sig.SignerCertificate.Subject -like "CN=$Signer,*")
}

function Install-VhClient {
    Write-Info "Downloading the VirtualHere client from $VhDownloadUrl"
    New-Item -ItemType Directory -Force -Path $VhInstallDir | Out-Null
    $tmp = Join-Path $VhInstallDir 'vhui64.exe.download'
    Invoke-WebRequest -Uri $VhDownloadUrl -OutFile $tmp -UseBasicParsing
    if (-not (Test-Signature $tmp $VhSigner)) {
        Remove-Item -LiteralPath $tmp -Force
        throw "The downloaded file isn't signed by $VhSigner, so it wasn't installed."
    }
    $exe = Join-Path $VhInstallDir 'vhui64.exe'
    Move-Item -LiteralPath $tmp -Destination $exe -Force
    Write-Ok "Installed the VirtualHere client to $exe"
    return $exe
}

function Get-VhClientProcess([string]$Exe) {
    Get-CimInstance Win32_Process -Filter "Name='vhui64.exe'" |
        Where-Object { $_.ExecutablePath -eq $Exe -and $_.CommandLine -notmatch '\s-t\s' }
}

function Start-VhClient([string]$Exe) {
    Start-Process -FilePath $Exe -WorkingDirectory (Split-Path $Exe) -WindowStyle Minimized
}

# Close the client so its settings file can be edited (it rewrites the file
# when it exits). Asks the user to close it if it doesn't go by itself, for
# example because it's asking whether to stop using a device.
function Stop-VhClient([string]$Exe) {
    if (-not (Get-VhClientProcess $Exe)) { return }
    $sender = Start-Process -FilePath $Exe -ArgumentList '-t', 'EXIT' -PassThru -WindowStyle Hidden
    for ($i = 0; $i -lt 20 -and (Get-VhClientProcess $Exe); $i++) { Start-Sleep -Milliseconds 500 }
    if (-not $sender.HasExited) { $sender | Stop-Process -Force -ErrorAction SilentlyContinue }
    while (Get-VhClientProcess $Exe) {
        Write-Warn 'VirtualHere is still open (it may be asking about a device that is in use).'
        Read-Host '    Close VirtualHere (right-click its tray icon > Exit), then press Enter' | Out-Null
    }
}

# Returns the ini text with AutoRefreshLookupPeriod set, keeping the rest of
# the file (and its Windows line endings) as it was.
function Set-IniRefreshPeriod([string]$Text, [int]$Seconds) {
    $line = "AutoRefreshLookupPeriod=$Seconds"
    $lines = @()
    if ($Text) { $lines = @($Text -split "\r?\n") }
    if ($lines.Count -gt 0 -and $lines[-1] -eq '') { $lines = $lines[0..($lines.Count - 2)] }
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^AutoRefreshLookupPeriod=') { $lines[$i] = $line; $found = $true }
    }
    if (-not $found) {
        $general = [array]::IndexOf($lines, '[General]')
        if ($general -ge 0) {
            $before = $lines[0..$general]
            $after = if ($general + 1 -lt $lines.Count) { $lines[($general + 1)..($lines.Count - 1)] } else { @() }
            $lines = @($before) + $line + @($after)
        } else {
            $lines += '[General]', $line
        }
    }
    return (($lines -join "`r`n") + "`r`n")
}

function Get-IniRefreshPeriod {
    if (-not (Test-Path -LiteralPath $VhIni)) { return $null }
    $m = Select-String -LiteralPath $VhIni -Pattern '^AutoRefreshLookupPeriod=(\d+)' | Select-Object -First 1
    if ($m) { return [int]$m.Matches[0].Groups[1].Value }
    return $null
}

function Set-VhRefreshPeriod([string]$Exe) {
    $current = Get-IniRefreshPeriod
    if ($current -eq $VhRefreshSeconds) {
        Write-Ok "VirtualHere already looks for the Pi every $VhRefreshSeconds seconds"
        return
    }
    $wasRunning = [bool](Get-VhClientProcess $Exe)
    Stop-VhClient $Exe
    $text = ''
    if (Test-Path -LiteralPath $VhIni) {
        Copy-Item -LiteralPath $VhIni -Destination "$VhIni.bak" -Force
        $text = [IO.File]::ReadAllText($VhIni)
    }
    [IO.File]::WriteAllText($VhIni, (Set-IniRefreshPeriod $text $VhRefreshSeconds), [Text.Encoding]::ASCII)
    $was = if ($current) { "$current" } else { '30 (the default)' }
    Write-Ok "VirtualHere will look for the Pi every $VhRefreshSeconds seconds (was $was)"
    if ($wasRunning) { Start-VhClient $Exe }
}

function Set-VhStartup([string]$Exe) {
    $existing = @(Get-VhStartupTargets)
    if ($existing.Count -gt 0) {
        Write-Ok 'VirtualHere already starts with Windows'
        if ($existing -notcontains $Exe) {
            Write-Info "(using $($existing[0]))"
        }
        return
    }
    $shell = New-Object -ComObject WScript.Shell
    $s = $shell.CreateShortcut($StartupLink)
    $s.TargetPath = $Exe
    $s.WorkingDirectory = Split-Path $Exe
    $s.WindowStyle = 7   # minimised
    $s.Description = 'VirtualHere client (moonlight-pi-setup)'
    $s.Save()
    Write-Ok 'VirtualHere will start with Windows'
}

function Install-VirtualHere {
    Write-Step 'VirtualHere client'
    $exe = Find-VhClient
    $isNew = $false
    if ($exe) {
        Write-Ok "Using the VirtualHere client at $exe"
    } else {
        $exe = Install-VhClient
        $isNew = $true
    }
    Set-VhStartup $exe
    Set-VhRefreshPeriod $exe
    if (-not (Get-VhClientProcess $exe)) {
        Start-VhClient $exe
        Write-Ok 'Started the VirtualHere client'
    }
    if ($isNew) {
        Write-Info 'The first time it runs, VirtualHere asks for permission to install its USB'
        Write-Info 'driver. Say Yes, or USB devices from the Pi will not work.'
    }
}

# ---------------------------------------------------------------------------
# Tailscale
# ---------------------------------------------------------------------------
function Test-TailscaleInstalled {
    return [bool](Get-Service -Name 'Tailscale' -ErrorAction SilentlyContinue) -or
        (Test-Path 'C:\Program Files\Tailscale\tailscale.exe')
}

function Install-Tailscale {
    Write-Step 'Tailscale'
    if (Test-TailscaleInstalled) {
        Write-Ok 'Tailscale is already installed'
        return
    }
    Write-Info 'Tailscale lets you stream when you are away from home. Only install it if you'
    Write-Info 'also installed it on the Pi.'
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
        Install-VirtualHere
        Install-Tailscale
    } catch {
        Write-Host ''
        Write-Host "    [ERROR] $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "    If you think this is a bug, please report it at: $IssuesUrl"
        return
    }

    Write-Step 'All done'
    Write-Info 'Last step, in VirtualHere: choose which USB devices move from the Pi to this PC.'
    Write-Info 'If the Pi shares devices only while streaming, start a stream first, so the'
    Write-Info "devices appear. Then right-click a device and choose Auto-Use Device (it moves"
    Write-Info 'whichever USB port it is in) or Auto-Use Port (anything in that port moves).'
    Write-Info 'From then on it moves to this PC by itself whenever you stream.'
}

# Set MPS_PC_SETUP_NO_MAIN=1 to load the functions without running (for testing).
if (-not $env:MPS_PC_SETUP_NO_MAIN) { Invoke-MoonlightPcSetup }
