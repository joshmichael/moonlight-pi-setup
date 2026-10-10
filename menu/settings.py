"""The menu's Settings: reads and changes them through moonlight-settings.

moonlight-settings is the root-owned helper that the setup script uses too, so
each setting is applied in one place. The menu runs it with sudo (a sudoers
rule allows exactly that helper without a password) for the settings that
need it.
"""
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import urllib.request

HELPER = '/usr/local/lib/moonlight-pi-setup/moonlight-settings'
STATE_FILE = os.path.expanduser('~/.local/share/moonlight-pi-setup/state')
COUNTRIES = '/usr/share/zoneinfo/iso3166.tab'
ZONES = '/usr/share/zoneinfo/zone1970.tab'
LATEST_SCRIPT_URL = 'https://raw.githubusercontent.com/joshmichael/moonlight-pi-setup/main/moonlight-pi-setup.sh'

SPEAKER_NAMES = {'stereo': 'Stereo', '5.1': '5.1 surround', '7.1': '7.1 surround'}
SPEAKER_CHANNELS = {'stereo': 2, '5.1': 6, '7.1': 8}

# Some names in the time zone database are shorter than people expect.
COUNTRY_NAMES = {'GB': 'United Kingdom', 'US': 'United States', 'KR': 'South Korea', 'KP': 'North Korea'}

URL_RE = re.compile(r'https://[^\s"\']+')


class SettingsError(Exception):
    pass


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


def version_tuple(v):
    return tuple(int(p) for p in re.findall(r'\d+', v or '')) or (0,)


# ---------------------------------------------------------------------------
# Countries (for the Wi-Fi country)
# ---------------------------------------------------------------------------
def countries():
    """[(code, name)] sorted by name."""
    out = []
    try:
        with open(COUNTRIES) as f:
            for line in f:
                if line.startswith('#') or '\t' not in line:
                    continue
                code, name = line.rstrip('\n').split('\t', 1)
                out.append((code, COUNTRY_NAMES.get(code, name)))
    except OSError:
        pass
    return sorted(out, key=lambda c: c[1].lower())


def country_name(code):
    for c, name in countries():
        if c == code:
            return name
    return code


def guess_country():
    """The country of the Pi's time zone, if it's set."""
    try:
        zone = subprocess.run(['timedatectl', 'show', '-p', 'Timezone', '--value'], capture_output=True,
                              text=True, timeout=5).stdout.strip()
        with open(ZONES) as f:
            for line in f:
                parts = line.split('\t')
                if len(parts) >= 3 and parts[2].strip() == zone:
                    return parts[0].split(',')[0]
    except (OSError, subprocess.SubprocessError):
        pass
    return ''


# ---------------------------------------------------------------------------
# HDMI audio
# ---------------------------------------------------------------------------
def hdmi_card():
    """The ALSA card of the HDMI port with a screen connected (like ~/.bash_profile)."""
    for port in (1, 2):
        for path in glob.glob('/sys/class/drm/card*-HDMI-A-%d/status' % port):
            try:
                with open(path) as f:
                    if f.read().strip() == 'connected':
                        return 'vc4hdmi%d' % (port - 1)
            except OSError:
                pass
    return 'vc4hdmi%s' % (state_get('hdmi_port') or '0')


# ---------------------------------------------------------------------------
# The backend
# ---------------------------------------------------------------------------
class Settings:
    def __init__(self):
        self.lock = threading.Lock()

    def available(self):
        return os.access(HELPER, os.X_OK)

    def _run(self, args, root=False, timeout=180):
        if not self.available():
            raise SettingsError("The settings helper isn't installed. Run the setup script again.")
        cmd = (['sudo', '-n', HELPER] if root else [HELPER]) + list(args)
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise SettingsError('That took too long. Try again.')
        if p.returncode != 0:
            raise SettingsError(error_text(p.stdout + '\n' + p.stderr))
        return p.stdout

    def stream(self, args, on_line, root=True):
        """Runs the helper, calling on_line(line) for each line of output.
        Returns (ok, error message)."""
        if not self.available():
            return False, "The settings helper isn't installed. Run the setup script again."
        cmd = (['sudo', '-n', HELPER] if root else [HELPER]) + list(args)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                bufsize=1, errors='replace')
        tail = []
        for line in proc.stdout:
            line = line.rstrip('\n')
            tail = (tail + [line])[-40:]
            on_line(line)
        rc = proc.wait()
        return rc == 0, '' if rc == 0 else error_text('\n'.join(tail))

    # -- settings ------------------------------------------------------------
    def status(self):
        st = {}
        for line in self._run(['status']).splitlines():
            k, sep, v = line.partition('=')
            if sep:
                st[k] = v
        return st

    def set(self, name, value):
        """name: quit-on-shutdown, usb-handoff, quiet-boot, wifi-powersave
        (value on/off) or wifi-country (value XX)."""
        with self.lock:
            return self._run([name, value], root=True)

    def speakers(self, mode):
        with self.lock:
            return self._run(['speakers', mode])

    def speaker_test(self, mode):
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

    # -- Quick setup -----------------------------------------------------------
    def quick_setup_pending(self):
        return state_get('quick_setup') == '1'

    def finish_quick_setup(self):
        state_set('quick_setup', '0')

    def setup_version(self):
        return state_get('version')

    # -- Tailscale ---------------------------------------------------------------
    def tailscale_status(self):
        """{'installed', 'state', 'ip', 'name', 'tailnet', 'online'}. state is
        Tailscale's BackendState (Running, Stopped, NeedsLogin, ...) or
        'NotRunning' if its service isn't running."""
        if not shutil.which('tailscale'):
            return {'installed': False, 'state': ''}
        try:
            p = subprocess.run(['tailscale', 'status', '--json'], capture_output=True, text=True, timeout=10)
            d = json.loads(p.stdout or '{}')
        except (OSError, ValueError, subprocess.SubprocessError):
            return {'installed': True, 'state': 'NotRunning'}
        me = d.get('Self') or {}
        ips = [ip for ip in (d.get('TailscaleIPs') or []) if '.' in ip]
        return {'installed': True, 'state': d.get('BackendState') or 'NotRunning',
                'ip': ips[0] if ips else '', 'name': (me.get('HostName') or ''),
                'tailnet': ((d.get('CurrentTailnet') or {}).get('Name') or ''),
                'online': bool(me.get('Online'))}

    def tailscale(self, cmd):
        """cmd: down or logout. (Signing in streams its output: see stream().)"""
        with self.lock:
            return self._run(['tailscale', cmd], root=True, timeout=60)

    # -- updates ---------------------------------------------------------------
    def check_updates(self):
        """(number of updates, [package names])."""
        out = self._run(['updates', 'check'], root=True, timeout=600)
        n, pkgs = 0, []
        for line in out.splitlines():
            if line.startswith('UPDATES='):
                n = int(line[8:] or 0)
            elif line.startswith('PACKAGES='):
                pkgs = line[9:].split()
        return n, pkgs

    def latest_version(self):
        """The newest setup version on GitHub."""
        with urllib.request.urlopen(LATEST_SCRIPT_URL, timeout=15) as r:
            for raw in r:
                m = re.match(r'SCRIPT_VERSION="([0-9.]+)"', raw.decode('utf-8', 'replace'))
                if m:
                    return m.group(1)
        raise SettingsError("Couldn't find the latest version.")

    # -- About -------------------------------------------------------------------
    def about(self):
        info = {'hostname': socket.gethostname(), 'version': self.setup_version()}
        info['model'] = _read('/proc/device-tree/model').strip('\0 \n')
        info['os'] = ''
        for line in _read('/etc/os-release').splitlines():
            if line.startswith('PRETTY_NAME='):
                info['os'] = line.split('=', 1)[1].strip('"')
        info['kernel'] = os.uname().release
        try:
            info['moonlight'] = subprocess.run(['dpkg-query', '-W', '-f=${Version}', 'moonlight-qt'],
                                               capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            info['moonlight'] = ''
        try:
            info['temp'] = int(_read('/sys/class/thermal/thermal_zone0/temp').strip()) / 1000
        except ValueError:
            info['temp'] = None
        info['throttled'] = None
        try:
            out = subprocess.run(['vcgencmd', 'get_throttled'], capture_output=True, text=True,
                                 timeout=5).stdout
            m = re.search(r'0x[0-9a-fA-F]+', out)
            if m:
                info['throttled'] = int(m.group(0), 16)
        except (OSError, subprocess.SubprocessError):
            pass
        du = shutil.disk_usage('/')
        info['disk_free'], info['disk_total'] = du.free, du.total
        try:
            info['uptime'] = float(_read('/proc/uptime').split()[0])
        except (ValueError, IndexError):
            info['uptime'] = None
        return info


def _read(path):
    try:
        with open(path, errors='replace') as f:
            return f.read()
    except OSError:
        return ''


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


def fmt_uptime(seconds):
    if seconds is None:
        return 'Unknown'
    m = int(seconds // 60)
    d, h, m = m // 1440, (m // 60) % 24, m % 60
    if d:
        return '%d day%s, %d hour%s' % (d, '' if d == 1 else 's', h, '' if h == 1 else 's')
    if h:
        return '%d hour%s, %d minute%s' % (h, '' if h == 1 else 's', m, '' if m == 1 else 's')
    return '%d minute%s' % (m, '' if m == 1 else 's')


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
