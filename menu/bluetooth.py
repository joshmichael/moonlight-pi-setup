"""Bluetooth controller pairing through BlueZ (D-Bus).

Controllers use one Bluetooth adapter: the Pi's built-in one, or a USB adapter
chosen in Controllers > Bluetooth adapter (saved by the settings helper in
BT_CONFIG). Other adapters, like one that USB sharing sends to the PC, are
left alone.
"""
import glob
import logging
import os
import re
import threading
import time

log = logging.getLogger(__name__)

SYS_BT = '/sys/class/bluetooth'
BT_CONFIG = '/var/lib/moonlight-pi-setup/bluetooth.conf'    # written by the settings helper
ADDRESS_RE = re.compile(r'^([0-9A-F]{2}:){5}[0-9A-F]{2}$')
USB_INTERFACE_RE = re.compile(r'^\d+-[\d.]+:\d+\.\d+$')

BLUEZ = 'org.bluez'
ADAPTER = 'org.bluez.Adapter1'
DEVICE = 'org.bluez.Device1'
BATTERY = 'org.bluez.Battery1'
PROPS = 'org.freedesktop.DBus.Properties'
OBJECT_MANAGER = 'org.freedesktop.DBus.ObjectManager'
AGENT_MANAGER = 'org.bluez.AgentManager1'
AGENT_PATH = '/org/moonlightpi/agent'

GAMEPAD_WORDS = ('controller', 'gamepad', 'joystick', 'xbox', 'dualsense', 'dualshock',
                 'joy-con', '8bitdo', 'pro controller', 'stadia')


class BluetoothError(Exception):
    pass


def is_gamepad(dev):
    if dev.get('icon') == 'input-gaming':
        return True
    cls = dev.get('class') or 0
    # Peripheral major class with the joystick or gamepad minor bits.
    if (cls >> 8) & 0x1F == 0x05 and (cls >> 2) & 0x0F in (1, 2):
        return True
    name = (dev.get('name') or '').lower()
    return dev.get('icon') in (None, '', 'input-gaming') and any(w in name for w in GAMEPAD_WORDS)


def needs_pairing(dev):
    """A controller that isn't paired, or is paired without a saved key."""
    return dev['gamepad'] and (not dev['paired'] or not dev.get('bonded', True))


def battery_from_sysfs(address):
    """Battery level some kernel drivers (e.g. DualSense) report, or None."""
    mac = address.lower()
    for path in glob.glob('/sys/class/power_supply/*'):
        if mac in os.path.basename(path).lower():
            try:
                with open(os.path.join(path, 'capacity')) as f:
                    return int(f.read().strip())
            except (OSError, ValueError):
                return None
    return None


def friendly_error(e):
    name = e.get_dbus_name() if hasattr(e, 'get_dbus_name') else ''
    msg = (e.get_dbus_message() if hasattr(e, 'get_dbus_message') else str(e)) or ''
    if name.endswith('AuthenticationTimeout') or name.endswith('NoReply') or 'timeout' in msg.lower():
        return 'The controller stopped responding. Put it back in pairing mode and try again.'
    if name.endswith('AuthenticationFailed') or name.endswith('AuthenticationRejected'):
        return 'The controller refused to pair. Put it back in pairing mode and try again.'
    if name.endswith('ConnectionAttemptFailed') or 'page timeout' in msg.lower():
        return "Couldn't reach the controller. Is it switched on and close by?"
    if name.endswith('InProgress'):
        return 'Already busy. Wait a moment and try again.'
    if name.endswith('DoesNotExist') or name.endswith('UnknownObject'):
        return 'The controller is no longer visible. Put it in pairing mode again.'
    return msg or name or 'Unknown Bluetooth error'


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ''


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------
def load_choice(path=BT_CONFIG):
    """The USB adapter chosen for controllers: {'address': its Bluetooth
    address, 'key': its USB sharing key}, or {} for the built-in one."""
    choice = {}
    for line in _read(path).splitlines():
        k, sep, v = line.partition('=')
        if sep and k in ('address', 'key'):
            choice[k] = v.strip()
    return choice if ADDRESS_RE.match(choice.get('address', '')) else {}


def usb_address(hci):
    """The USB address (like 3-2) of the device an adapter is part of, or ''
    for an adapter that isn't USB (the Pi's built-in one)."""
    dev = os.path.basename(os.path.realpath(os.path.join(SYS_BT, hci, 'device')))
    return dev.split(':')[0] if USB_INTERFACE_RE.match(dev) else ''


def blocked(hci):
    """Whether an adapter's radio is switched off (rfkill)."""
    return any(_read(p) == '1' for p in glob.glob('%s/%s/rfkill*/soft' % (SYS_BT, hci)) +
               glob.glob('%s/%s/rfkill*/hard' % (SYS_BT, hci)))


def hci_address(hci):
    """An adapter's Bluetooth address, from BlueZ ('' if BlueZ doesn't know it yet)."""
    try:
        import dbus
        obj = dbus.SystemBus().get_object(BLUEZ, '/org/bluez/' + hci)
        return str(dbus.Interface(obj, PROPS).Get(ADAPTER, 'Address')).upper()
    except Exception:
        return ''


def adapters(objects):
    """The adapters BlueZ knows (objects: its GetManagedObjects), the built-in
    one first, as dicts: path, hci, address, usb (its USB address, '' for the
    built-in one), key (its key in USB sharing's settings), name, blocked."""
    import usb_sharing
    out = []
    for path, ifaces in objects.items():
        a = ifaces.get(ADAPTER)
        if a is None:
            continue
        hci = os.path.basename(str(path))
        usb = usb_address(hci)
        item = {'path': str(path), 'hci': hci, 'address': str(a.get('Address', '')).upper(), 'usb': usb,
                'key': '', 'name': 'Built-in Bluetooth', 'blocked': blocked(hci)}
        if usb:
            d = os.path.join(usb_sharing.SYS_USB, usb)
            vid, pid = _read(d + '/idVendor').lower(), _read(d + '/idProduct').lower()
            item['key'] = usb_sharing.device_key('%s:%s' % (vid, pid), usb_sharing._ident(d, usb))
            item['name'] = usb_sharing._name(d, vid, pid)
        out.append(item)
    return sorted(out, key=lambda a: (bool(a['usb']), a['hci']))


def pick(found, choice):
    """The adapter controllers use: the chosen USB one (None if it isn't plugged
    in), or else the built-in one (or the first USB one on a Pi without it)."""
    if choice:
        return next((a for a in found if a['address'] == choice['address']), None)
    return found[0] if found else None


def paired_gamepad_count():
    """Quick check for the boot decision, without starting the agent."""
    try:
        import dbus
        bus = dbus.SystemBus()
        om = dbus.Interface(bus.get_object(BLUEZ, '/'), OBJECT_MANAGER)
        objects = om.GetManagedObjects()
        adapter = pick(adapters(objects), load_choice())
        n = 0
        for ifaces in objects.values():
            d = ifaces.get(DEVICE)
            if not d or not adapter or str(d.get('Adapter', '')) != adapter['path']:
                continue
            dev = _device_dict(d)
            if dev['paired'] and dev['bonded'] and is_gamepad(dev):
                n += 1
        return n
    except Exception as e:
        log.warning('Bluetooth check failed: %s', e)
        return 0


def _device_dict(d):
    return {
        'address': str(d.get('Address', '')),
        'name': str(d.get('Alias') or d.get('Name') or d.get('Address', '')),
        'icon': str(d.get('Icon', '')),
        'class': int(d.get('Class', 0)),
        'paired': bool(d.get('Paired', False)),
        # Paired without a stored key: BlueZ refuses its controller connections
        # ("Rejected connection from !bonded device"). Older BlueZ has no Bonded.
        'bonded': bool(d.get('Bonded', d.get('Paired', False))),
        'connected': bool(d.get('Connected', False)),
        'rssi': int(d['RSSI']) if 'RSSI' in d else None,
    }


class Bluetooth:
    def __init__(self):
        import dbus
        import dbus.mainloop.glib
        import dbus.service
        from gi.repository import GLib
        self.dbus = dbus
        if hasattr(dbus.mainloop.glib, 'threads_init'):
            dbus.mainloop.glib.threads_init()
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        self.loop = GLib.MainLoop()
        threading.Thread(target=self.loop.run, daemon=True).start()
        self.discovering = False
        self.lock = threading.Lock()

        class Agent(dbus.service.Object):
            """Accepts pairing without a PIN, which is how game controllers pair."""
            iface = 'org.bluez.Agent1'

            @dbus.service.method(iface, in_signature='', out_signature='')
            def Release(self):
                pass

            @dbus.service.method(iface, in_signature='o', out_signature='s')
            def RequestPinCode(self, device):
                return '0000'

            @dbus.service.method(iface, in_signature='o', out_signature='u')
            def RequestPasskey(self, device):
                return dbus.UInt32(0)

            @dbus.service.method(iface, in_signature='ouq', out_signature='')
            def DisplayPasskey(self, device, passkey, entered):
                pass

            @dbus.service.method(iface, in_signature='os', out_signature='')
            def DisplayPinCode(self, device, pincode):
                pass

            @dbus.service.method(iface, in_signature='ou', out_signature='')
            def RequestConfirmation(self, device, passkey):
                pass

            @dbus.service.method(iface, in_signature='o', out_signature='')
            def RequestAuthorization(self, device):
                pass

            @dbus.service.method(iface, in_signature='os', out_signature='')
            def AuthorizeService(self, device, uuid):
                pass

            @dbus.service.method(iface, in_signature='', out_signature='')
            def Cancel(self):
                pass

        self.agent = Agent(self.bus, AGENT_PATH)
        try:
            mgr = self._iface('/org/bluez', AGENT_MANAGER)
            mgr.RegisterAgent(AGENT_PATH, 'NoInputNoOutput')
            mgr.RequestDefaultAgent(AGENT_PATH)
        except dbus.exceptions.DBusException as e:
            log.warning('Could not register the pairing agent: %s', e)

    # -- helpers ------------------------------------------------------------
    def _iface(self, path, name):
        return self.dbus.Interface(self.bus.get_object(BLUEZ, path), name)

    def _objects(self):
        return self._iface('/', OBJECT_MANAGER).GetManagedObjects()

    def _adapter_path(self, objects=None):
        """The object path of the adapter controllers use, or None."""
        a = pick(adapters(objects or self._objects()), load_choice())
        return a['path'] if a else None

    def _props(self, path):
        return self._iface(path, PROPS)

    def _device(self, path):
        return self._iface(path, DEVICE)

    def _path_for(self, address):
        objects = self._objects()
        adapter = self._adapter_path(objects)
        for path, ifaces in objects.items():
            d = ifaces.get(DEVICE)
            if d and str(d.get('Adapter', '')) == adapter and str(d.get('Address', '')).upper() == address.upper():
                return path
        raise BluetoothError('The controller is no longer visible. Put it in pairing mode again.')

    # -- public -------------------------------------------------------------
    def available(self):
        """Whether BlueZ is running with an adapter (the one controllers use
        may still be unplugged: see state())."""
        try:
            return any(ADAPTER in ifaces for ifaces in self._objects().values())
        except Exception:
            return False

    def state(self):
        """{'adapters': every adapter, 'adapter': the one controllers use (None if
        it isn't plugged in), 'choice': load_choice(), 'devices': devices()}."""
        objects = self._objects()
        found = adapters(objects)
        choice = load_choice()
        adapter = pick(found, choice)
        return {'adapters': found, 'adapter': adapter, 'choice': choice,
                'devices': self._devices(objects, adapter['path'] if adapter else None)}

    def ensure_powered(self):
        path = self._adapter_path()
        if not path:
            if load_choice():
                raise BluetoothError("Your USB Bluetooth adapter isn't plugged in.")
            raise BluetoothError("This Pi's Bluetooth isn't available.")
        props = self._props(path)
        if not props.Get(ADAPTER, 'Powered'):
            try:
                props.Set(ADAPTER, 'Powered', True)
            except self.dbus.exceptions.DBusException:
                raise BluetoothError("Couldn't switch the Bluetooth adapter on. Try again in a moment.")
        return path

    def devices(self):
        """The Bluetooth devices the adapter controllers use knows, as dicts."""
        objects = self._objects()
        return self._devices(objects, self._adapter_path(objects))

    def _devices(self, objects, adapter):
        result = []
        for ifaces in objects.values():
            d = ifaces.get(DEVICE)
            if not d or str(d.get('Adapter', '')) != adapter:
                continue
            dev = _device_dict(d)
            dev['gamepad'] = is_gamepad(dev)
            bat = ifaces.get(BATTERY)
            dev['battery'] = int(bat['Percentage']) if bat and 'Percentage' in bat else None
            if dev['battery'] is None and dev['connected']:
                dev['battery'] = battery_from_sysfs(dev['address'])
            result.append(dev)
        return result

    def _set_pairable(self, on):
        """The adapter must be pairable (bondable) while pairing, or the kernel
        pairs without bonding: no link key is saved, and the controller can't
        reconnect afterwards."""
        try:
            self._props(self._adapter_path()).Set(ADAPTER, 'Pairable', bool(on))
        except Exception as e:
            log.info('set pairable %s: %s', on, e)

    def start_discovery(self):
        with self.lock:
            path = self.ensure_powered()
            self._set_pairable(True)
            adapter = self._iface(path, ADAPTER)
            try:
                adapter.SetDiscoveryFilter({'Transport': 'auto'})
            except self.dbus.exceptions.DBusException:
                pass
            try:
                adapter.StartDiscovery()
            except self.dbus.exceptions.DBusException as e:
                if not e.get_dbus_name().endswith('InProgress'):
                    raise BluetoothError(friendly_error(e))
            self.discovering = True

    def stop_discovery(self, keep_pairable=False):
        with self.lock:
            if self.discovering:
                self.discovering = False
                try:
                    self._iface(self._adapter_path(), ADAPTER).StopDiscovery()
                except Exception as e:
                    log.info('stop discovery: %s', e)
            if not keep_pairable:
                self._set_pairable(False)

    def pairable_gamepads(self):
        """Controllers in pairing mode right now (seen during discovery and not
        properly paired yet)."""
        return [d for d in self.devices() if needs_pairing(d) and d['rssi'] is not None]

    def _rediscover(self, address, seconds=20):
        """Searches until the controller shows up again; returns its object path."""
        path = self._adapter_path()
        adapter = self._iface(path, ADAPTER)
        try:
            adapter.StartDiscovery()
        except self.dbus.exceptions.DBusException:
            pass
        try:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                for p, ifaces in self._objects().items():
                    d = ifaces.get(DEVICE)
                    if d and str(d.get('Adapter', '')) == path and \
                            str(d.get('Address', '')).upper() == address.upper() and 'RSSI' in d:
                        return p
                time.sleep(0.5)
        finally:
            try:
                adapter.StopDiscovery()
            except self.dbus.exceptions.DBusException:
                pass
        raise BluetoothError('The controller left pairing mode. Put it back in pairing mode and try again.')

    def pair(self, address):
        """Pairs, trusts (so it reconnects by itself) and connects a controller."""
        dbus = self.dbus
        was_discovering = self.discovering
        # Stop searching (pairing is more reliable without it), but stay pairable.
        self.stop_discovery(keep_pairable=True)
        self._set_pairable(True)
        try:
            path = self._path_for(address)
            props = self._props(path)
            if props.Get(DEVICE, 'Paired') and not self._is_bonded(props):
                # An earlier pairing without a saved key: remove it and pair from scratch.
                self._remove(path)
                path = self._rediscover(address)
                props = self._props(path)
            dev = self._device(path)
            try:
                dev.Pair(timeout=60)
            except dbus.exceptions.DBusException as e:
                if not e.get_dbus_name().endswith('AlreadyExists'):
                    try:
                        if not props.Get(DEVICE, 'Paired'):
                            self._remove(path)
                    except dbus.exceptions.DBusException:
                        pass
                    if was_discovering:
                        self.start_discovery()
                    raise BluetoothError(friendly_error(e))
            if not self._is_bonded(props):
                self._remove(path)
                raise BluetoothError("It paired, but the Pi couldn't save the pairing, so it wouldn't reconnect "
                                     "later. Put it back in pairing mode and try again.")
            props.Set(DEVICE, 'Trusted', True)
            try:
                dev.Connect(timeout=30)
            except dbus.exceptions.DBusException as e:
                if not props.Get(DEVICE, 'Connected'):
                    raise BluetoothError('Paired, but it didn\'t connect: ' + friendly_error(e))
        finally:
            if not self.discovering:
                self._set_pairable(False)

    def _is_bonded(self, props):
        try:
            return bool(props.Get(DEVICE, 'Bonded'))
        except self.dbus.exceptions.DBusException:
            return True     # BlueZ older than 5.73 has no Bonded property

    def connect(self, address):
        path = self._path_for(address)
        try:
            self._device(path).Connect(timeout=30)
        except self.dbus.exceptions.DBusException as e:
            raise BluetoothError(friendly_error(e))

    def disconnect(self, address):
        path = self._path_for(address)
        try:
            self._device(path).Disconnect(timeout=15)
        except self.dbus.exceptions.DBusException as e:
            raise BluetoothError(friendly_error(e))

    def forget(self, address):
        self._remove(self._path_for(address))

    def _remove(self, path):
        try:
            self._iface(self._adapter_path(), ADAPTER).RemoveDevice(path)
        except self.dbus.exceptions.DBusException as e:
            raise BluetoothError(friendly_error(e))

    def close(self):
        self.stop_discovery()
        try:
            self._iface('/org/bluez', AGENT_MANAGER).UnregisterAgent(AGENT_PATH)
        except Exception:
            pass
        self.loop.quit()
