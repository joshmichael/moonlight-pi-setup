#!/usr/bin/env python3
"""USB sharing: sends USB devices plugged into the Pi to the gaming PC while
Moonlight is streaming, with USB/IP (Linux's usbip-host driver and usbipd).

The menu imports this to list the devices and ports, and to explain what
happens to each device. Run as root by moonlight-usb-sharing.service
(`usb_sharing.py serve`), it watches Moonlight's log, shares the devices when a
stream starts and gives them back to the Pi when it ends.

A device only leaves the Pi when the PC being streamed to is ready for it: the
helper that the PC setup installs asks the Pi for its devices every 2 seconds,
and the firewall remembers which addresses asked in the last 30 seconds. If the
PC isn't ready, the devices stay on the Pi and work through Moonlight as usual.

Installed by moonlight-pi-setup.
"""
import glob
import ipaddress
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time

SYS_USB = '/sys/bus/usb/devices'
UDEV_DATA = '/run/udev/data'
STATE_DIR = '/var/lib/moonlight-pi-setup'
CONFIG = STATE_DIR + '/usb-sharing.conf'       # written by the settings helper
SEEN = STATE_DIR + '/usb-seen.json'            # devices the Pi has seen, for the menu
STATUS = '/run/moonlight-usb-sharing/status.json'
MOONLIGHT_LOG = '/tmp/moonlight.log'
MODEL_FILE = '/proc/device-tree/model'
USB_IDS = ('/usr/share/misc/usb.ids', '/usr/share/hwdata/usb.ids', '/var/lib/usbutils/usb.ids')

USBIP_PORT = 3240
READY_SECONDS = 30          # a PC is ready if its helper asked within this long
NFT_TABLE = 'moonlight_usb'
TAILSCALE_NETS = (ipaddress.ip_network('100.64.0.0/10'), ipaddress.ip_network('fd7a:115c:a1e0::/48'))
SEEN_MAX = 40

# What a device is, and whether it goes to the PC unless set otherwise.
KINDS = {
    'controller': ('Controller', "it's a controller", True),
    'audio': ('Headset or audio', "it's a headset or audio device", True),
    'keyboard': ('Keyboard', "it's a keyboard", False),
    'mouse': ('Mouse', "it's a mouse", False),
    'storage': ('Storage', "it's a storage device", False),
    'bluetooth': ('Bluetooth adapter', "it's a Bluetooth adapter", False),
    'camera': ('Camera', "it's a camera", False),
    'other': ('Other', "it isn't a controller or a headset", False),
}

PORT_KEY_RE = re.compile(r'^[0-9]{1,3}-[0-9]{1,3}(\.[0-9]{1,3}){0,6}$')
MODEL_RE = re.compile(r'^[0-9a-f]{4}:[0-9a-f]{4}$')


def _read(path, default=''):
    try:
        with open(path, errors='replace') as f:
            return f.read().strip()
    except OSError:
        return default


def board_model():
    return _read(MODEL_FILE).strip('\0 ')


# ---------------------------------------------------------------------------
# Settings (changed only by the settings helper, as root)
# ---------------------------------------------------------------------------
def load_config(path=CONFIG):
    """{'tailscale': bool, 'devices': {model: 'always'|'never'}, 'ports': set of port keys}."""
    cfg = {'tailscale': False, 'devices': {}, 'ports': set()}
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except OSError:
        return cfg
    for line in lines:
        key, sep, value = line.partition('=')
        if not sep:
            continue
        if key == 'tailscale':
            cfg['tailscale'] = value == '1'
        elif key.startswith('device.') and MODEL_RE.match(key[7:]) and value in ('always', 'never'):
            cfg['devices'][key[7:]] = value
        elif key.startswith('port.') and PORT_KEY_RE.match(key[5:]) and value == 'on':
            cfg['ports'].add(key[5:])
    return cfg


def load_seen(path=SEEN):
    try:
        with open(path) as f:
            seen = json.load(f)
        return seen if isinstance(seen, dict) else {}
    except (OSError, ValueError):
        return {}


def load_status(path=STATUS):
    """What the sharing service last reported, or {} if it isn't running."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------------
# Ports. A port is named by the Linux address of the device plugged into it
# (bus-port.port...), so "1-1.3" is port 3 of a hub in port 1 of bus 1. A USB 3
# socket has two addresses, one for USB 2 devices and one for USB 3 devices
# (Linux links them as "peers"): the lower bus number names the socket.
# ---------------------------------------------------------------------------
def _split(key):
    bus, _, path = key.partition('-')
    return int(bus), [int(p) for p in path.split('.')]


def _port_dir(key):
    """The sysfs directory of the hub port with this address."""
    bus, parts = _split(key)
    if len(parts) == 1:
        return '%s/usb%d/%d-0:1.0/usb%d-port%d' % (SYS_USB, bus, bus, bus, parts[0])
    hub = '%d-%s' % (bus, '.'.join(str(p) for p in parts[:-1]))
    return '%s/%s/%s:1.0/%s-port%d' % (SYS_USB, hub, hub, hub, parts[-1])


def _key_from_dir(d):
    name = os.path.basename(d)
    m = re.match(r'usb(\d+)-port(\d+)$', name)
    if m:
        return '%s-%s' % m.groups()
    m = re.match(r'(\d+-[\d.]+)-port(\d+)$', name)
    if m:
        return '%s.%s' % m.groups()
    return None


def port_key(address):
    """The name of the socket at this address (the same for its USB 2 and USB 3 halves)."""
    peer = os.path.join(_port_dir(address), 'peer')
    other = _key_from_dir(os.path.realpath(peer)) if os.path.exists(peer) else None
    keys = [address] + ([other] if other else [])
    return min(keys, key=_split)


def root_ports():
    """The board's own USB sockets, in a fixed order."""
    keys = set()
    for hub in glob.glob(SYS_USB + '/usb*'):
        bus = os.path.basename(hub)[3:]
        for d in glob.glob('%s/%s-0:1.0/usb%s-port*' % (hub, bus, bus)):
            if _read(d + '/connect_type') == 'hardwired':
                continue        # wired inside the board, not a socket
            key = _key_from_dir(d)
            if key:
                keys.add(port_key(key))
    return sorted(keys, key=_split)


def port_label(key, roots=None):
    """'Port 2', or 'Port 2 › hub port 3' for a port on a hub."""
    roots = root_ports() if roots is None else roots
    bus, parts = _split(key)
    root = port_key('%d-%d' % (bus, parts[0]))
    name = 'Port %d' % (roots.index(root) + 1) if root in roots else 'Port %s' % root
    return name + ''.join(' › hub port %d' % p for p in parts[1:])


def port_rule_for(key, ports):
    """The port rule that covers a device at this port: its own port, or the
    port of a hub it's plugged into."""
    for p in sorted(ports, key=len, reverse=True):
        if key == p or key.startswith(p + '.'):
            return p
    return None


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------
_usb_ids = None


def _usb_ids_names(vid, pid):
    """(vendor, product) names from the usb.ids database, if it's installed."""
    global _usb_ids
    if _usb_ids is None:
        _usb_ids = {}
        for path in USB_IDS:
            try:
                with open(path, encoding='utf-8', errors='replace') as f:
                    vendor = None
                    for line in f:
                        if line.startswith('#') or not line.strip():
                            continue
                        if line.startswith('C '):
                            break       # device classes follow the vendors
                        if not line.startswith('\t'):
                            vendor = line[:4].lower()
                            _usb_ids[vendor] = line[4:].strip()
                        elif vendor and not line.startswith('\t\t'):
                            _usb_ids['%s:%s' % (vendor, line[1:5].lower())] = line[5:].strip()
                break
            except OSError:
                continue
    return _usb_ids.get(vid, ''), _usb_ids.get('%s:%s' % (vid, pid), '')


def _name(path, vid, pid):
    maker, product = _read(path + '/manufacturer'), _read(path + '/product')
    if not product:
        maker2, product = _usb_ids_names(vid, pid)
        maker = maker or maker2
    if not product:
        return 'USB device %s:%s' % (vid, pid)
    brand = maker.split()[0] if maker else ''
    if brand and brand.lower() not in product.lower() and brand.lower() not in ('usb', 'generic'):
        return '%s %s' % (brand, product)
    return product


def _input_props(path, address):
    """The udev properties (ID_INPUT_*) of the input devices a USB device has."""
    props = set()
    events = (glob.glob('%s/%s:*/input/input*/event*' % (path, address)) +
              glob.glob('%s/%s:*/*/input/input*/event*' % (path, address)))
    for ev in events:
        dev = _read(ev + '/dev')
        data = _read('%s/c%s' % (UDEV_DATA, dev)) if dev else ''
        if data:
            props.update(line[2:].split('=')[0] for line in data.splitlines()
                         if line.startswith('E:ID_INPUT_') and line.endswith('=1'))
        else:
            props.update(_caps_props(os.path.dirname(ev)))
    return props


def _bits(text):
    value = 0
    for word in text.split():
        value = (value << 64) | int(word, 16)
    return value


def _caps_props(input_dir):
    """Like udev's ID_INPUT_* properties, from an input device's capabilities
    (if udev's database can't be read)."""
    try:
        keys = _bits(_read(input_dir + '/capabilities/key', '0'))
        rel = _bits(_read(input_dir + '/capabilities/rel', '0'))
    except ValueError:
        return set()
    props = set()
    if any((keys >> b) & 1 for b in range(0x120, 0x140)):           # joystick and gamepad buttons
        props.add('ID_INPUT_JOYSTICK')
    if all((keys >> b) & 1 for b in (16, 30, 44)):                   # Q, A, Z
        props.add('ID_INPUT_KEYBOARD')
    if (rel & 0x3) == 0x3 and (keys >> 0x110) & 1:                  # REL_X, REL_Y and BTN_LEFT
        props.add('ID_INPUT_MOUSE')
    return props


def _kind(path, address):
    classes = set()
    for d in glob.glob('%s/%s:*' % (path, address)):
        classes.add((_read(d + '/bInterfaceClass'), _read(d + '/bInterfaceSubClass'),
                     _read(d + '/bInterfaceProtocol')))
    props = _input_props(path, address)
    names = {c[0] for c in classes}
    if 'ID_INPUT_JOYSTICK' in props:
        return 'controller'
    if 'ID_INPUT_KEYBOARD' in props:
        return 'keyboard'
    if 'ID_INPUT_MOUSE' in props:
        return 'mouse'
    if '0e' in names:
        return 'camera'
    if '01' in names:
        return 'audio'
    if '08' in names:
        return 'storage'
    if ('e0', '01', '01') in classes:
        return 'bluetooth'
    return 'other'


def _driver(path):
    return os.path.basename(os.path.realpath(path + '/driver')) if os.path.exists(path + '/driver') else ''


def devices(seen=None):
    """The USB devices plugged in now (hubs too, with kind 'hub'), as dicts:
    address, model (vendor:product), name, kind, port, hub, shared, builtin."""
    seen = load_seen() if seen is None else seen
    model_name = board_model()
    out = []
    for entry in sorted(os.listdir(SYS_USB) if os.path.isdir(SYS_USB) else []):
        if ':' in entry or entry.startswith('usb') or not PORT_KEY_RE.match(entry):
            continue
        path = os.path.join(SYS_USB, entry)
        vid, pid = _read(path + '/idVendor').lower(), _read(path + '/idProduct').lower()
        if not vid:
            continue
        model = '%s:%s' % (vid, pid)
        hub = _read(path + '/bDeviceClass') == '09'
        shared = _driver(path) == 'usbip-host'
        if hub:
            kind = 'hub'
        elif shared:
            # Shared, its interfaces aren't on the Pi any more: use what it was.
            kind = (seen.get(model) or {}).get('kind') or 'other'
        else:
            kind = _kind(path, entry)
        key = port_key(entry)
        builtin = (_read(_port_dir(entry) + '/connect_type') == 'hardwired' or
                   # The Pi 500's own keyboard is a USB device inside it.
                   ('Raspberry Pi 500' in model_name and vid == '2e8a' and kind == 'keyboard'))
        out.append({'address': entry, 'model': model, 'name': _name(path, vid, pid), 'kind': kind,
                    'port': key, 'hub': hub, 'shared': shared, 'builtin': builtin})
    return out


def hub_ports(address):
    """The names of a hub's own ports."""
    keys = set()
    for d in glob.glob('%s/%s/%s:1.0/%s-port*' % (SYS_USB, address, address, address)):
        key = _key_from_dir(d)
        if key:
            keys.add(port_key(key))
    return keys


def decide(dev, cfg):
    """(goes to the PC while streaming, why), for a device that isn't a hub."""
    rule = cfg['devices'].get(dev['model'])
    if rule == 'always':
        return True, 'you set it to Always'
    if rule == 'never':
        return False, 'you set it to Never'
    port = port_rule_for(dev['port'], cfg['ports'])
    if port:
        return True, "it's in a PC port" if port == dev['port'] else "it's on a hub in a PC port"
    _, why, goes = KINDS.get(dev['kind'], KINDS['other'])
    return goes, why


def kind_label(kind):
    if kind == 'hub':
        return 'USB hub'
    return KINDS.get(kind, KINDS['other'])[0]


# ---------------------------------------------------------------------------
# For the menu
# ---------------------------------------------------------------------------
def snapshot():
    """Everything the menu shows: {'devices', 'remembered', 'ports', 'config',
    'status'}. Devices plugged in now have 'goes', 'why' and 'label' (their
    port's name); built-in devices are left out."""
    cfg = load_config()
    seen = load_seen()
    roots = root_ports()
    devs = []
    for d in devices(seen):
        if d['builtin']:
            continue
        d['label'] = port_label(d['port'], roots)
        if not d['hub']:
            d['goes'], d['why'] = decide(d, cfg)
        devs.append(d)
    plugged = {d['model'] for d in devs}
    remembered = []
    for model, info in sorted(seen.items(), key=lambda kv: -kv[1].get('last', 0)):
        if model not in plugged and isinstance(info, dict):
            rule = cfg['devices'].get(model)
            remembered.append({'model': model, 'name': info.get('name') or model,
                               'kind': info.get('kind') or 'other', 'rule': rule})
    # Every socket, every port of the hubs plugged in, and any other port with a rule.
    keys = set(roots) | set(cfg['ports'])
    for d in devs:
        keys.add(d['port'])
        if d['hub']:
            keys |= hub_ports(d['address'])
    ports = {k: {'key': k, 'label': port_label(k, roots), 'devices': [], 'rule': k in cfg['ports']}
             for k in keys}
    for d in devs:
        ports[d['port']]['devices'].append(d)
    return {'devices': devs, 'remembered': remembered,
            'ports': sorted(ports.values(), key=lambda p: _split(p['key'])),
            'config': cfg, 'status': load_status()}


def is_tailscale(addr):
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in TAILSCALE_NETS)


# ---------------------------------------------------------------------------
# The sharing service (root)
# ---------------------------------------------------------------------------
LAUNCH_RE = re.compile(r'Executing request: "https?://(\[[0-9A-Fa-f:.]+\]|[^/:"\s]+):\d+/(?:launch|resume)\?')
RTSP_RE = re.compile(r'rtsp(?:enc)?://(\[[0-9A-Fa-f:.]+\]|[^/:"\s]+):\d+')
START = 'Starting video stream'
STOP = 'Stopping video stream'


def log(msg):
    print(msg, flush=True)


def run(args, timeout=15):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


def moonlight_running():
    return run(['pgrep', '-x', 'moonlight-qt'], 5)[0] == 0


def resolve(host):
    host = host.strip('[]')
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        pass
    try:
        return socket.getaddrinfo(host, None)[0][4][0]
    except (OSError, IndexError):
        return None


class LogWatcher:
    """Follows Moonlight's log, which is rewritten each time Moonlight starts."""

    def __init__(self, path=MOONLIGHT_LOG):
        self.path = path
        self.inode = None
        self.pos = 0
        self.partial = ''

    def initial(self):
        """(streaming, PC address) from the log so far, in case this service
        restarted during a stream."""
        streaming, pc, pending = False, None, None
        try:
            st = os.stat(self.path)
            with open(self.path, 'rb') as f:
                text = f.read(st.st_size).decode('utf-8', 'replace')
        except OSError:
            return False, None
        self.inode, self.pos = st.st_ino, st.st_size
        for line in text.splitlines():
            pending = self.address(line) or pending
            if START in line:
                streaming, pc = True, pending
            elif STOP in line:
                streaming = False
        return streaming and moonlight_running(), pc

    @staticmethod
    def address(line):
        m = LAUNCH_RE.search(line) or RTSP_RE.search(line)
        return resolve(m.group(1)) if m else None

    def read(self):
        """(new lines, whether Moonlight started again)."""
        try:
            st = os.stat(self.path)
        except OSError:
            return [], False
        restarted = False
        if self.inode is None:
            self.inode, self.pos = st.st_ino, st.st_size
        elif st.st_ino != self.inode or st.st_size < self.pos:
            self.inode, self.pos, self.partial, restarted = st.st_ino, 0, '', True
        if st.st_size <= self.pos:
            return [], restarted
        try:
            with open(self.path, 'rb') as f:
                f.seek(self.pos)
                data = f.read(st.st_size - self.pos)
        except OSError:
            return [], restarted
        self.pos += len(data)
        text = self.partial + data.decode('utf-8', 'replace')
        lines = text.split('\n')
        self.partial = lines.pop()
        return lines, restarted


def nft(script):
    try:
        p = subprocess.run(['nft', '-f', '-'], input=script, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return p.returncode == 0, p.stderr.strip()


def firewall_create():
    """The table, with the sets of addresses that asked for devices recently."""
    return nft('''table inet %(t)s
delete table inet %(t)s
table inet %(t)s {
  set ready4 { type ipv4_addr; flags dynamic, timeout; timeout %(s)ds; }
  set ready6 { type ipv6_addr; flags dynamic, timeout; timeout %(s)ds; }
  chain input {
    type filter hook input priority filter; policy accept;
    tcp dport %(p)d jump usbip
  }
  chain usbip {
  }
}
''' % {'t': NFT_TABLE, 's': READY_SECONDS, 'p': USBIP_PORT})


def firewall_rules(tailscale, pc):
    """Who may ask for devices: never over Tailscale unless it's allowed, and
    during a stream only the PC being streamed to."""
    rules = []
    if not tailscale:
        rules.append('iifname "tailscale0" drop')
    rules += ['ct state new update @ready4 { ip saddr }', 'ct state new update @ready6 { ip6 saddr }']
    if pc:
        if ':' in pc:
            rules += ['meta nfproto ipv4 drop', 'ip6 saddr != %s drop' % pc]
        else:
            rules += ['meta nfproto ipv6 drop', 'ip saddr != %s drop' % pc]
    body = ''.join('    %s\n' % r for r in rules)
    return nft('flush chain inet %s usbip\ntable inet %s {\n  chain usbip {\n%s  }\n}\n' % (NFT_TABLE, NFT_TABLE, body))


def ready_addresses():
    """The addresses whose helpers asked for devices in the last 30 seconds."""
    found = set()
    for name in ('ready4', 'ready6'):
        rc, out = run(['nft', '-j', 'list', 'set', 'inet', NFT_TABLE, name], 10)
        if rc != 0:
            continue
        try:
            items = json.loads(out).get('nftables', [])
        except ValueError:
            continue
        for item in items:
            for elem in (item.get('set') or {}).get('elem', []):
                val = elem.get('elem', {}).get('val') if isinstance(elem, dict) else elem
                if isinstance(val, str):
                    found.add(val)
    return found


def shared_addresses():
    return {d for d in os.listdir('/sys/bus/usb/drivers/usbip-host') if PORT_KEY_RE.match(d)} \
        if os.path.isdir('/sys/bus/usb/drivers/usbip-host') else set()


def write_json(path, data):
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w') as f:
            json.dump(data, f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except OSError as e:
        log('Could not write %s: %s' % (path, e))


class Service:
    def __init__(self):
        self.stop = False
        self.usbipd = None
        self.usbipd_started = 0
        self.watcher = LogWatcher()
        self.streaming, self.pc = False, None
        self.pending = None
        self.rules = None
        self.seen = load_seen()
        self.seen_changed = False
        self.last = load_status().get('last')
        self.status = None

    # -- usbipd -------------------------------------------------------------
    def keep_usbipd(self):
        if self.usbipd and self.usbipd.poll() is None:
            return
        if self.usbipd:
            log('usbipd stopped (exit code %s)' % self.usbipd.returncode)
            self.usbipd = None
        if time.monotonic() - self.usbipd_started < 10:
            return
        self.usbipd_started = time.monotonic()
        try:
            self.usbipd = subprocess.Popen(['usbipd'])
            log('Started usbipd on port %d' % USBIP_PORT)
        except OSError as e:
            log("Couldn't start usbipd: %s" % e)

    # -- stream state -------------------------------------------------------
    def follow_log(self):
        lines, restarted = self.watcher.read()
        if restarted and self.streaming:
            self.end_stream('Moonlight started again')
        for line in lines:
            addr = self.watcher.address(line)
            if addr:
                self.pending = addr
            if START in line and not self.streaming:
                self.streaming, self.pc = True, self.pending
                log('Stream started (PC %s)' % (self.pc or 'unknown'))
            elif STOP in line and self.streaming:
                self.end_stream('stream closed')
        if self.streaming and not moonlight_running():
            self.end_stream('Moonlight closed')

    def end_stream(self, why):
        log('Stream ended (%s): giving USB devices back to the Pi' % why)
        self.streaming, self.pc, self.pending = False, None, None

    # -- sharing ------------------------------------------------------------
    def remember(self, dev):
        info = self.seen.get(dev['model'])
        if not info or info.get('name') != dev['name'] or info.get('kind') != dev['kind'] or \
                time.time() - info.get('last', 0) > 3600:
            self.seen[dev['model']] = {'name': dev['name'], 'kind': dev['kind'], 'last': int(time.time())}
            self.seen_changed = True

    def save_seen(self):
        if not self.seen_changed:
            return
        newest = sorted(self.seen.items(), key=lambda kv: -kv[1].get('last', 0))[:SEEN_MAX]
        self.seen = dict(newest)
        write_json(SEEN, self.seen)
        self.seen_changed = False

    def step(self):
        self.keep_usbipd()
        self.follow_log()
        cfg = load_config()
        pc = self.pc if self.streaming else None
        allowed = bool(pc) and (cfg['tailscale'] or not is_tailscale(pc))
        ready = ready_addresses()
        # Check the PC is ready before narrowing the firewall to it.
        pc_ready = allowed and pc in ready
        rules = (cfg['tailscale'], pc if allowed else None)
        if rules != self.rules:
            ok, err = firewall_rules(*rules)
            if ok:
                self.rules = rules
            else:
                log("Couldn't update the firewall: %s" % err)
        want, names = set(), []
        devs = devices(self.seen)
        for d in devs:
            if d['hub'] or d['builtin']:
                continue
            if not d['shared']:
                self.remember(d)
            if pc_ready and decide(d, cfg)[0]:
                want.add(d['address'])
                names.append(d['name'])
        shared = shared_addresses()
        for a in sorted(shared - want):
            rc, out = run(['usbip', 'unbind', '-b', a])
            log('Gave %s back to the Pi%s' % (a, '' if rc == 0 else ': ' + out))
        for a in sorted(want - shared):
            rc, out = run(['usbip', 'bind', '-b', a])
            log('Shared %s with %s%s' % (a, pc, '' if rc == 0 else ': ' + out))
        if self.streaming:
            # What happened in this stream, for the menu to show afterwards.
            last = {'pc': pc, 'ready': pc_ready, 'devices': sorted(set(names)),
                    'tailscale_blocked': bool(pc) and is_tailscale(pc) and not cfg['tailscale']}
            if {k: v for k, v in (self.last or {}).items() if k != 'time'} != last:
                self.last = dict(last, time=int(time.time()))
        self.save_seen()
        status = {'streaming': self.streaming, 'pc': pc, 'pc_ready': pc_ready,
                  'ready': sorted(ready), 'shared': sorted(shared_addresses()), 'last': self.last}
        if status != self.status:
            self.status = status
            write_json(STATUS, dict(status, updated=int(time.time())))

    def run(self):
        signal.signal(signal.SIGTERM, lambda *a: setattr(self, 'stop', True))
        signal.signal(signal.SIGINT, lambda *a: setattr(self, 'stop', True))
        run(['modprobe', 'usbip-host'])
        ok, err = firewall_create()
        if not ok:
            log("Couldn't set up the firewall, so nothing will be shared: %s" % err)
            return 1
        self.streaming, self.pc = self.watcher.initial()
        log('Watching Moonlight%s' % (' (a stream is running)' if self.streaming else ''))
        try:
            while not self.stop:
                try:
                    self.step()
                except Exception as e:      # keep going: the next step may work
                    log('Error: %s' % e)
                time.sleep(1)
        finally:
            for a in sorted(shared_addresses()):
                run(['usbip', 'unbind', '-b', a])
            if self.usbipd and self.usbipd.poll() is None:
                self.usbipd.terminate()
                try:
                    self.usbipd.wait(5)
                except subprocess.TimeoutExpired:
                    self.usbipd.kill()
            nft('delete table inet %s\n' % NFT_TABLE)
            try:
                os.remove(STATUS)
            except OSError:
                pass
            log('Stopped: USB devices are back on the Pi')
        return 0


if __name__ == '__main__':
    if sys.argv[1:] == ['serve']:
        sys.exit(Service().run())
    if sys.argv[1:] == ['list']:
        for dev in snapshot()['devices']:
            goes = '' if dev['hub'] else (' -> PC' if dev['goes'] else ' (stays)')
            print('%-10s %-9s %-14s %s [%s]%s' % (dev['address'], dev['model'], kind_label(dev['kind']),
                                                   dev['name'], dev['label'], goes))
        sys.exit(0)
    print('Use: usb_sharing.py serve|list', file=sys.stderr)
    sys.exit(2)
