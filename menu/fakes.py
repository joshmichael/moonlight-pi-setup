"""Fake Wi-Fi, Bluetooth and phone setup, for trying the menu without real hardware.

Used with `moonlight_menu.py --fake`. Not installed on the Pi. Set FAKE_NO_PAIRED=1
to start with no paired controllers (to see auto-pairing).
"""
import os
import time

from network import NetworkError, key_mgmt_for, validate_password
from bluetooth import BluetoothError, needs_pairing


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
