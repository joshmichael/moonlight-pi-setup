"""The menu's settings: read and changed through moonlight-settings.

moonlight-settings is the root-owned helper that the setup script uses too, so
each setting is applied in one place. The menu runs it with sudo (a sudoers
rule allows exactly that helper without a password) for the settings that
need it.
"""
import functools
import glob
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HELPER = '/usr/local/lib/moonlight-pi-setup/moonlight-settings'
STATE_FILE = os.path.expanduser('~/.local/share/moonlight-pi-setup/state')
MOONLIGHT_CONF = os.path.expanduser('~/.config/Moonlight Game Streaming Project/Moonlight.conf')
CMDLINE = '/boot/firmware/cmdline.txt'
COUNTRIES = '/usr/share/zoneinfo/iso3166.tab'
ZONES = '/usr/share/zoneinfo/zone1970.tab'
LATEST_SCRIPT_URL = 'https://raw.githubusercontent.com/joshmichael/moonlight-pi-setup/main/moonlight-pi-setup.sh'
PC_SETUP_COMMAND = ('irm https://raw.githubusercontent.com/joshmichael/moonlight-pi-setup/main/windows/'
                    'moonlight-pc-setup.ps1 | iex')
PC_SETUP_URL = 'https://github.com/joshmichael/moonlight-pi-setup#setting-up-the-gaming-pc'

TAILSCALE_V4 = ipaddress.ip_network('100.64.0.0/10')
HOST_OS = ('windows', 'linux', 'macos')       # where a streaming host can run
HOST_PORT = 47989                             # a streaming host's default port

SPEAKER_NAMES = {'stereo': 'Stereo', '5.1': '5.1 surround', '7.1': '7.1 surround'}
SPEAKER_CHANNELS = {'stereo': 2, '5.1': 6, '7.1': 8}

# Some names in the time zone database are shorter than people expect.
COUNTRY_NAMES = {'GB': 'United Kingdom', 'US': 'United States', 'KR': 'South Korea', 'KP': 'North Korea'}

URL_RE = re.compile(r'https://[^\s"\']+')

_lock = threading.Lock()      # one settings change at a time


class SettingsError(Exception):
    pass


def _read(path):
    try:
        with open(path, errors='replace') as f:
            return f.read()
    except OSError:
        return ''


def error_text(output):
    """The most useful line of a failed helper run."""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.startswith('ERROR:'):
            return ln[len('ERROR:'):].strip()
    errors = [ln for ln in lines[-8:] if ln.startswith('[ERROR]')]     # from the setup script
    if errors:
        return errors[0][len('[ERROR]'):].strip()
    for ln in reversed(lines):
        if 'a password is required' in ln or 'not allowed to execute' in ln:
            return ("The menu isn't allowed to change this setting. Run the setup script again "
                    "to fix it.")
    return lines[-1] if lines else 'Something went wrong.'


# ---------------------------------------------------------------------------
# The setup script's state file (key=value lines)
# ---------------------------------------------------------------------------
def state_get(key, default=''):
    try:
        with open(STATE_FILE) as f:
            for line in f:
                k, sep, v = line.rstrip('\n').partition('=')
                if sep and k == key:
                    return v
    except OSError:
        pass
    return default


def state_set(key, value):
    lines = []
    try:
        with open(STATE_FILE) as f:
            lines = [ln for ln in f.read().splitlines() if not ln.startswith(key + '=')]
    except OSError:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    lines.append('%s=%s' % (key, value))
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    os.replace(tmp, STATE_FILE)


def setup_version():
    return state_get('version')


def version_tuple(v):
    return tuple(int(p) for p in re.findall(r'\d+', v or '')) or (0,)


def quick_setup_pending():
    return state_get('quick_setup') == '1'


def finish_quick_setup():
    state_set('quick_setup', '0')


# ---------------------------------------------------------------------------
# Changing settings through the helper
# ---------------------------------------------------------------------------
def _helper_command(args, root):
    if not os.access(HELPER, os.X_OK):
        raise SettingsError("The settings helper isn't installed. Run the setup script again.")
    return (['sudo', '-n', HELPER] if root else [HELPER]) + list(args)


def _run(args, root=False, timeout=180):
    try:
        p = subprocess.run(_helper_command(args, root), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SettingsError('That took too long. Try again.')
    if p.returncode != 0:
        raise SettingsError(error_text(p.stdout + '\n' + p.stderr))
    return p.stdout


def stream(args, on_line):
    """Runs the helper as root, calling on_line(line) for each line of output.
    Returns (ok, error message)."""
    try:
        cmd = _helper_command(args, root=True)
    except SettingsError as e:
        return False, str(e)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            bufsize=1, errors='replace')
    tail = []
    for line in proc.stdout:
        line = line.rstrip('\n')
        tail = (tail + [line])[-40:]
        on_line(line)
    rc = proc.wait()
    return rc == 0, '' if rc == 0 else error_text('\n'.join(tail))


def status():
    """The current settings, as the helper reports them (strings: '1' is on)."""
    st = {}
    for line in _run(['status']).splitlines():
        k, sep, v = line.partition('=')
        if sep:
            st[k] = v
    return st


def change(name, value):
    """name: quit-on-shutdown, usb-sharing, usb-tailscale, quiet-boot or
    wifi-powersave (value on/off), or wifi-country (value XX)."""
    with _lock:
        return _run([name, value], root=True)


def usb_device(model, rule):
    """What a USB device (model: vendor:product) does: always, never or auto."""
    with _lock:
        return _run(['usb-device', model, rule], root=True)


def usb_port(key, on):
    """Whether anything plugged into a port goes to the PC."""
    with _lock:
        return _run(['usb-port', key, 'on' if on else 'off'], root=True)


def set_speakers(mode):
    with _lock:
        return _run(['speakers', mode])


def tailscale(cmd):
    """cmd: down or logout. (Signing in streams its output: see stream().)"""
    with _lock:
        return _run(['tailscale', cmd], root=True, timeout=60)


def check_updates():
    """(number of updates, [package names])."""
    out = _run(['updates', 'check'], root=True, timeout=600)
    n, pkgs = 0, []
    for line in out.splitlines():
        if line.startswith('UPDATES='):
            n = int(line[8:] or 0)
        elif line.startswith('PACKAGES='):
            pkgs = line[9:].split()
    return n, pkgs


def latest_version():
    """The newest setup version on GitHub."""
    with urllib.request.urlopen(LATEST_SCRIPT_URL, timeout=15) as r:
        for raw in r:
            m = re.match(r'SCRIPT_VERSION="([0-9.]+)"', raw.decode('utf-8', 'replace'))
            if m:
                return m.group(1)
    raise SettingsError("Couldn't find the latest version.")


# ---------------------------------------------------------------------------
# Speakers
# ---------------------------------------------------------------------------
def hdmi_card():
    """The ALSA card of the HDMI port with a screen connected (like ~/.bash_profile)."""
    for port in (1, 2):
        for path in glob.glob('/sys/class/drm/card*-HDMI-A-%d/status' % port):
            if _read(path).strip() == 'connected':
                return 'vc4hdmi%d' % (port - 1)
    return 'vc4hdmi%s' % (state_get('hdmi_port') or '0')


def speaker_test(mode):
    """Plays speaker-test: each speaker says where it is."""
    env = dict(os.environ, MOONLIGHT_HDMI_CARD=hdmi_card())
    try:
        p = subprocess.run(['speaker-test', '-D', 'default', '-c', str(SPEAKER_CHANNELS[mode]),
                            '-t', 'wav', '-l', '1'], capture_output=True, text=True, timeout=90, env=env)
    except FileNotFoundError:
        raise SettingsError("speaker-test isn't installed (it's part of alsa-utils).")
    except subprocess.TimeoutExpired:
        raise SettingsError("The speaker test didn't finish.")
    if p.returncode != 0:
        raise SettingsError(error_text(p.stderr or p.stdout))


# ---------------------------------------------------------------------------
# Wi-Fi country
# ---------------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def countries():
    """((code, name), ...) sorted by name."""
    out = []
    for line in _read(COUNTRIES).splitlines():
        if line.startswith('#') or '\t' not in line:
            continue
        code, name = line.split('\t', 1)
        out.append((code, COUNTRY_NAMES.get(code, name)))
    return tuple(sorted(out, key=lambda c: c[1].lower()))


def country_name(code):
    return dict(countries()).get(code, code)


def wifi_country():
    """The Wi-Fi country set in cmdline.txt (by raspi-config), or '' if none.
    Raspberry Pi OS keeps Wi-Fi switched off until it's set."""
    m = re.search(r'cfg80211\.ieee80211_regdom=([A-Z]{2})', _read(CMDLINE))
    return m.group(1) if m else ''


def guess_country():
    """The country of the Pi's time zone, if it's set."""
    try:
        zone = subprocess.run(['timedatectl', 'show', '-p', 'Timezone', '--value'], capture_output=True,
                              text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ''
    for line in _read(ZONES).splitlines():
        parts = line.split('\t')
        if len(parts) >= 3 and parts[2].strip() == zone:
            return parts[0].split(',')[0]
    return ''


# ---------------------------------------------------------------------------
# Tailscale, and Moonlight's saved PCs
# ---------------------------------------------------------------------------
def _tailscale_json():
    """What `tailscale status --json` says, or {} if Tailscale isn't running."""
    try:
        p = subprocess.run(['tailscale', 'status', '--json'], capture_output=True, text=True, timeout=10)
        return json.loads(p.stdout or '{}')
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def tailscale_status():
    """{'installed', 'state', 'ip', 'tailnet'}. state is Tailscale's BackendState
    (Running, Stopped, NeedsLogin, ...), or 'NotRunning' if its service isn't
    running."""
    if not shutil.which('tailscale'):
        return {'installed': False, 'state': ''}
    d = _tailscale_json()
    ips = [ip for ip in d.get('TailscaleIPs') or [] if '.' in ip]
    return {'installed': True, 'state': d.get('BackendState') or 'NotRunning', 'ip': ips[0] if ips else '',
            'tailnet': (d.get('CurrentTailnet') or {}).get('Name') or ''}


def is_tailscale_ip(addr):
    try:
        return ipaddress.ip_address(addr) in TAILSCALE_V4
    except ValueError:
        return False


def moonlight_hosts(path=MOONLIGHT_CONF):
    """The PCs saved in Moonlight's settings ([hosts] section), as dicts of
    their keys (index, hostname, uuid, localport, manualaddress, ...)."""
    hosts = {}
    section = None
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.rstrip('\n')
                if line.startswith('['):
                    section = line.strip('[]')
                    continue
                key, sep, value = line.partition('=')
                m = re.match(r'(\d+)\\([a-z]+)$', key)
                if section == 'hosts' and sep and m:
                    hosts.setdefault(m.group(1), {'index': m.group(1)})[m.group(2)] = value.strip('"')
    except OSError:
        return []
    return [h for h in hosts.values() if h.get('uuid')]


def set_moonlight_manual_address(index, ip, port, path=MOONLIGHT_CONF):
    """Sets a saved PC's manual address in Moonlight's settings, leaving the
    rest of the file as it is."""
    with open(path, encoding='utf-8', errors='surrogateescape', newline='') as f:
        lines = f.read().split('\n')
    want = {'%s\\manualaddress' % index: ip, '%s\\manualport' % index: str(port)}
    section, last = None, None
    for i, line in enumerate(lines):
        if line.startswith('['):
            section = line.strip()
            continue
        if section == '[hosts]':
            if line.strip():
                last = i
            key = line.partition('=')[0]
            if key in want:
                lines[i] = '%s=%s' % (key, want.pop(key))
    if want:
        if last is None:
            raise SettingsError("Moonlight hasn't saved any PCs yet.")
        lines[last + 1:last + 1] = ['%s=%s' % kv for kv in want.items()]
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', errors='surrogateescape', newline='') as f:
        f.write('\n'.join(lines))
    os.replace(tmp, path)


def pc_name(addr):
    """The name of the PC saved in Moonlight with this address, or ''."""
    for h in moonlight_hosts():
        for key in ('localaddress', 'manualaddress', 'remoteaddress', 'ipv6address'):
            if h.get(key, '').strip('[]') == addr:
                return h.get('hostname', '')
    return ''


def moonlight_running():
    try:
        return subprocess.run(['pgrep', '-x', 'moonlight-qt'], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def host_uniqueid(ip, port, timeout=2.5):
    """The ID a streaming host (Sunshine, Apollo, ...) reports: the same
    request Moonlight makes to identify a PC."""
    try:
        with urllib.request.urlopen('http://%s:%d/serverinfo' % (ip, port), timeout=timeout) as r:
            m = re.search(rb'<uniqueid>([^<]*)</uniqueid>', r.read(65536))
            return m.group(1).decode(errors='replace') if m else ''
    except (OSError, ValueError):
        return ''


def find_host(uuid, candidates, port):
    """The address of the candidate whose streaming host has this ID: the
    first candidate on its own, then the rest at once."""
    if not candidates:
        return None
    if host_uniqueid(candidates[0]['ip'], port).lower() == uuid.lower():
        return candidates[0]['ip']
    rest = candidates[1:]
    if rest:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for p, uid in zip(rest, pool.map(lambda p: host_uniqueid(p['ip'], port), rest)):
                if uid.lower() == uuid.lower():
                    return p['ip']
    return None


def moonlight_tailscale_sync(wait=0):
    """Gives each PC saved in Moonlight its Tailscale address (as the PC's
    manual address), so Moonlight can reach it away from home. Waits up to
    `wait` seconds for Tailscale to connect. Returns [(PC name, address)]
    for the PCs it changed."""
    if moonlight_running():
        return []       # Moonlight saves its settings when it closes, which would undo this
    hosts = moonlight_hosts()
    if not hosts or not shutil.which('tailscale'):
        return []
    end = time.monotonic() + wait
    d = _tailscale_json()
    while d.get('BackendState') != 'Running' and time.monotonic() < end:
        time.sleep(1.5)
        d = _tailscale_json()
    if d.get('BackendState') != 'Running':
        return []
    peers = []
    for peer in (d.get('Peer') or {}).values():
        ips = [ip for ip in peer.get('TailscaleIPs') or [] if is_tailscale_ip(ip)]
        if ips:
            peers.append({'name': (peer.get('HostName') or '').lower(), 'ip': ips[0],
                          'online': bool(peer.get('Online')), 'os': (peer.get('OS') or '').lower()})
    known = {p['ip'] for p in peers}
    changed = []
    for h in hosts:
        current = h.get('manualaddress', '')
        if current and (not is_tailscale_ip(current) or current in known):
            continue    # an address you entered yourself, or already the PC's Tailscale address
        port = int(h.get('localport') or 0) or HOST_PORT
        name = h.get('hostname', '').lower()
        # The PC with the same name first, then any other computer on Tailscale.
        candidates = [p for p in peers if p['online'] and (p['name'] == name or p['os'] in HOST_OS)]
        candidates.sort(key=lambda p: p['name'] != name)
        ip = find_host(h['uuid'], candidates[:12], port)
        if ip:
            set_moonlight_manual_address(h['index'], ip, port)
            changed.append((h.get('hostname') or 'Your PC', ip))
    return changed


# ---------------------------------------------------------------------------
# About this Pi
# ---------------------------------------------------------------------------
def _output(args, timeout=10):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ''


def about():
    info = {'hostname': socket.gethostname(), 'version': setup_version(),
            'model': _read('/proc/device-tree/model').strip('\0 \n'), 'os': ''}
    for line in _read('/etc/os-release').splitlines():
        if line.startswith('PRETTY_NAME='):
            info['os'] = line.split('=', 1)[1].strip('"')
    info['moonlight'] = _output(['dpkg-query', '-W', '-f=${Version}', 'moonlight-qt']).strip()
    info['tailscale_version'] = ''
    if shutil.which('tailscale'):
        info['tailscale_version'] = (_output(['tailscale', 'version']).splitlines() or [''])[0].strip()
    try:
        info['temp'] = int(_read('/sys/class/thermal/thermal_zone0/temp').strip()) / 1000
    except ValueError:
        info['temp'] = None
    m = re.search(r'0x[0-9a-fA-F]+', _output(['vcgencmd', 'get_throttled'], timeout=5))
    info['throttled'] = int(m.group(0), 16) if m else None
    du = shutil.disk_usage('/')
    info['disk_free'], info['disk_total'] = du.free, du.total
    try:
        info['uptime'] = float(_read('/proc/uptime').split()[0])
    except (ValueError, IndexError):
        info['uptime'] = None
    return info


def power_status(flags):
    """(colour name, text) for vcgencmd get_throttled flags."""
    if flags is None:
        return 'dim', 'Unknown'
    if flags & 0x1:
        return 'bad', 'Power supply too weak right now'
    if flags & 0x4 or flags & 0x8:
        return 'warn', 'Slowed down (too hot or low power)'
    if flags & 0x10000:
        return 'warn', 'Power supply was too weak since the Pi started'
    if flags & 0x40000 or flags & 0x80000:
        return 'warn', 'Slowed down since the Pi started (too hot)'
    return 'good', 'OK'


def plural(n, word):
    """'1 minute', '2 minutes'."""
    return '%d %s%s' % (n, word, '' if n == 1 else 's')


def fmt_uptime(seconds):
    if seconds is None:
        return 'Unknown'
    m = int(seconds // 60)
    d, h, m = m // 1440, (m // 60) % 24, m % 60
    if d:
        return '%s, %s' % (plural(d, 'day'), plural(h, 'hour'))
    if h:
        return '%s, %s' % (plural(h, 'hour'), plural(m, 'minute'))
    return plural(m, 'minute')


# ---------------------------------------------------------------------------
# Reading the helper's output
# ---------------------------------------------------------------------------
def apt_progress(line):
    """(percent, description) from an apt Status-Fd line, or None."""
    if line.startswith(('pmstatus:', 'dlstatus:')):
        parts = line.split(':', 3)
        if len(parts) == 4:
            try:
                return float(parts[2]), parts[3]
            except ValueError:
                return None
    return None


def find_url(line):
    m = URL_RE.search(line)
    return m.group(0).rstrip('",}') if m else None
