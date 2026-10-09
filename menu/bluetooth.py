"""Bluetooth controller pairing through BlueZ (D-Bus)."""
import glob
import logging
import os
import threading

log = logging.getLogger(__name__)

BLUEZ = 'org.bluez'
ADAPTER = 'org.bluez.Adapter1'
DEVICE = 'org.bluez.Device1'
BATTERY = 'org.bluez.Battery1'
PROPS = 'org.freedesktop.DBus.Properties'
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


def paired_gamepad_count():
    """Quick check for the boot decision, without starting the agent."""
    try:
        import dbus
        bus = dbus.SystemBus()
        om = dbus.Interface(bus.get_object(BLUEZ, '/'), 'org.freedesktop.DBus.ObjectManager')
        n = 0
        for _, ifaces in om.GetManagedObjects().items():
            d = ifaces.get(DEVICE)
            if not d:
                continue
            dev = _device_dict(d)
            if dev['paired'] and dev['bonded'] and is_gamepad(dev):
                n += 1
        return n
    except Exception as e:
        log.warning('Bluetooth check failed: %s', e)
        return 0


def _device_dict(d, path=None):
    return {
        'path': str(path) if path else None,
        'address': str(d.get('Address', '')),
        'name': str(d.get('Alias') or d.get('Name') or d.get('Address', '')),
        'icon': str(d.get('Icon', '')),
        'class': int(d.get('Class', 0)),
        'paired': bool(d.get('Paired', False)),
        # Paired without a stored key: BlueZ refuses its controller connections
        # ("Rejected connection from !bonded device"). Older BlueZ has no Bonded.
        'bonded': bool(d.get('Bonded', d.get('Paired', False))),
        'trusted': bool(d.get('Trusted', False)),
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
            mgr = dbus.Interface(self.bus.get_object(BLUEZ, '/org/bluez'), 'org.bluez.AgentManager1')
            mgr.RegisterAgent(AGENT_PATH, 'NoInputNoOutput')
            mgr.RequestDefaultAgent(AGENT_PATH)
        except dbus.exceptions.DBusException as e:
            log.warning('Could not register the pairing agent: %s', e)

    # -- helpers ------------------------------------------------------------
    def _objects(self):
        om = self.dbus.Interface(self.bus.get_object(BLUEZ, '/'), 'org.freedesktop.DBus.ObjectManager')
        return om.GetManagedObjects()

    def _adapter_path(self):
        for path, ifaces in self._objects().items():
            if ADAPTER in ifaces:
                return path
        return None

    def _props(self, path):
        return self.dbus.Interface(self.bus.get_object(BLUEZ, path), PROPS)

    def _device(self, path):
        return self.dbus.Interface(self.bus.get_object(BLUEZ, path), DEVICE)

    def _path_for(self, address):
        for path, ifaces in self._objects().items():
            d = ifaces.get(DEVICE)
            if d and str(d.get('Address', '')).upper() == address.upper():
                return path
        raise BluetoothError('The controller is no longer visible. Put it in pairing mode again.')

    # -- public -------------------------------------------------------------
    def available(self):
        try:
            return self._adapter_path() is not None
        except Exception:
            return False

    def ensure_powered(self):
        path = self._adapter_path()
        if not path:
            raise BluetoothError("This Pi's Bluetooth isn't available.")
        props = self._props(path)
        if not props.Get(ADAPTER, 'Powered'):
            props.Set(ADAPTER, 'Powered', True)
        return path

    def devices(self):
        """All known Bluetooth devices, as dicts."""
        result = []
        for path, ifaces in self._objects().items():
            d = ifaces.get(DEVICE)
            if not d:
                continue
            dev = _device_dict(d, path)
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
            adapter = self.dbus.Interface(self.bus.get_object(BLUEZ, path), ADAPTER)
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
                    path = self._adapter_path()
                    adapter = self.dbus.Interface(self.bus.get_object(BLUEZ, path), ADAPTER)
                    adapter.StopDiscovery()
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
        import time
        path = self._adapter_path()
        adapter = self.dbus.Interface(self.bus.get_object(BLUEZ, path), ADAPTER)
        try:
            adapter.StartDiscovery()
        except self.dbus.exceptions.DBusException:
            pass
        try:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                for p, ifaces in self._objects().items():
                    d = ifaces.get(DEVICE)
                    if d and str(d.get('Address', '')).upper() == address.upper() and 'RSSI' in d:
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
        adapter_path = self._adapter_path()
        adapter = self.dbus.Interface(self.bus.get_object(BLUEZ, adapter_path), ADAPTER)
        try:
            adapter.RemoveDevice(path)
        except self.dbus.exceptions.DBusException as e:
            raise BluetoothError(friendly_error(e))

    def close(self):
        self.stop_discovery()
        try:
            mgr = self.dbus.Interface(self.bus.get_object(BLUEZ, '/org/bluez'), 'org.bluez.AgentManager1')
            mgr.UnregisterAgent(AGENT_PATH)
        except Exception:
            pass
        self.loop.quit()
