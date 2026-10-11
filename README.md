# Moonlight Pi Setup

Turns a **Raspberry Pi 5** into a [Moonlight](https://moonlight-stream.org) game-streaming box for your TV. The Pi boots straight into Moonlight, with hardware HEVC decoding, HDR and surround sound, and has a menu on the TV for controllers, Wi-Fi and settings, so you rarely need a keyboard.

Tested on a Pi 5 (8 GB) over wired gigabit Ethernet: **2560×1440 at 60 fps, HEVC 10-bit HDR, 5.1 surround**, about 3.5 ms decode time and no dropped frames.

## Requirements

- A **Raspberry Pi 5** (any RAM size).
- **Raspberry Pi OS Lite (64-bit)**, based on Debian 13 "Trixie". In Raspberry Pi Imager it's under *Raspberry Pi OS (other)*. Turn on SSH in Imager's settings. The desktop version isn't supported.
- A TV on either HDMI port. Sound follows whichever port has a screen connected.
- **Wired Ethernet** is strongly recommended. Wi-Fi works too.
- A gaming PC running [Sunshine](https://github.com/LizardByte/Sunshine), [Apollo](https://github.com/ClassicOldSong/Apollo) or [Vibepollo](https://github.com/Nonary/Vibepollo).

## Installing

Connect to the Pi over SSH and run this as your normal user (not with `sudo`). It asks for your password if it needs it.

```bash
curl -fsSL https://raw.githubusercontent.com/joshmichael/moonlight-pi-setup/main/moonlight-pi-setup.sh | bash
```

To read the script first:

```bash
curl -fsSLO https://raw.githubusercontent.com/joshmichael/moonlight-pi-setup/main/moonlight-pi-setup.sh
less moonlight-pi-setup.sh
bash moonlight-pi-setup.sh
```

The script asks no questions. It shows a summary, asks **Go ahead?**, installs everything, and offers to restart. It installs Moonlight, the TV menu, [Tailscale](#streaming-away-from-home-tailscale) and [VirtualHere](#sharing-usb-devices-with-your-pc-virtualhere), and sets the Pi to start Moonlight when it boots. Your settings are chosen on the TV afterwards.

Running the script again updates or repairs the setup and keeps your settings. You can also update from the TV (**Settings → Updates**).

## First start

After the restart, **Quick setup** opens on the TV. It asks one question per screen and applies each answer straight away:

- the Wi-Fi country, if it isn't set (Raspberry Pi OS keeps Wi-Fi off until it is)
- your speakers (stereo, 5.1 or 7.1), with a speaker test
- whether to end the stream on your PC when the Pi shuts down
- whether to hide boot messages
- Wi-Fi power saving (only on Wi-Fi)
- when VirtualHere sends USB devices to your PC
- whether to sign in to Tailscale now, by scanning a code with your phone (only if you're online)

You can skip it, and run it again from **Settings**. Until then the recommended settings are used.

Then pair Moonlight with your PC (enter the PIN in your streaming host's web page) and set these in Moonlight's settings:

| Setting | Recommended |
|---|---|
| Video codec | **HEVC**. The Pi 5 decodes it in hardware, but not H.264 |
| Resolution | Your TV's, or 1440p / 1080p |
| Frame rate | 60 FPS |
| Bitrate | 50–100 Mbit/s on wired Ethernet |
| V-Sync | On. Off saves a few milliseconds, but may tear |
| Audio | Already set to match your speakers. Set Windows to the same layout |

During a stream, **Ctrl+Alt+Shift+S** shows the performance overlay, and **Ctrl+Alt+Shift+Q** (or Select+Start+L1+R1 on a controller) ends it.

## The TV menu

The menu opens whenever Moonlight closes: on Moonlight's list of PCs, press **B** (Circle on PlayStation) or **Esc** and confirm. At boot it opens first if the Pi isn't connected to a network, has no controller connected or paired, or hasn't had its Quick setup.

Use a controller (D-pad or left stick, A to select, B to go back), a keyboard (arrow keys, Enter, Esc) or a mouse. Button hints match the controller you're using.

| Item | What it does |
|---|---|
| **Start Moonlight** | Back to Moonlight (or press Start) |
| **Controllers** | Pair Bluetooth controllers, and connect, disconnect or forget them. Shows battery levels |
| **Wi-Fi & network** | Network status, Wi-Fi on or off, choosing a network (with an on-screen keyboard), setting up Wi-Fi from your phone, and saved networks |
| **Settings** | See [Settings](#settings) |
| **Restart or shut down** | Restarts or shuts down the Pi |
| **Command line** | Leaves the menu for a text command line (needs a keyboard). Type `exit` to come back |

**Pairing a controller:** choose **Controllers → Pair a new controller** and put the controller in pairing mode. On a DualSense or DualShock 4, hold **Create/Share + PS**. On an Xbox controller, hold the pair button on top. If no controller is connected or paired at all, the menu pairs the first one it finds by itself, so you can set up a new Pi without a keyboard.

**Wi-Fi from your phone:** choose **Wi-Fi & network → Set up Wi-Fi from your phone**. It also starts by itself when the Pi boots without a network. The Pi starts its own Wi-Fi network and shows a QR code. Scan it, and a setup page opens on your phone (or open `http://10.42.0.1`). Choose your Wi-Fi and type its password. If the password was wrong, the setup network comes back so you can try again. It has its own password, shown only on the TV, and switches off after 15 minutes.

### Settings

| Setting | What it does |
|---|---|
| **Speakers** | Stereo, 5.1 or 7.1 surround, and a speaker test. Moonlight's own audio setting is changed to match |
| **End stream on PC at shutdown** | See [below](#ending-the-stream-when-the-pi-is-switched-off). On by default |
| **VirtualHere** | When VirtualHere sends USB devices to your PC, and whether it works over Tailscale. See [below](#sharing-usb-devices-with-your-pc-virtualhere) |
| **Tailscale** | Whether Tailscale is connected, and whether Moonlight can reach your PC through it. Sign in, turn it off or on, or sign out. Installs Tailscale if it's missing |
| **Wi-Fi power saving** | Power saving can make Wi-Fi doze between packets, which causes stutter and lag spikes. Off by default (recommended) |
| **Wi-Fi country** | The country whose Wi-Fi channels the Pi uses |
| **Hide boot messages** | Hides the text and splash screen while the Pi starts. On by default. Takes effect after a restart |
| **Updates** | Installs new versions of Moonlight and Raspberry Pi OS (`apt full-upgrade`), and updates this setup from GitHub, keeping your settings |
| **About this Pi** | Network addresses, the SSH command, versions, temperature, power supply warnings and free space |
| **Run Quick setup again** | Goes through the first-time questions again |
| **Uninstall this setup** | See [Uninstalling](#uninstalling) |

## Ending the stream when the Pi is switched off

If you switch the Pi off without ending the stream in Moonlight, your PC keeps the stream and the game running, so you can reconnect later. With a virtual display (Apollo or Vibepollo) this can leave the PC stuck on the stream.

With **End stream on PC at shutdown** on, the Pi tells each PC it's paired with to end its stream as it shuts down. It waits up to 10 seconds per PC, so shutdown is never held up for long.

- This **closes the game you're streaming**, like choosing Quit in Moonlight, so save first. It also ends a stream someone else is running from one of your paired PCs.
- To try it without shutting down, start a stream and run `moonlight-quit-all` over SSH.
- What happened at recent shutdowns: `cat ~/.local/share/moonlight-pi-setup/quit-on-shutdown.log`

## Streaming away from home (Tailscale)

[Tailscale](https://tailscale.com) connects the Pi to your PC over the internet without opening ports on your router.

1. Install Tailscale on your gaming PC and sign in. The [PC setup script](#setting-up-the-gaming-pc) can install it.
2. Sign the Pi in on the TV: **Settings → Tailscale → Sign in**, then scan the code with your phone. Use the same account as on the PC.
3. While you're still at home, turn off key expiry for the Pi and the PC in the [Tailscale admin console](https://login.tailscale.com/admin/machines) (the **…** menu next to each machine → **Disable key expiry**). Otherwise Tailscale signs them out after 180 days.

**Moonlight finds your PC over Tailscale by itself.** Moonlight only knows your PC's home address, so away from home the PC would look offline. The Pi looks for each PC you've paired among your Tailscale devices and saves its Tailscale address in Moonlight as an extra address. It does this when the Pi starts, when the menu opens, and after you sign in, as long as the PC is on and signed in to Tailscale. **Settings → Tailscale** shows "ready away from home" once it's done. At home Moonlight still uses the local address, and an address you entered yourself is never replaced.

Away from home:

- **Lower Moonlight's bitrate** to below your home connection's upload speed, often 20–40 Mbit/s.
- Controllers plugged into the Pi work through Moonlight instead of VirtualHere (see below).
- If `tailscale ping <pc-name>` says **"via DERP"**, the connection is relayed, which adds latency.
- Wake-on-LAN doesn't work over Tailscale. Leave the PC on.

## Sharing USB devices with your PC (VirtualHere)

[VirtualHere](https://www.virtualhere.com) makes USB devices plugged into the Pi appear on your PC as if they were plugged in there. A wired DualSense, for example, keeps its haptics and adaptive triggers. The setup installs the VirtualHere server on the Pi. On the PC, set up the client with the [PC setup script](#setting-up-the-gaming-pc), or from [virtualhere.com](https://www.virtualhere.com/usb_client_software).

In the VirtualHere client, right-click each device that should move to the PC and choose **Auto-Use Device** (it moves whichever port it's in) or **Auto-Use Port** (anything in that port moves). Other devices stay with the Pi. The free version shares one device at a time; more needs a licence from virtualhere.com.

**Only while streaming (recommended, on by default).** Devices stay on the Pi between streams, so a controller works in the menu and in Moonlight. When a stream starts, the Pi starts the VirtualHere server and the devices move to the PC within a few seconds. When the stream ends, they come back. Start a stream before setting up Auto-Use, as the devices only appear in the client during one.

- During a stream the controller is on the PC, so Moonlight's controller shortcut can't end it. **Quit the game on the PC** (for example, exit Steam Big Picture), or press Ctrl+Alt+Shift+Q on a keyboard plugged into the Pi.
- To see what it's doing: `journalctl -u moonlight-usb-handoff --no-pager`

Turned off, VirtualHere shares devices with the PC all the time.

**Over Tailscale (not recommended, off by default).** VirtualHere is blocked on the Tailscale network by a firewall rule, so it only works at home. Over the internet it adds input lag and stutter, and controllers work better through Moonlight. If you turn it on anyway, add the Pi in the VirtualHere client: right-click **USB Hubs → Specify Hubs** and enter the Pi's Tailscale address and port, for example `100.101.102.103:7575` (the menu shows the address).

Bluetooth controllers always stay with the Pi, because the Pi 5's built-in Bluetooth isn't a USB device. For full DualSense features over VirtualHere, use a USB cable, or a USB Bluetooth adapter on the Pi.

## Setting up the gaming PC

If you use VirtualHere or Tailscale, run this in **PowerShell** on your Windows PC, as your normal user:

```powershell
irm https://raw.githubusercontent.com/joshmichael/moonlight-pi-setup/main/windows/moonlight-pc-setup.ps1 | iex
```

It uses the VirtualHere client you already have, or downloads the official one and checks its signature. It sets the client to start with Windows and to look for the Pi every 5 seconds instead of 30, so devices arrive quickly when a stream starts. It also offers to install Tailscale. It's safe to run again.

The first time the VirtualHere client runs, it asks to install its USB driver: say Yes.

## What the script changes

| Change | Why |
|---|---|
| Installs `moonlight-qt` with `apt`, adding Moonlight's official repository if needed | The official build, updated with the system |
| Adds your user to the `video`, `render`, `input` and `audio` groups | Access to the display, GPU, controllers and sound |
| Writes `~/.config/moonlight-pi-setup/eglfs.json` | Points Moonlight at the Pi 5's display. Without it, Moonlight fails with "Cannot create window: no screens available" |
| Writes `~/.asoundrc`, sets `AUDIODEV=default` and `SDL_AUDIODRIVER=alsa` | Sends sound to the connected HDMI port and fixes surround sound (see below) |
| Writes `/etc/sysctl.d/90-moonlight-pi-setup.conf` | A larger network buffer for high bitrates |
| Boots to the console with auto-login, and adds a start-up loop to `~/.bash_profile` | Starts Moonlight at boot, and the menu when it closes |
| Installs the TV menu in `/usr/local/lib/moonlight-pi-setup`, with `moonlight-menu`, the `moonlight-wifi-setup` service and a polkit rule | The menu, and permission for it to change Wi-Fi, restart and run phone setup |
| Installs the settings helper, `/usr/local/lib/moonlight-pi-setup/moonlight-settings`, with a sudoers rule | The script and the menu change settings through this one root-owned helper, which only accepts its own commands. It keeps its records in `/var/lib/moonlight-pi-setup` |
| Installs Tailscale and the VirtualHere server | They do nothing until you sign in to Tailscale or use a device from the PC |
| By default, and changeable in Settings: quiet-boot options in `/boot/firmware/cmdline.txt` and `config.txt`, the `moonlight-quit-on-shutdown` service, Wi-Fi power saving off (`/etc/NetworkManager/conf.d/90-moonlight-pi-setup-wifi.conf`), the `moonlight-usb-handoff` service, and the `moonlight-virtualhere-tailscale` firewall rule | See above. Changing the setting undoes it |

Everything the script adds to a file is marked, and changed files are backed up first.

### Why surround sound needs fixing

On Raspberry Pi OS Lite (plain ALSA), Moonlight has two surround problems on the Pi 5:

1. **"Your selected surround sound setting is not supported"**: for 5.1, Moonlight's audio library looks for an ALSA device called `surround51`, which the Pi's HDMI output doesn't have.
2. **Sound from the wrong speakers**: the Pi's HDMI output expects 5.1 channels in a different order (FL FR LFE FC RL RR) from the one Linux programs send (FL FR RL RR FC LFE).

The speakers setting fixes both. **7.1 is experimental**: it uses the same method, but hasn't been tested on real 7.1 speakers. If a speaker plays the wrong channel, please [open an issue](https://github.com/joshmichael/moonlight-pi-setup/issues) with your TV or receiver model.

## Troubleshooting

**Logs**

- Moonlight: `/tmp/moonlight.log`
- TV menu: `/tmp/moonlight-menu.log`
- Setup script runs: `~/.local/share/moonlight-pi-setup/`
- Phone Wi-Fi setup: `journalctl -u moonlight-wifi-setup --no-pager`

If the menu can't start, the Pi falls back to a "press any key for a command line" prompt, then starts Moonlight.

**No sound, or surround not working**

```bash
aplay -L | grep -i hdmi                       # Is the HDMI audio device there?
speaker-test -c 6 -t wav -l 1                 # 5.1 test: each speaker says where it is
grep -i channels /tmp/moonlight.log | tail -3 # Should say 6 channels for 5.1
```

Sound goes to whichever HDMI port has a screen connected when Moonlight starts (HDMI 0 if both do). After moving the cable, restart the Pi or quit Moonlight. If the 5.1 test fails, your TV may only accept stereo from the Pi: to pass surround through a TV to a receiver, the TV needs **eARC**. Otherwise, connect the Pi to the receiver.

**Stutter or dropped frames**

`ethtool eth0 | grep -E "Speed|Duplex"` should say `1000Mb/s` and `Full`. To test the connection, install [iperf3](https://iperf.fr) on both. Run `iperf3 -s` on the PC, then on the Pi:

```bash
sudo apt install iperf3
iperf3 -c <PC_IP> -R -u -b 500M -t 15   # "Lost/Total" should be close to 0%
```

**"Cannot create window: no screens available"**

Run the script again.

## Uninstalling

On the TV: **Settings → Uninstall this setup**. Choose whether to also remove Moonlight, Tailscale and VirtualHere (Tailscale and VirtualHere only if the setup installed them), then **Uninstall**. The Pi restarts when it's done.

Or over SSH:

```bash
~/.local/share/moonlight-pi-setup/moonlight-pi-setup.sh --uninstall
```

This removes what the script added (see [What the script changes](#what-the-script-changes)). It puts back how the Pi booted before, your original `~/.asoundrc` and boot options, and removes the menu, the settings helper and the services. Over SSH, it asks before removing Moonlight, Tailscale or VirtualHere. Your user stays in the video, render, input and audio groups, and backups stay in `~/.local/share/moonlight-pi-setup/backups`.

## Known limitations

- **Raspberry Pi 5 and Raspberry Pi OS Lite only.** Other setups, including the desktop version, may work with `--force`, but aren't supported.
- **7.1 audio** is experimental.
- **VRR** isn't available: the Pi 5's HDMI output doesn't support it.
- **PyroWave** (from Nonary's Moonlight fork) decodes too slowly on the Pi 5 for 60 fps.
- **Enterprise Wi-Fi** (a username as well as a password) can't be set up from the menu.

## Reporting problems

Please [open an issue](https://github.com/joshmichael/moonlight-pi-setup/issues) with:

- your Pi model and OS version (`cat /etc/os-release`)
- your TV or receiver model and speaker setup
- the setup log from `~/.local/share/moonlight-pi-setup/`
- `/tmp/moonlight.log` if the problem is in Moonlight

## Licence

MIT. Moonlight, Tailscale and VirtualHere are separate projects with their own licences; this script installs them from their official sources.
