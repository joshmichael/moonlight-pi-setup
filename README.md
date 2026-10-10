# Moonlight Pi Setup

A setup script that turns a **Raspberry Pi 5** into a [Moonlight](https://moonlight-stream.org) game-streaming box. After setup, the Pi can boot straight into Moonlight on your TV, with hardware HEVC decoding, HDR and surround sound. It works on both **Raspberry Pi OS Lite** and the full **desktop** version.

Tested result on a Pi 5 (8 GB) over wired gigabit Ethernet: **2560×1440 at 60 fps, HEVC 10-bit HDR, 5.1 surround**, with ~3.5 ms decode time and no dropped frames.

---

## Requirements

- **Raspberry Pi 5** (any RAM size)
- **Raspberry Pi OS (64-bit)**, based on Debian 13 "Trixie": either **Lite** (recommended for a dedicated box) or the full **desktop** version. Enable SSH in Raspberry Pi Imager's settings so you can manage the Pi once it boots into Moonlight.
- **Wired Ethernet** (strongly recommended)
- A TV or monitor on HDMI. Either port works, and you can swap ports later: sound follows whichever port has a screen connected.
- A gaming PC running a streaming host: [Sunshine](https://github.com/LizardByte/Sunshine), [Apollo](https://github.com/ClassicOldSong/Apollo) or [Vibepollo](https://github.com/Nonary/Vibepollo)

## Installation

Run this on the Pi as your **normal user** (not with `sudo`). It asks for your password when it needs it.

```bash
curl -fsSL https://raw.githubusercontent.com/joshmichael/moonlight-pi-setup/main/moonlight-pi-setup.sh | bash
```

If you'd rather read the script before running it:

```bash
curl -fsSLO https://raw.githubusercontent.com/joshmichael/moonlight-pi-setup/main/moonlight-pi-setup.sh
less moonlight-pi-setup.sh
bash moonlight-pi-setup.sh
```

Have the TV (and receiver, if you use one) switched on while the script runs, so it can detect the HDMI port and play a speaker test.

### Questions the script asks

| Question | Default |
|---|---|
| **Lite:** Start Moonlight automatically when the Pi boots (recommended) | Yes |
| **Desktop:** Boot straight into Moonlight instead of the desktop (recommended) | Yes |
| **With auto-start:** Show the controller and Wi-Fi menu when Moonlight closes (recommended) | Yes |
| **With the menu, only if the Pi doesn't know yet:** Which country you're in (two letters, for Wi-Fi channels) | — |
| Speaker setup: Stereo, 5.1 or 7.1 (not asked if you keep the desktop, see below) | Stereo |
| Hide boot messages (recommended) | Yes |
| Quit the running game on your PC when the Pi shuts down (recommended) | Yes |
| **Wi-Fi only:** Turn off Wi-Fi power saving (recommended on Wi-Fi) | Yes |
| Install Tailscale (not asked if it's already installed) | No |
| Install VirtualHere (not asked if it's already installed) | No |
| **With VirtualHere and auto-start:** Share USB devices with your PC only while streaming (recommended) | Yes |

You'll see a summary before anything is changed. At the end, the script offers to reboot.

When you re-run the script, the defaults are the answers you gave last time, so you can just press Enter through everything except the setting you want to change.

## What the script changes

| Change | Why |
|---|---|
| Installs `moonlight-qt` with `apt` (adds Moonlight's official package repository only if needed) | The official build, kept up to date by `apt upgrade` |
| Adds your user to the `video`, `render`, `input` and `audio` groups | Access to the display, GPU, controllers and audio |
| Writes `~/.config/moonlight-pi-setup/eglfs.json` | Points Moonlight at the Pi 5's display controller. Without it Moonlight fails with **"Cannot create window: no screens available"** |
| Writes `~/.asoundrc`, and checks which HDMI port has a screen connected each time Moonlight starts | Sends audio to the TV's HDMI port, even if you swap ports later, and fixes the surround channel order (see below) |
| Sets `AUDIODEV=default` and `SDL_AUDIODRIVER=alsa` | Stops Moonlight wrongly reporting that surround sound isn't supported, and makes it use the settings above |
| Writes `/etc/sysctl.d/90-moonlight-pi-setup.conf` (`net.core.rmem_max`) | Lets Moonlight use a larger network buffer, which helps at high bitrates |
| Switches the Pi to boot to the console with auto-login, and adds a launch loop to `~/.bash_profile` | Boots straight into Moonlight, and restarts it if it closes. How the Pi booted before (console or desktop) is remembered and restored on uninstall |
| Optional: installs the TV menu (`python3-pygame` and friends, `/usr/local/lib/moonlight-pi-setup/menu`, `/usr/local/bin/moonlight-menu`), the `moonlight-wifi-setup` service, and a polkit rule (`/etc/polkit-1/rules.d/50-moonlight-pi-setup.rules`) | A menu on the TV for Bluetooth controllers, Wi-Fi and restarting, usable with a controller (see [The TV menu](#the-tv-menu)). The polkit rule lets your user change Wi-Fi, restart or shut down, and run phone setup without a password |
| Optional: adds quiet-boot options to `/boot/firmware/cmdline.txt` and `config.txt` | Hides boot text and the splash screen |
| Optional: installs `/usr/local/bin/moonlight-quit-all` and the `moonlight-quit-on-shutdown` service | Ends the game on your PC when the Pi is switched off (see below) |
| Optional, on Wi-Fi: writes `/etc/NetworkManager/conf.d/90-moonlight-pi-setup-wifi.conf` (`wifi.powersave = 2`) | Wi-Fi power saving makes the Pi's Wi-Fi doze between packets, which causes stutter and lag spikes |
| Optional: installs Tailscale and/or VirtualHere | See below |
| Optional, with VirtualHere: installs `/usr/local/bin/moonlight-usb-handoff` and the `moonlight-usb-handoff` service, and stops the VirtualHere server starting at boot | Shares USB devices with your PC only while you're streaming (see below) |

Everything the script adds is wrapped in marker comments, and every file is backed up first, so it's safe to **run it again** to change your choices. Answering No to an option you turned on before undoes it (for example, boot messages come back and the quit-on-shutdown service is removed).

### Why the audio fix is needed

On Raspberry Pi OS Lite (plain ALSA, no PipeWire or PulseAudio), Moonlight has two surround-sound problems on the Pi 5. Both come from the Pi's software, not your TV or speakers:

1. **"Your selected surround sound setting is not supported"**: when asked for 5.1, the audio library Moonlight uses looks for an ALSA device called `surround51`, which the Pi's HDMI output doesn't provide.
2. **Wrong speakers**: the Pi's HDMI output expects the six 5.1 channels in a different order (FL FR LFE FC RL RR) from the one Linux programs send (FL FR RL RR FC LFE). Without the fix, the centre, subwoofer and rear channels come out of the wrong speakers.

The script fixes both. On the desktop version, the fix is used when Moonlight boots on its own. If you keep the desktop, the desktop's audio system (PipeWire) handles the speakers instead, and the script leaves audio alone.

> **7.1 is experimental.** It's built using the same method as the tested 5.1 fix, but hasn't been confirmed on real 7.1 speakers. If any speaker plays the wrong channel, please [open an issue](https://github.com/joshmichael/moonlight-pi-setup/issues) with your TV/receiver model and the output of `speaker-test -c 8 -t wav -l 1`.

## Using the desktop version

On the full desktop version of Raspberry Pi OS, the script asks whether to **boot straight into Moonlight instead of the desktop**.

**Yes (recommended).** The Pi boots to the console and starts Moonlight on its own, exactly like the Lite setup. This gives the lowest latency and is the only way to get **HDR**. The desktop stays installed:

- To open it, quit Moonlight, press a key within 5 seconds, then type `sudo systemctl start display-manager`.
- Uninstalling (or re-running the script and answering No) switches the Pi back to booting to the desktop, including desktop auto-login if you had it.

**No.** The Pi keeps booting to the desktop, and you open Moonlight from the applications menu (under Games). Streaming works, but it runs through the desktop, so there's no HDR and slightly more latency. Audio is handled by the desktop: for surround sound, choose 5.1 or 7.1 for the HDMI output in the desktop's sound settings.

## After rebooting

If you chose to boot straight into Moonlight, the Pi boots into Moonlight's host list. Pair it with your PC (enter the PIN shown in your host's web UI), then set:

| Setting | Recommended |
|---|---|
| Video codec | **HEVC** (the Pi 5 decodes it in hardware; H.264 is software-decoded on the Pi 5) |
| Resolution | Your TV's resolution, or 1440p / 1080p |
| Frame rate | 60 FPS |
| Bitrate | 50–100 Mbit/s on wired Ethernet |
| V-Sync | On. Off saves a few milliseconds, but may cause tearing |
| Audio | Match your speakers, and set Windows to the same layout |

Press **Ctrl+Alt+Shift+S** during a stream to show the performance overlay, and **Ctrl+Alt+Shift+Q** (or Select+Start+L1+R1 on a controller) to end it.

To get a command line on the TV, quit Moonlight and choose **Command line** in the [TV menu](#the-tv-menu) (without the menu: press any key within 5 seconds of Moonlight closing).

## The TV menu

If you chose the menu, it opens on the TV whenever Moonlight closes. To get there from Moonlight's main screen (the list of PCs), press **B** (Circle on PlayStation) or **Esc**, and confirm quitting. It also opens by itself at boot if the Pi isn't connected to a network, or if no controller is connected or paired yet.

It works with a controller (D-pad or left stick, A to select, B to go back), a keyboard (arrow keys, Enter, Esc) or a mouse.

| Menu item | What it does |
|---|---|
| **Start Moonlight** | Back to Moonlight (or press Start/Options) |
| **Controllers** | Pair a new Bluetooth controller, and connect, disconnect or forget paired ones. Shows battery levels where the controller reports them. Paired controllers reconnect by themselves when you switch them on |
| **Wi-Fi & network** | Network status, Wi-Fi on/off, choose a Wi-Fi network (with an on-screen keyboard for the password), set up Wi-Fi from your phone, and saved networks |
| **Restart or shut down** | Restarts or shuts down the Pi |
| **Command line** | Leaves the menu for a text command line (needs a keyboard). Type `exit` to come back |

**Pairing a controller:** choose **Controllers → Pair a new controller**, put the controller in pairing mode, and select it when it appears. On a DualSense or DualShock 4, hold **Create/Share + PS** until the light flashes. On an Xbox controller, switch it on and hold the small pair button on top until the Xbox button flashes quickly. If no controller is connected or paired at all, the menu pairs the first controller you put in pairing mode by itself, so you can set up a new Pi without a cable or keyboard.

**Setting up Wi-Fi from your phone:** choose **Wi-Fi & network → Set up Wi-Fi from your phone** (it starts by itself when the Pi boots without a network). The Pi starts its own Wi-Fi network and the TV shows a QR code:

1. Scan the code with your phone's camera to join the Pi's setup network.
2. A setup page opens on your phone (if it doesn't, open `http://10.42.0.1`).
3. Choose your Wi-Fi network, type its password, and tap Connect.

The Pi joins your network and the TV shows when it's connected. If the password was wrong, the setup network comes back so you can try again. The setup network has its own password, shown on the TV, so only someone who can see the TV can use it, and it switches off after 15 minutes.

Notes:

- Bluetooth controllers always work through Moonlight on the Pi. They can't be moved to your PC with the [VirtualHere hand-off](#virtualhere-sharing-usb-devices-with-your-pc), because the Pi 5's built-in Bluetooth isn't a USB device. For full DualSense features (adaptive triggers, HD haptics), use a USB cable, or a USB Bluetooth adapter on the Pi that VirtualHere moves as a whole.
- Enterprise Wi-Fi (networks that need a username as well as a password) isn't supported in the menu.
- The menu's log is at `/tmp/moonlight-menu.log`. If the menu can't start, the Pi falls back to the plain "press any key for a command line" prompt and restarts Moonlight.

## Optional extras

### Quit the game when the Pi is switched off

If you switch the Pi off (for example with its power button) without quitting the game in Moonlight first, your PC keeps the game running, because streaming hosts are designed to let you reconnect and carry on. With a virtual display (Apollo or Vibepollo), this can leave the PC stuck on the stream until the session is ended.

With this option, the Pi tells every PC it's paired with to quit its running game as it shuts down. PCs with nothing running aren't affected. The command is sent before the network is switched off, and if a PC doesn't respond the Pi gives up after 10 seconds per PC, so shutdown is never held up for long.

- This **closes the game**, the same as choosing Quit in Moonlight, so save first.
- If someone else is streaming from one of your paired PCs on another device, their game will be closed too.
- To test it without shutting down, start a stream and run `moonlight-quit-all` over SSH.
- To see what happened at recent shutdowns: `cat ~/.local/share/moonlight-pi-setup/quit-on-shutdown.log`. (Raspberry Pi OS clears the system journal at every reboot, so `journalctl -b -1` won't show it.)

### Tailscale (streaming away from home)

[Tailscale](https://tailscale.com) connects the Pi and your PC over the internet without opening ports on your router.

- Install Tailscale on your **gaming PC** too, signed in to the same account. The [PC setup script](#setting-up-the-gaming-pc) can do this.

**Before you take the Pi away from home**, do these once while you're still at home:

1. **Give Moonlight your PC's Tailscale address.** Moonlight only knows the PC's home-network address, so away from home the PC would show as offline. Find the PC's Tailscale address (it starts with `100.`) in the Tailscale app on the PC, or by running `tailscale ip -4` on the PC. Then in Moonlight on the Pi, choose **Add PC manually** and enter it. Moonlight recognises it as the same PC, keeps the pairing, and saves it as an extra address. At home it keeps using the local address.
2. **Turn off key expiry for the Pi and the PC.** Tailscale signs devices out every 180 days by default, and a Pi that boots into Moonlight is awkward to sign back in. In the [Tailscale admin console](https://login.tailscale.com/admin/machines), open the **…** menu next to each of the two machines and choose **Disable key expiry**.

Away from home:

- **Lower Moonlight's bitrate** (in Moonlight's settings) to below your home connection's *upload* speed, often 20–40 Mbit/s. The 50–100 Mbit/s that works at home will stutter or fail over the internet. Raise it again when you're back home.
- **VirtualHere won't work away from home.** The VirtualHere client on your PC only finds the Pi on your home network, so a controller plugged into the Pi stays with the Pi and works through Moonlight's own controller support instead (buttons, rumble and usually motion, but not adaptive triggers or HD haptics). That's the better option anyway: VirtualHere over the internet adds input lag and stutter, so don't add the Pi's Tailscale address to VirtualHere.
- Run `tailscale ping <pc-name>`. If replies say **"via DERP"**, the connection is relayed, which adds latency. A direct connection is better.
- Wake-on-LAN from Moonlight won't work over Tailscale. Leave the PC on, or wake it with something that's always on at home.

### VirtualHere (sharing USB devices with your PC)

[VirtualHere](https://www.virtualhere.com) makes USB devices plugged into the Pi appear on your PC as if they were plugged in directly. A wired DualSense, for example, keeps its native haptics and adaptive triggers.

- Set up the **VirtualHere client** on your PC with the [PC setup script](#setting-up-the-gaming-pc), or by hand from https://www.virtualhere.com/usb_client_software
- In the client, right-click each device that should move to the PC and choose **Auto-Use Device** (the device moves whichever USB port it's in) or **Auto-Use Port** (anything plugged into that port moves). Devices without Auto-Use stay with the Pi. Choosing **Stop using** on a device turns its Auto-Use off, so turn it back on afterwards.
- The free version shares **one device at a time**. More needs a licence from virtualhere.com.
- While a device is shared through VirtualHere it belongs to the PC, so Moonlight on the Pi can't see it.
- **Home network only.** Away from home (over Tailscale), VirtualHere won't find the Pi, and controllers work through Moonlight instead. See [Tailscale](#tailscale-streaming-away-from-home).

#### Sharing USB devices only while streaming (recommended)

With this option, a controller plugged into the Pi works in Moonlight's menus between streams, and moves to your PC (with full haptics and adaptive triggers on a DualSense) while you're streaming. The Pi watches Moonlight's log: when a stream starts it switches the VirtualHere server on, and when the stream ends (quit or disconnect) it switches it off, which gives the devices back to the Pi.

On the PC, run the [PC setup script](#setting-up-the-gaming-pc), which keeps the VirtualHere client running and makes it find the Pi quickly. Then start a stream (the Pi's devices only appear in the client while you're streaming) and turn on Auto-Use for the devices that should move, as above.

Then:

- When a stream starts, the controller takes a few seconds to appear on the PC (2–8 seconds in testing). When the stream ends, it's back on the Pi within about 2 seconds.
- **To end a stream, quit the game or app on the PC** (for example, exit Steam Big Picture), or press Ctrl+Alt+Shift+Q on a keyboard plugged into the Pi. The controller is on the PC during a stream, so Moonlight's controller shortcut (Select+Start+L1+R1) can't reach the Pi.
- It needs Moonlight to start automatically, because it reads the log that the auto-start writes.
- To see what it's doing: `journalctl -u moonlight-usb-handoff --no-pager`

## Setting up the gaming PC

If you use VirtualHere or Tailscale, run this in **PowerShell** on your Windows gaming PC, as your normal user (not as administrator):

```powershell
irm https://raw.githubusercontent.com/joshmichael/moonlight-pi-setup/main/windows/moonlight-pc-setup.ps1 | iex
```

It:

- **VirtualHere:** uses the client you already have, or downloads the official one (and checks it's signed by VirtualHere). It makes the client start with Windows, and makes it look for the Pi every 5 seconds instead of every 30, so USB devices reach the PC within seconds of a stream starting. The first time the client runs, it asks for permission to install its USB driver: say Yes.
- **Tailscale:** asks whether to install it, if it isn't installed already.

It's safe to run again, and only changes what isn't set up yet. It doesn't choose which USB devices move: you do that in the VirtualHere client (see [VirtualHere](#virtualhere-sharing-usb-devices-with-your-pc)).

## Troubleshooting

**Logs**

- Moonlight: `/tmp/moonlight.log` (cleared on reboot)
- Setup script runs: `~/.local/share/moonlight-pi-setup/`
- TV menu: `/tmp/moonlight-menu.log` (cleared on reboot)
- Phone Wi-Fi setup: `journalctl -u moonlight-wifi-setup --no-pager`

**No sound, or surround not working**

```bash
aplay -L | grep -i hdmi                       # Is the HDMI audio device there?
speaker-test -c 6 -t wav -l 1                 # 5.1 test: each speaker announces its position
grep -i channels /tmp/moonlight.log | tail -3 # Should say 6 channels for 5.1
```

Sound goes to whichever HDMI port has a screen connected when Moonlight starts (HDMI 0 if both do). After moving the cable to the other port, reboot, or quit Moonlight so it restarts. To check which port is being used, run `echo $MOONLIGHT_HDMI_CARD` over SSH: `vc4hdmi0` is HDMI 0, `vc4hdmi1` is HDMI 1.

If the speaker test fails for 5.1, your TV may only accept stereo from the Pi. To pass surround sound through a TV to a receiver or soundbar, the TV needs **eARC** (not plain ARC). Otherwise, connect the Pi to the receiver directly.

**Stutter or dropped frames**

Check the link speed with `ethtool eth0 | grep -E "Speed|Duplex"`. It should say `1000Mb/s` and `Full`. To test the connection to your PC, install [iperf3](https://iperf.fr) on both. On the PC run `iperf3 -s`, then on the Pi:

```bash
sudo apt install iperf3
iperf3 -c <PC_IP> -R -u -b 500M -t 15   # "Lost/Total" should be close to 0%
```

**"Cannot create window: no screens available"**

Re-run the script. If it persists, check that `~/.config/moonlight-pi-setup/eglfs.json` exists and that `echo $QT_QPA_EGLFS_KMS_CONFIG` prints its path when you log in on the TV.

## Uninstalling

```bash
~/.local/share/moonlight-pi-setup/moonlight-pi-setup.sh --uninstall
```

This removes the settings the script added, restores your original `~/.asoundrc` if you had one, puts back how the Pi originally booted (to the desktop, or to a console login on Lite), restores the boot options and Wi-Fi power saving, switches VirtualHere back to sharing all the time, removes the TV menu, and removes the quit-on-shutdown service (without quitting a game that's running at the time). It asks before uninstalling Moonlight, Tailscale or VirtualHere. Backups stay in `~/.local/share/moonlight-pi-setup/backups`.

## Known limitations

- **Raspberry Pi 5 only**, on Raspberry Pi OS (Trixie), Lite or desktop. Other setups may work with `--force` but aren't supported.
- **Desktop version support is new.** It has been tested in simulation but not yet on real hardware. Lite is the most tested setup. Please report any problems.
- **PyroWave** (from Nonary's Moonlight fork) is not supported on the Pi 5. It can be patched to run, but decodes too slowly for 60 fps and is SDR-only.
- **VRR** isn't available, because the Pi 5's HDMI output doesn't support it.
- **7.1 audio** is experimental (see above).

## Reporting problems

Please [open an issue](https://github.com/joshmichael/moonlight-pi-setup/issues) and include:

- your Pi model and OS version (`cat /etc/os-release`)
- TV/receiver model and speaker setup
- the setup log from `~/.local/share/moonlight-pi-setup/`
- `/tmp/moonlight.log` if the problem is in Moonlight

## Licence

MIT. Moonlight, Tailscale and VirtualHere are separate projects with their own licences; this script only installs them from their official sources.
