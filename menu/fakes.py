"""Fake Wi-Fi, Bluetooth, phone setup and settings, for trying the menu without real hardware.

Used with `moonlight_menu.py --fake`. Not installed on the Pi. Set FAKE_NO_PAIRED=1
to start with no paired controllers (to see auto-pairing), FAKE_QUICK_SETUP=1 to
start with Quick setup, and FAKE_TAILSCALE to a Tailscale state (Running, Stopped,
NeedsLogin, or none for not installed).
"""
import os
import time

from network import NetworkError, key_mgmt_for, validate_password
from bluetooth import BluetoothError, needs_pairing
from settings import SettingsError


class FakeNetwork:
    wifi_dev = 'wlan0'

    def __init__(self):
        self.enabled = True
        self.current = None
        self.saved_list = [{'name': 'Grandma', 'uuid': 'u-grandma', 'ssid': 'Grandma'}]
        self.visible = [
            {'ssid': 'HomeNet 5G', 'signal': 82, 'security': 'WPA2'},
            {'ssid': 'Grandma', 'signal': 64, 'security': 'WPA2 WPA3'},
            {'ssid': 'CoffeeShop Guest', 'signal': 40, 'security': ''},
            {'ssid': 'Office', 'signal': 35, 'security': 'WPA2 802.1X'},
            {'ssid': 'Neighbour_2.4', 'signal': 18, 'security': 'WPA2'},
        ]

    def state(self):
        return 'connected' if self.current else 'disconnected'

    def online(self):
        return self.current is not None

    def status(self):
        return {'state': self.state(), 'hotspot': False, 'tailscale': '100.64.0.7' if self.current else '',
                'online': self.current is not None, 'internet': 'full' if self.current else 'unknown',
                'ethernet': {'device': 'eth0', 'connected': False, 'ip': ''},
                'wifi': {'device': 'wlan0', 'state': 'connected' if self.current else 'disconnected',
                         'connected': self.current is not None, 'connection': self.current or '',
                         'ip': '192.168.1.42' if self.current else '', 'ssid': self.current or '',
                         'signal': 82 if self.current else 0, 'enabled': self.enabled}}

    def wifi_enabled(self):
        return self.enabled

    def set_wifi_enabled(self, on):
        time.sleep(0.6)
        self.enabled = on
        if not on:
            self.current = None

    def scan(self, rescan=True):
        time.sleep(float(os.environ.get('FAKE_SCAN_SECONDS', 1.2)))
        saved = {s['ssid'] for s in self.saved_list}
        nets = [dict(n, in_use=n['ssid'] == self.current, saved=n['ssid'] in saved) for n in self.visible]
        return sorted(nets, key=lambda n: (not n['in_use'], not n['saved'], -n['signal']))

    def saved(self):
        return list(self.saved_list)

    def active_wifi_uuid(self):
        for s in self.saved_list:
            if s['ssid'] == self.current:
                return s['uuid']
        return None

    def connect_saved(self, uid):
        time.sleep(1)
        self.current = next(s['ssid'] for s in self.saved_list if s['uuid'] == uid)

    def connect_new(self, ssid, password, security, hidden=False):
        time.sleep(1.5)
        if key_mgmt_for(security) and validate_password(security, password):
            raise NetworkError(validate_password(security, password))
        if password == 'wrongpassword':
            raise NetworkError('Wrong password?')
        self.saved_list = [s for s in self.saved_list if s['ssid'] != ssid]
        self.saved_list.append({'name': ssid, 'uuid': 'u-' + ssid, 'ssid': ssid})
        self.current = ssid

    def disconnect(self, uid):
        self.current = None

    def forget(self, uid):
        self.saved_list = [s for s in self.saved_list if s['uuid'] != uid]
        if self.active_wifi_uuid() is None:
            self.current = None


class FakeBluetooth:
    def __init__(self):
        self.discovering = False
        self.devs = [
            {'address': 'AA:BB:CC:00:00:01', 'name': 'DualSense Wireless Controller', 'icon': 'input-gaming',
             'class': 0x2508, 'paired': True, 'bonded': True, 'trusted': True, 'connected': True, 'rssi': None, 'battery': 70,
             'gamepad': True, 'path': None},
            {'address': 'AA:BB:CC:00:00:02', 'name': 'Xbox Wireless Controller', 'icon': 'input-gaming',
             'class': 0x2508, 'paired': True, 'bonded': True, 'trusted': True, 'connected': False, 'rssi': None, 'battery': None,
             'gamepad': True, 'path': None},
        ]
        if os.environ.get('FAKE_NO_PAIRED'):
            self.devs = []
        if os.environ.get('FAKE_UNBONDED'):
            self.devs[0].update(bonded=False, connected=False, battery=None)
        self.started = None

    def available(self):
        return True

    def devices(self):
        devs = [dict(d) for d in self.devs]
        if self.discovering and self.started and time.monotonic() - self.started > 2:
            devs.append({'address': 'AA:BB:CC:00:00:09', 'name': 'Wireless Controller', 'icon': 'input-gaming',
                         'class': 0x2508, 'paired': False, 'bonded': False, 'trusted': False, 'connected': False, 'rssi': -52,
                         'battery': None, 'gamepad': True, 'path': None})
        return devs

    def start_discovery(self):
        self.discovering = True
        self.started = time.monotonic()

    def stop_discovery(self):
        self.discovering = False

    def pairable_gamepads(self):
        return [d for d in self.devices() if needs_pairing(d) and d['rssi'] is not None]

    def pair(self, address):
        time.sleep(2)
        self.discovering = False
        self.devs.append({'address': address, 'name': 'Wireless Controller', 'icon': 'input-gaming',
                          'class': 0x2508, 'paired': True, 'bonded': True, 'trusted': True, 'connected': True, 'rssi': None,
                          'battery': 45, 'gamepad': True, 'path': None})

    def _find(self, address):
        for d in self.devs:
            if d['address'] == address:
                return d
        raise BluetoothError('No such device')

    def connect(self, address):
        time.sleep(1)
        self._find(address)['connected'] = True

    def disconnect(self, address):
        self._find(address)['connected'] = False

    def forget(self, address):
        self.devs = [d for d in self.devs if d['address'] != address]

    def close(self):
        pass


class FakePhoneSetup:
    """Walks through starting -> ready, and stays ready."""

    def __init__(self):
        self.t0 = None

    def start(self):
        self.t0 = time.time()

    def stop(self):
        self.t0 = None

    def status(self, since):
        if self.t0 is None:
            return None
        base = {'updated': self.t0 + 1, 'hotspot_ssid': 'Moonlight-Setup-90AB', 'hotspot_password': 'k7m2p9qa',
                'url': 'http://10.42.0.1', 'last_error': ''}
        if time.time() - self.t0 < 1.5:
            return dict(base, state='starting')
        return dict(base, state='ready')


class FakeSettings:
    KEYS = {'quit-on-shutdown': 'quit_on_shutdown', 'usb-handoff': 'usb_handoff', 'quiet-boot': 'quiet_boot',
            'wifi-powersave': 'wifi_powersave'}

    def __init__(self):
        self.st = {'speakers': '5.1', 'quit_on_shutdown': '1', 'virtualhere': '1', 'usb_handoff': '1',
                   'quiet_boot': '1', 'wifi': '1', 'wifi_powersave': '0', 'wifi_country': 'GB', 'tailscale': '1'}
        self.pending = bool(os.environ.get('FAKE_QUICK_SETUP'))
        state = os.environ.get('FAKE_TAILSCALE', 'Running')
        self.ts = {'installed': state != 'none', 'state': state if state != 'none' else '',
                   'ip': '100.101.102.103', 'name': 'moonlight-pi', 'tailnet': 'example@gmail.com', 'online': True}

    def available(self):
        return True

    def status(self):
        time.sleep(0.2)
        return dict(self.st)

    def set(self, name, value):
        time.sleep(0.5)
        if name == 'wifi-country':
            self.st['wifi_country'] = value
        else:
            self.st[self.KEYS[name]] = '1' if value == 'on' else '0'
        return 'ok'

    def speakers(self, mode):
        time.sleep(0.3)
        self.st['speakers'] = mode

    def speaker_test(self, mode):
        time.sleep(float(os.environ.get('FAKE_TEST_SECONDS', 2)))
        if os.environ.get('FAKE_TEST_FAIL'):
            raise SettingsError('Channels count (6) not available for playbacks: Invalid argument')

    def quick_setup_pending(self):
        return self.pending

    def finish_quick_setup(self):
        self.pending = False

    def setup_version(self):
        return '1.8.0'

    def tailscale_status(self):
        time.sleep(0.1)
        return dict(self.ts)

    def tailscale(self, cmd):
        time.sleep(0.5)
        self.ts['state'] = 'Stopped' if cmd == 'down' else 'NeedsLogin'

    def stream(self, args, on_line, root=True):
        if args == ['tailscale', 'up']:
            time.sleep(1)
            if self.ts['state'] != 'Stopped':
                on_line('{"AuthURL": "https://login.tailscale.com/a/1a2b3c4d5e6f7a", "BackendState": "NeedsLogin"}')
                time.sleep(float(os.environ.get('FAKE_LOGIN_SECONDS', 6)))
            self.ts['state'] = 'Running'
            return True, ''
        if args == ['tailscale', 'install']:
            for line in ('Downloading and installing Tailscale…', 'Installing Tailscale for debian trixie',
                         '+ apt-get install -y tailscale', 'Setting up tailscale (1.104.1) ...', 'Tailscale installed'):
                on_line(line)
                time.sleep(0.6)
            self.ts.update(installed=True, state='NeedsLogin')
            return True, ''
        if args == ['updates', 'install']:
            pkgs = ['moonlight-qt', 'libc6', 'raspi-firmware']
            for i in range(60):
                on_line('pmstatus:%s:%.1f:Installing %s' % (pkgs[i // 20], i * 100 / 60, pkgs[i // 20]))
                if i % 6 == 0:
                    on_line('Unpacking %s ...' % pkgs[i // 20])
                time.sleep(0.1)
            return True, ''
        if args == ['update-setup']:
            for line in ('Downloading the latest version…', 'Installing version 1.9.0…', '==> Checking your system',
                         '    [OK] Raspberry Pi OS Lite', '==> Installing required packages',
                         '==> Installing the TV menu and settings', '==> All done'):
                on_line(line)
                time.sleep(0.7)
            return True, ''
        return False, 'Unknown command'

    def check_updates(self):
        time.sleep(1.5)
        return 3, ['moonlight-qt', 'libc6', 'raspi-firmware']

    def latest_version(self):
        time.sleep(0.8)
        return os.environ.get('FAKE_LATEST', '1.9.0')

    def about(self):
        time.sleep(0.1)
        return {'hostname': 'moonlight-pi', 'version': '1.8.0', 'model': 'Raspberry Pi 5 Model B Rev 1.0',
                'os': 'Debian GNU/Linux 13 (trixie)', 'kernel': '6.12.47+rpt-rpi-2712', 'moonlight': '6.2.0-1',
                'temp': 61.4, 'throttled': int(os.environ.get('FAKE_THROTTLED', '0'), 16),
                'disk_free': 21.3e9, 'disk_total': 31.1e9, 'uptime': 5 * 3600 + 17 * 60}
