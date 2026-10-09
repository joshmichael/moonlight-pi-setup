"""Network status and Wi-Fi control through NetworkManager.

Uses nmcli for most things. New Wi-Fi profiles are added over D-Bus, so the
password never appears on a command line (where other programs could see it).
"""
import logging
import subprocess
import time
import uuid

log = logging.getLogger(__name__)

HOTSPOT_NAME = 'moonlight-setup-hotspot'


class NetworkError(Exception):
    pass


def run(args, timeout=20):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, '', '%s not found' % args[0]
    except subprocess.TimeoutExpired:
        return 124, '', 'timed out'
    return p.returncode, p.stdout, p.stderr.strip()


def split_terse(line):
    """Splits a line of `nmcli -t` output on unescaped colons."""
    fields, cur, esc = [], '', False
    for ch in line:
        if esc:
            cur += ch
            esc = False
        elif ch == '\\':
            esc = True
        elif ch == ':':
            fields.append(cur)
            cur = ''
        else:
            cur += ch
    fields.append(cur)
    return fields


def nmcli_rows(args, timeout=20):
    rc, out, err = run(['nmcli', '-t'] + args, timeout)
    if rc != 0:
        raise NetworkError(err or 'nmcli failed')
    return [split_terse(line) for line in out.splitlines() if line]


def key_mgmt_for(security):
    """NetworkManager key management for an nmcli SECURITY string, or None if open."""
    sec = (security or '').upper()
    if sec in ('', '--'):
        return None
    if '802.1X' in sec:
        raise NetworkError("This network needs a username and password (enterprise Wi-Fi), "
                           "which isn't supported here.")
    if 'WEP' in sec:
        return 'none'
    if 'WPA3' in sec and 'WPA2' not in sec and 'WPA1' not in sec:
        return 'sae'
    return 'wpa-psk'


def validate_password(security, password):
    """An error message if password can't be right for this kind of network, else ''."""
    km = key_mgmt_for(security)
    if km is None:
        return ''
    if km == 'none':
        if len(password) in (5, 13) or (len(password) in (10, 26) and _is_hex(password)):
            return ''
        return 'WEP passwords are 5 or 13 characters (or 10 or 26 hex digits).'
    if not 8 <= len(password) <= 63:
        return 'Wi-Fi passwords are 8 to 63 characters.'
    if any(not 32 <= ord(c) <= 126 for c in password):
        return 'Wi-Fi passwords can only use letters, numbers and standard symbols.'
    return ''


def _is_hex(s):
    return all(c in '0123456789abcdefABCDEF' for c in s)


def friendly_error(err):
    e = err.lower()
    if 'secrets were required' in e or 'no secrets' in e or '(7)' in e or 'supplicant' in e:
        return 'Wrong password?'
    if 'no network with ssid' in e or 'not found' in e:
        return "Couldn't find that network. Is it in range?"
    if 'timed out' in e or 'timeout' in e:
        return 'It took too long to connect.'
    if 'ip configuration could not be reserved' in e or 'dhcp' in e:
        return "Connected, but the network didn't give the Pi an address."
    if 'not authorized' in e or 'insufficient privileges' in e:
        return "The Pi didn't allow this change (permissions)."
    return err.replace('Error: ', '').strip() or 'Unknown error'


class Network:
    def __init__(self):
        self.wifi_dev = None
        try:
            for dev, typ in nmcli_rows(['-f', 'DEVICE,TYPE', 'device']):
                if typ == 'wifi':
                    self.wifi_dev = dev
                    break
        except NetworkError as e:
            log.warning('nmcli: %s', e)

    # -- status -------------------------------------------------------------
    def state(self):
        try:
            return nmcli_rows(['-f', 'STATE', 'general'])[0][0]
        except (NetworkError, IndexError):
            return 'unknown'

    def online(self):
        """True when the Pi is on a network (internet not required, LAN is enough)."""
        return self.state().startswith('connected')

    def wait_startup(self, timeout):
        run(['nm-online', '-s', '-q', '-t', str(int(timeout))], timeout + 5)

    def _ip(self, dev):
        rc, out, _ = run(['nmcli', '-g', 'IP4.ADDRESS', 'device', 'show', dev])
        if rc != 0 or not out.strip():
            return ''
        return out.strip().split(' | ')[0].split('/')[0]

    def status(self):
        st = {'state': self.state(), 'ethernet': None, 'wifi': None, 'tailscale': '', 'hotspot': False}
        try:
            devices = nmcli_rows(['-f', 'DEVICE,TYPE,STATE,CONNECTION', 'device'])
        except NetworkError:
            devices = []
        for dev, typ, state, conn in devices:
            if typ == 'ethernet' and st['ethernet'] is None:
                connected = state == 'connected'
                st['ethernet'] = {'device': dev, 'connected': connected,
                                  'ip': self._ip(dev) if connected else ''}
            elif typ == 'wifi' and dev == self.wifi_dev:
                connected = state == 'connected'
                st['wifi'] = {'device': dev, 'state': state, 'connected': connected and conn != HOTSPOT_NAME,
                              'connection': conn, 'ip': self._ip(dev) if connected else '',
                              'ssid': '', 'signal': 0, 'enabled': self.wifi_enabled()}
                st['hotspot'] = conn == HOTSPOT_NAME
        w = st['wifi']
        if w and w['connected']:
            try:
                for active, ssid, sig in nmcli_rows(['-f', 'ACTIVE,SSID,SIGNAL', 'device', 'wifi',
                                                     'list', '--rescan', 'no']):
                    if active == 'yes':
                        w['ssid'], w['signal'] = ssid, int(sig or 0)
                        break
            except (NetworkError, ValueError):
                pass
            if not w['ssid']:
                w['ssid'] = w['connection']
        rc, out, _ = run(['tailscale', 'ip', '-4'], 3)
        if rc == 0:
            st['tailscale'] = out.strip().splitlines()[0] if out.strip() else ''
        return st

    def wifi_enabled(self):
        rc, out, _ = run(['nmcli', 'radio', 'wifi'])
        return rc == 0 and out.strip() == 'enabled'

    def set_wifi_enabled(self, on):
        rc, _, err = run(['nmcli', 'radio', 'wifi', 'on' if on else 'off'])
        if rc != 0:
            raise NetworkError(friendly_error(err))
        if on:
            # Give the radio a moment to come up before anyone scans.
            for _ in range(10):
                try:
                    states = [s for d, s in nmcli_rows(['-f', 'DEVICE,STATE', 'device']) if d == self.wifi_dev]
                except NetworkError:
                    states = []
                if states and states[0] != 'unavailable':
                    break
                time.sleep(0.5)

    # -- Wi-Fi networks -----------------------------------------------------
    def scan(self, rescan=True):
        """Visible networks, strongest first, one entry per name."""
        if not self.wifi_dev:
            raise NetworkError('This Pi has no Wi-Fi.')
        args = ['-f', 'IN-USE,SSID,SIGNAL,SECURITY', 'device', 'wifi', 'list', 'ifname', self.wifi_dev]
        try:
            rows = nmcli_rows(args + ['--rescan', 'yes' if rescan else 'no'], 40)
        except NetworkError:
            rows = nmcli_rows(args + ['--rescan', 'auto'], 40)
        saved = {n['ssid'] for n in self.saved()}
        best = {}
        for in_use, ssid, sig, sec in rows:
            if not ssid:
                continue
            try:
                sig = int(sig)
            except ValueError:
                sig = 0
            cur = best.get(ssid)
            entry = {'ssid': ssid, 'signal': sig, 'security': sec if sec != '--' else '',
                     'in_use': in_use == '*', 'saved': ssid in saved}
            if cur is None or sig > cur['signal']:
                if cur and cur['in_use']:
                    entry['in_use'] = True
                best[ssid] = entry
            elif in_use == '*':
                cur['in_use'] = True
        return sorted(best.values(), key=lambda n: (not n['in_use'], not n['saved'], -n['signal']))

    def saved(self):
        """Saved Wi-Fi profiles (not counting the setup hotspot)."""
        result = []
        try:
            rows = nmcli_rows(['-f', 'NAME,UUID,TYPE', 'connection', 'show'])
        except NetworkError:
            return result
        for name, uid, typ in rows:
            if typ != '802-11-wireless' or name == HOTSPOT_NAME:
                continue
            rc, out, _ = run(['nmcli', '-g', '802-11-wireless.ssid', 'connection', 'show', uid])
            result.append({'name': name, 'uuid': uid, 'ssid': out.strip() if rc == 0 else name})
        return result

    def active_wifi_uuid(self):
        try:
            for uid, typ, dev in nmcli_rows(['-f', 'UUID,TYPE,DEVICE', 'connection', 'show', '--active']):
                if typ == '802-11-wireless' and dev == self.wifi_dev:
                    return uid
        except NetworkError:
            pass
        return None

    def connect_saved(self, uid):
        rc, _, err = run(['nmcli', '--wait', '45', 'connection', 'up', 'uuid', uid], 60)
        if rc != 0:
            raise NetworkError(friendly_error(err))

    def connect_new(self, ssid, password, security, hidden=False):
        """Saves a new Wi-Fi profile and connects to it. Removes it again if that fails."""
        km = key_mgmt_for(security)
        err = validate_password(security, password or '') if km else ''
        if err:
            raise NetworkError(err)
        # Re-entering the password for a saved network replaces the old profile.
        for n in self.saved():
            if n['ssid'] == ssid:
                run(['nmcli', 'connection', 'delete', 'uuid', n['uuid']])
        uid = self._add_profile(ssid, password, km, hidden)
        rc, _, err = run(['nmcli', '--wait', '45', 'connection', 'up', 'uuid', uid], 60)
        if rc != 0:
            run(['nmcli', 'connection', 'delete', 'uuid', uid])
            raise NetworkError(friendly_error(err))

    def _add_profile(self, ssid, password, km, hidden):
        import dbus
        uid = str(uuid.uuid4())
        s = dbus.Dictionary(signature='sa{sv}')
        s['connection'] = dbus.Dictionary({
            'id': ssid, 'uuid': uid, 'type': '802-11-wireless',
            'interface-name': self.wifi_dev, 'autoconnect': True}, signature='sv')
        s['802-11-wireless'] = dbus.Dictionary({
            'ssid': dbus.ByteArray(ssid.encode()), 'mode': 'infrastructure',
            'hidden': bool(hidden)}, signature='sv')
        if km == 'none':
            key_type = 1 if len(password) in (5, 13, 10, 26) else 2
            s['802-11-wireless-security'] = dbus.Dictionary({
                'key-mgmt': 'none', 'auth-alg': 'open', 'wep-key0': password,
                'wep-key-type': dbus.UInt32(key_type)}, signature='sv')
        elif km:
            s['802-11-wireless-security'] = dbus.Dictionary({
                'key-mgmt': km, 'psk': password}, signature='sv')
        s['ipv4'] = dbus.Dictionary({'method': 'auto'}, signature='sv')
        s['ipv6'] = dbus.Dictionary({'method': 'auto'}, signature='sv')
        try:
            bus = dbus.SystemBus()
            settings = dbus.Interface(
                bus.get_object('org.freedesktop.NetworkManager', '/org/freedesktop/NetworkManager/Settings'),
                'org.freedesktop.NetworkManager.Settings')
            settings.AddConnection(s)
        except dbus.exceptions.DBusException as e:
            raise NetworkError(friendly_error(e.get_dbus_message() or str(e)))
        return uid

    def disconnect(self, uid):
        rc, _, err = run(['nmcli', 'connection', 'down', 'uuid', uid])
        if rc != 0:
            raise NetworkError(friendly_error(err))

    def forget(self, uid):
        rc, _, err = run(['nmcli', 'connection', 'delete', 'uuid', uid])
        if rc != 0:
            raise NetworkError(friendly_error(err))
