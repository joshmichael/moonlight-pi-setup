#!/usr/bin/env python3
"""Xbox controller on sign-in and UAC screens: while Windows shows its lock
screen, its sign-in screen or a UAC prompt during a stream, every controller
that isn't an Xbox controller also shows up on the PC as an Xbox controller
that copies it (an "Xbox copy").

Windows' own controller support on those screens (entering a PIN, choosing
Yes on a UAC prompt) only works with Xbox controllers. A DualSense, a
DualShock 4 or a Switch Pro controller can't use them, whether it's on the PC
through USB sharing or goes through Moonlight. The original controller is
never changed or disconnected, so a game keeps its own features (like a
DualSense's haptics); the copy goes away when the screen does.

How it works:
  - A small service on the PC (installed by the PC setup) tells the Pi whether
    Windows is showing one of those screens: a few bytes over UDP (port 3241),
    every second while it is, and every 5 seconds otherwise.
  - While the PC being streamed to shows one, this service creates a virtual
    wired Xbox 360 controller (uinput) for each controller that isn't an Xbox
    one. Moonlight sees it, and the PC gets an Xbox controller.
  - A copy reads its controller from the controller's Linux input device when
    the controller is on the Pi (Bluetooth, or USB kept on the Pi), and from its
    USB reports (usbmon, see usb_combo.py) when USB sharing has sent it to the
    PC. A controller that moves to the PC while a screen is up keeps its copy.
  - Nothing is read while no screen is up, so it costs nothing during games.

Run as root by moonlight-xbox-copy.service (`xbox_copy.py serve`). Also:
  xbox_copy.py status          what the service last reported
  xbox_copy.py controllers     the controllers connected now, and whether each gets a copy
  xbox_copy.py capture [--raw] [--seconds N]
                               shows what each controller's copy would read (root, for USB sharing)
  xbox_copy.py serve --force-secure
                               acts as if the PC were showing a sign-in screen (for testing)

Installed by moonlight-pi-setup.
"""
import fcntl
import glob
import json
import os
import re
import select
import signal
import socket
import struct
import sys
import time

import usb_combo
import usb_sharing
from usb_combo import SOUTH, EAST, WEST, NORTH, L1, R1, SELECT, START, GUIDE, L3, R3, NEUTRAL

PORT = 3241                     # UDP, from the PC's service
SECURE_SECONDS = 3.0            # a screen counts as up this long after the PC last said so
MAX_COPIES = 4
STEP_SECONDS = 1.0
MISSING_SECONDS = 3.0           # a copy stays this long after its controller disappears
STATUS = '/run/moonlight-xbox-copy/status.json'
SYS_INPUT = '/sys/class/input'
USBIP_HOST = '/sys/bus/usb/drivers/usbip-host'
USB_SHARING_SERVICE = '/etc/systemd/system/moonlight-usb-sharing.service'
UINPUT = '/dev/uinput'
COPY_PHYS = 'moonlight-xbox-copy'
MAGIC = 'moonlight-pi-setup'

# Linux input codes (linux/input-event-codes.h).
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
SYN_REPORT, SYN_DROPPED = 0, 3
BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 0x130, 0x131, 0x133, 0x134      # BTN_A, BTN_B, BTN_X, BTN_Y
BTN_TL, BTN_TR, BTN_TL2, BTN_TR2 = 0x136, 0x137, 0x138, 0x139
BTN_SELECT, BTN_START, BTN_MODE, BTN_THUMBL, BTN_THUMBR = 0x13a, 0x13b, 0x13c, 0x13d, 0x13e
BTN_DPAD_UP, BTN_DPAD_DOWN, BTN_DPAD_LEFT, BTN_DPAD_RIGHT = 0x220, 0x221, 0x222, 0x223
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_HAT0X, ABS_HAT0Y = 0, 1, 2, 3, 4, 5, 0x10, 0x11
INPUT_PROP_ACCELEROMETER = 6
BUS_USB, BUS_BLUETOOTH, BUS_VIRTUAL = 0x03, 0x05, 0x06
MICROSOFT = 0x045e
XBOX_DRIVERS = ('xpad', 'xpadneo', 'microsoft')

EVENT = struct.Struct('llHHi')          # struct input_event (64-bit)
ABSINFO = struct.Struct('6i')           # value, minimum, maximum, fuzz, flat, resolution
KEY_BYTES = 96                          # (KEY_MAX + 1) / 8
EVIOCGKEY = 0x80000000 | KEY_BYTES << 16 | 0x4518


def EVIOCGABS(axis):
    return 0x80184540 + axis


# A controller's buttons, as Linux reports them (by position, as its gamepad API
# says), and as the Xbox copy reports them (as Linux's Xbox driver, xpad, does:
# by label, so its X, on the left, is BTN_X, which Linux also calls BTN_NORTH).
SOURCE_BUTTONS = {BTN_SOUTH: SOUTH, BTN_EAST: EAST, BTN_WEST: WEST, BTN_NORTH: NORTH, BTN_TL: L1,
                  BTN_TR: R1, BTN_SELECT: SELECT, BTN_START: START, BTN_MODE: GUIDE, BTN_THUMBL: L3,
                  BTN_THUMBR: R3}
COPY_BUTTONS = ((SOUTH, 0x130), (EAST, 0x131), (WEST, 0x133), (NORTH, 0x134), (L1, BTN_TL), (R1, BTN_TR),
                (SELECT, BTN_SELECT), (START, BTN_START), (GUIDE, BTN_MODE), (L3, BTN_THUMBL),
                (R3, BTN_THUMBR))
DPAD_BUTTONS = {BTN_DPAD_UP: 1, BTN_DPAD_DOWN: 2, BTN_DPAD_LEFT: 4, BTN_DPAD_RIGHT: 8}


def log(msg):
    print(msg, flush=True)


def _read(path, default=''):
    try:
        with open(path, errors='replace') as f:
            return f.read().strip()
    except OSError:
        return default


def _bits(text):
    value = 0
    for word in text.split():
        value = (value << 64) | int(word, 16)
    return value


def _has(bits, n):
    return (bits >> n) & 1


# ---------------------------------------------------------------------------
# The Xbox copy: a virtual wired Xbox 360 controller (uinput; see the kernel's
# Documentation/input/uinput.rst). Its buttons and axes are exactly those of
# Linux's Xbox driver, so SDL (and so Moonlight) knows it as an Xbox 360 controller.
# ---------------------------------------------------------------------------
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT = 0x40045564, 0x40045565, 0x40045567
UI_SET_PHYS = 0x4008556c
UI_DEV_SETUP, UI_ABS_SETUP = 0x405c5503, 0x401c5504
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
COPY_AXES = ((ABS_X, -32768, 32767, 16, 128), (ABS_Y, -32768, 32767, 16, 128), (ABS_Z, 0, 255, 0, 0),
             (ABS_RX, -32768, 32767, 16, 128), (ABS_RY, -32768, 32767, 16, 128), (ABS_RZ, 0, 255, 0, 0),
             (ABS_HAT0X, -1, 1, 0, 0), (ABS_HAT0Y, -1, 1, 0, 0))
# Where each axis is in a state (see usb_combo.py).
STATE_AXES = ((1, ABS_Z), (2, ABS_RZ), (3, ABS_X), (4, ABS_Y), (5, ABS_RX), (6, ABS_RY), (7, ABS_HAT0X),
              (8, ABS_HAT0Y))


class XboxPad:
    """A virtual Xbox 360 controller. write() sends a state; close() releases
    everything first, then removes it."""

    def __init__(self, number):
        if not os.path.exists(UINPUT):
            usb_sharing.run(['modprobe', 'uinput'])
        self.fd = os.open(UINPUT, os.O_WRONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
            for _, code in COPY_BUTTONS:
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_ABS)
            for code, lo, hi, fuzz, flat in COPY_AXES:
                fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
                fcntl.ioctl(self.fd, UI_ABS_SETUP, struct.pack('H2x6i', code, 0, lo, hi, fuzz, flat, 0))
            # Tells the copies apart from real controllers (and from each other).
            fcntl.ioctl(self.fd, UI_SET_PHYS, ('%s/%d' % (COPY_PHYS, number)).encode() + b'\0')
            # struct uinput_setup: bus, vendor, product, version, name, ff_effects_max.
            fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack('HHHH80sI', BUS_USB, MICROSOFT, 0x028e, 0x0114,
                                                           b'Microsoft X-Box 360 pad', 0))
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
        except OSError:
            os.close(self.fd)
            raise
        self.state = NEUTRAL

    def write(self, state):
        old = self.state
        if state == old:
            return
        self.state = state
        events = []
        changed = state[0] ^ old[0]
        if changed:
            for bit, code in COPY_BUTTONS:
                if changed & bit:
                    events.append(EVENT.pack(0, 0, EV_KEY, code, 1 if state[0] & bit else 0))
        for i, code in STATE_AXES:
            if state[i] != old[i]:
                events.append(EVENT.pack(0, 0, EV_ABS, code, state[i]))
        events.append(EVENT.pack(0, 0, EV_SYN, SYN_REPORT, 0))
        try:
            os.write(self.fd, b''.join(events))
        except OSError:
            pass        # the kernel's buffer is full: the next change resends everything that changed

    def close(self):
        self.write(NEUTRAL)
        time.sleep(0.05)        # so Moonlight sends the released buttons before the copy goes
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self.fd)


# ---------------------------------------------------------------------------
# Controllers
# ---------------------------------------------------------------------------
def _usb_address(path):
    """The USB device (like 1-2) an input device belongs to, from its sysfs path."""
    found = None
    for part in os.path.realpath(path).split(os.sep):
        if usb_sharing.PORT_KEY_RE.match(part):
            found = part
    return found


def _usb_model(address):
    path = '%s/%s' % (usb_sharing.SYS_USB, address)
    return '%s:%s' % (_read(path + '/idVendor').lower(), _read(path + '/idProduct').lower())


def input_controllers():
    """The controllers that have a Linux input device on the Pi (Bluetooth, or
    USB not shared), as dicts: key, name, connection, xbox, event, usb."""
    out = []
    for ev in sorted(glob.glob(SYS_INPUT + '/event*'), key=lambda p: int(re.sub(r'\D', '', p) or 0)):
        dev = ev + '/device'
        try:
            keys = _bits(_read(dev + '/capabilities/key', '0'))
            axes = _bits(_read(dev + '/capabilities/abs', '0'))
            props = _bits(_read(dev + '/properties', '0'))
            bus = int(_read(dev + '/id/bustype', '0'), 16)
            vendor = int(_read(dev + '/id/vendor', '0'), 16)
        except ValueError:
            continue
        # A gamepad: face buttons and a stick. That leaves out a DualSense's
        # touchpad and motion sensors, which Linux gives devices of their own.
        if not (_has(keys, BTN_SOUTH) and _has(axes, ABS_X) and _has(axes, ABS_Y)) or \
                _has(props, INPUT_PROP_ACCELEROMETER):
            continue
        if bus == BUS_VIRTUAL or _read(dev + '/phys').startswith(COPY_PHYS):
            continue        # virtual controllers, like the copies themselves
        driver = os.path.basename(os.path.realpath(dev + '/device/driver'))
        name = _read(dev + '/name') or 'Controller'
        if bus == BUS_BLUETOOTH:
            key, connection, usb = 'bt:' + (_read(dev + '/uniq').lower() or name), 'bluetooth', None
        elif bus == BUS_USB:
            usb = _usb_address(dev)
            if not usb:
                continue
            key, connection = 'usb:' + usb, 'usb-pi'
        else:
            continue
        out.append({'key': key, 'name': name, 'connection': connection, 'usb': usb,
                    'xbox': vendor == MICROSOFT or driver in XBOX_DRIVERS,
                    'event': '/dev/input/' + os.path.basename(ev)})
    return out


def shared_controllers():
    """The controllers USB sharing has sent to the PC (bound to usbip-host): the
    same dicts, with pad (a usb_combo.STATE_PADS key, or None if a copy can't
    read it) and bus and devnum instead of event."""
    out, seen = [], None
    try:
        names = sorted(n for n in os.listdir(USBIP_HOST) if usb_sharing.PORT_KEY_RE.match(n))
    except OSError:
        return out
    for address in names:
        path = '%s/%s' % (usb_sharing.SYS_USB, address)
        vid, pid = _read(path + '/idVendor').lower(), _read(path + '/idProduct').lower()
        model = '%s:%s' % (vid, pid)
        pad = usb_combo.state_pad_type(model)
        xbox = vid == '045e' or usb_combo.pad_type(address) in ('xbox360', 'xboxone')
        if not pad and not xbox:
            # Only controllers matter: USB sharing remembers what each device was.
            if seen is None:
                seen = usb_sharing.load_seen()
            if usb_sharing._seen_info(seen, model, model).get('kind') != 'controller':
                continue
        try:
            bus, devnum = int(_read(path + '/busnum')), int(_read(path + '/devnum'))
        except ValueError:
            continue
        out.append({'key': 'usb:' + address, 'name': usb_sharing._name(path, vid, pid), 'connection': 'usb-pc',
                    'usb': address, 'xbox': xbox, 'pad': pad, 'bus': bus, 'devnum': devnum})
    return out


def controllers():
    """Every controller connected to the Pi now, on the Pi or on the PC."""
    shared = shared_controllers()
    keys = {c['key'] for c in shared}
    return shared + [c for c in input_controllers() if c['key'] not in keys]


# ---------------------------------------------------------------------------
# Reading a controller on the Pi (its Linux input device). Opened read-only and
# never grabbed, so Moonlight keeps getting it too.
# ---------------------------------------------------------------------------
def _scale(value, info, lo, hi):
    """value, from the axis's own range (info: min, max), to lo..hi."""
    vmin, vmax = info
    if vmax <= vmin:
        return 0
    return max(lo, min(hi, lo + ((value - vmin) * (hi - lo) + (vmax - vmin) // 2) // (vmax - vmin)))


class InputSource:
    """A controller's Linux input device, read into states."""

    def __init__(self, event):
        self.fd = os.open(event, os.O_RDONLY | os.O_NONBLOCK)
        try:
            axes = _bits(_read('%s/%s/device/capabilities/abs' % (SYS_INPUT, os.path.basename(event)), '0'))
            # Which axis is what. A controller without a right stick on RX/RY
            # (some generic ones) has it on Z/RZ instead, and no analog triggers.
            roles = {ABS_X: 3, ABS_Y: 4, ABS_HAT0X: 7, ABS_HAT0Y: 8}
            if _has(axes, ABS_RX) and _has(axes, ABS_RY):
                roles.update({ABS_RX: 5, ABS_RY: 6, ABS_Z: 1, ABS_RZ: 2})
            elif _has(axes, ABS_Z) and _has(axes, ABS_RZ):
                roles.update({ABS_Z: 5, ABS_RZ: 6})
            self.roles = {a: i for a, i in roles.items() if _has(axes, a)}
            self.ranges = {}
            self.analog_lt, self.analog_rt = 1 in self.roles.values(), 2 in self.roles.values()
            self.dropped = False
            self.sync()
        except OSError:
            os.close(self.fd)
            raise

    def close(self):
        os.close(self.fd)

    def sync(self):
        """Reads the whole state from the device (when opening it, and after
        the kernel dropped events)."""
        state = [0, 0, 0, 0, 0, 0, 0, 0, 0]
        for axis, i in self.roles.items():
            value, vmin, vmax = ABSINFO.unpack(fcntl.ioctl(self.fd, EVIOCGABS(axis), bytes(ABSINFO.size)))[:3]
            self.ranges[axis] = (vmin, vmax)
            state[i] = self.axis_value(axis, i, value)
        pressed = int.from_bytes(fcntl.ioctl(self.fd, EVIOCGKEY, bytes(KEY_BYTES)), 'little')
        for code, bit in SOURCE_BUTTONS.items():
            if _has(pressed, code):
                state[0] |= bit
        self.dpad = sum(b for code, b in DPAD_BUTTONS.items() if _has(pressed, code))
        self.hat = [state[7], state[8]]
        if not self.analog_lt and _has(pressed, BTN_TL2):
            state[1] = 255
        if not self.analog_rt and _has(pressed, BTN_TR2):
            state[2] = 255
        self.values = state
        self.apply_dpad()

    def axis_value(self, axis, i, value):
        if i in (1, 2):
            return _scale(value, self.ranges[axis], 0, 255)
        if i in (7, 8):
            return (value > 0) - (value < 0)
        return _scale(value, self.ranges[axis], -32768, 32767)

    def apply_dpad(self):
        """The d-pad: its hat axes, or its buttons on controllers that have those."""
        d, s = self.dpad, self.values
        s[7] = max(-1, min(1, self.hat[0] + ((d >> 3) & 1) - ((d >> 2) & 1)))
        s[8] = max(-1, min(1, self.hat[1] + ((d >> 1) & 1) - (d & 1)))

    def read(self):
        """The new state after the events waiting, or None if nothing changed.
        Raises OSError when the controller is gone."""
        try:
            data = os.read(self.fd, EVENT.size * 64)
        except BlockingIOError:
            return None
        s = self.values
        ready = False
        for _, _, etype, code, value in EVENT.iter_unpack(data[:len(data) - len(data) % EVENT.size]):
            if etype == EV_SYN:
                if code == SYN_DROPPED:
                    self.dropped = True
                elif code == SYN_REPORT:
                    if self.dropped:        # read everything again (see Documentation/input/event-codes.rst)
                        self.dropped = False
                        self.sync()
                        s = self.values
                    ready = True
            elif self.dropped:
                continue
            elif etype == EV_KEY:
                bit = SOURCE_BUTTONS.get(code)
                if bit:
                    s[0] = s[0] | bit if value else s[0] & ~bit
                elif code in DPAD_BUTTONS:
                    self.dpad = self.dpad | DPAD_BUTTONS[code] if value else self.dpad & ~DPAD_BUTTONS[code]
                    self.apply_dpad()
                elif code == BTN_TL2 and not self.analog_lt:
                    s[1] = 255 if value else 0
                elif code == BTN_TR2 and not self.analog_rt:
                    s[2] = 255 if value else 0
            elif etype == EV_ABS:
                i = self.roles.get(code)
                if i is not None:
                    s[i] = self.axis_value(code, i, value)
                    if i >= 7:
                        self.hat[i - 7] = s[i]
                        self.apply_dpad()
        return tuple(s) if ready else None

    def state(self):
        return tuple(self.values)


# ---------------------------------------------------------------------------
# Messages from the PC's service (UDP). Plain text, "moonlight-pi-setup KIND 1"
# then key=value words:
#   secure-screen: secure=1 or 0, host=the PC's name (every second while a
#                  screen is up, every 5 seconds otherwise, and at once when it changes)
#   hello:         asks the Pi to say who it is (the PC finds the Pi this way):
#                  it answers "moonlight-pi-setup pi 1 name=... tailscale=..."
# ---------------------------------------------------------------------------
def parse_message(data):
    """(kind, {key: value}) or None."""
    try:
        words = data[:512].decode('ascii').split()
    except UnicodeDecodeError:
        return None
    if len(words) < 3 or words[0] != MAGIC or words[2] != '1':
        return None
    fields = {}
    for w in words[3:]:
        k, sep, v = w.partition('=')
        if sep and re.match(r'^[a-z]{1,16}$', k):
            fields[k] = v[:64]
    return words[1], fields


def plain_address(addr):
    """192.168.0.2 rather than ::ffff:192.168.0.2, and without a %interface."""
    addr = addr.split('%')[0]
    return addr[7:] if addr.startswith('::ffff:') and '.' in addr else addr


def open_socket():
    """A UDP socket on PORT, for IPv4 and IPv6 (IPv4 only if IPv6 is off)."""
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('::', PORT))
    except OSError:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('0.0.0.0', PORT))
    s.setblocking(False)
    return s


def tailscale_ipv4():
    rc, out = usb_sharing.run(['ip', '-o', '-4', 'addr', 'show', 'dev', 'tailscale0'], 5)
    m = re.search(r'inet (100\.[0-9.]+)/', out) if rc == 0 else None
    return m.group(1) if m else ''


# ---------------------------------------------------------------------------
# Which controllers get a copy (kept apart from the devices, so it can be tested)
# ---------------------------------------------------------------------------
def plan_copies(ctrls, current, active):
    """Which controllers get a copy now: {key: controller}, at most MAX_COPIES,
    keeping the ones that already have one. And [(controller, why not)] for the
    controllers that don't get one."""
    if not active:
        return {}, []
    want, skipped = {}, []
    for c in sorted(ctrls, key=lambda c: (c['key'] not in current, c['key'])):
        if c['xbox']:
            continue
        if c['connection'] == 'usb-pc' and not c.get('pad'):
            skipped.append((c, "a copy can't read it while it's on the PC: set it to Never in USB sharing"))
        elif len(want) >= MAX_COPIES:
            skipped.append((c, 'there are already %d copies' % MAX_COPIES))
        else:
            want[c['key']] = c
    return want, skipped


def source_id(c):
    return ('usbmon', c['bus'], c['devnum']) if c['connection'] == 'usb-pc' else ('input', c['event'])


class Copy:
    def __init__(self, ctrl, pad, number):
        self.ctrl = ctrl
        self.pad = pad
        self.number = number
        self.source = None          # source_id() of what it reads
        self.input = None           # an InputSource, for a controller on the Pi
        self.reader = None          # reads a USB report into a state, for one on the PC
        self.states = 0             # states read from USB reports
        self.since = time.monotonic()
        self.missing = None         # when its controller disappeared


# ---------------------------------------------------------------------------
# The service (root)
# ---------------------------------------------------------------------------
class Service:
    def __init__(self, force_secure=False):
        self.stop = False
        self.force_secure = force_secure
        self.watcher = usb_sharing.LogWatcher()
        self.streaming, self.pc, self.pending = False, None, None
        self.pc_looked_up, self.pc_addrs = None, set()
        self.moonlight_pid = None
        self.agents = {}            # address -> {'host', 'seen' (time), 'until' (monotonic)}
        self.copies = {}            # controller key -> Copy
        self.numbers = set()
        self.skipped = []
        self.usbmon = usb_combo.Usbmon()
        self.usbmon_handlers = {}
        self.usbmon_failed = False
        self.sock = None
        self.tailscale, self.tailscale_checked = '', 0
        self.status, self.status_written = None, 0
        self.next_step = 0
        self.warned = set()

    # -- the stream ---------------------------------------------------------
    def moonlight_alive(self):
        """Whether Moonlight is still running (without starting a program every second)."""
        if self.moonlight_pid and _read('/proc/%d/comm' % self.moonlight_pid) == 'moonlight-qt':
            return True
        self.moonlight_pid = None
        for comm in glob.glob('/proc/[0-9]*/comm'):
            if _read(comm) == 'moonlight-qt':
                self.moonlight_pid = int(comm.split('/')[2])
                return True
        return False

    def follow_log(self):
        lines, restarted = self.watcher.read()
        if restarted and self.streaming:
            self.end_stream('Moonlight started again')
        for line in lines:
            addr = self.watcher.address(line)
            if addr:
                self.pending = addr
            if usb_sharing.START in line and not self.streaming:
                self.streaming, self.pc = True, self.pending
                log('Stream started (PC %s)' % (self.pc or 'unknown'))
            elif usb_sharing.STOP in line and self.streaming:
                self.end_stream('stream closed')
        if self.streaming and not self.moonlight_alive():
            self.end_stream('Moonlight closed')

    def end_stream(self, why):
        log('Stream ended (%s)' % why)
        self.streaming, self.pc, self.pending = False, None, None

    def secure(self):
        """Whether the PC being streamed to is showing a sign-in or UAC screen."""
        if self.force_secure:
            return True
        now = time.monotonic()
        return any(a['until'] > now for addr, a in self.agents.items() if addr in self.pc_addrs)

    # -- messages -----------------------------------------------------------
    def receive(self):
        """Reads the messages waiting. Returns True if a screen came up or went away."""
        changed = False
        while True:
            try:
                data, src = self.sock.recvfrom(1024)
            except (BlockingIOError, InterruptedError):
                return changed
            except OSError as e:
                log('Error reading a message: %s' % e)
                return changed
            msg = parse_message(data)
            if not msg:
                continue
            kind, fields = msg
            addr = plain_address(src[0])
            if kind == 'hello':
                self.answer(src)
            elif kind == 'secure-screen' and fields.get('secure') in ('0', '1'):
                a = self.agents.get(addr)
                if not a:
                    a = self.agents[addr] = {'host': '', 'seen': 0, 'until': 0}
                    log('Heard from the PC on %s (%s)' % (fields.get('host') or 'a PC', addr))
                a['host'] = re.sub(r'[^A-Za-z0-9._-]', '', fields.get('host', ''))[:32]
                a['seen'] = time.time()
                up = fields['secure'] == '1'
                was = a['until'] > time.monotonic()
                a['until'] = time.monotonic() + SECURE_SECONDS if up else 0
                if was != up:
                    changed = changed or addr in self.pc_addrs
                    log('%s: sign-in or UAC screen %s' % (a['host'] or addr, 'up' if up else 'gone'))

    def answer(self, src):
        if time.monotonic() - self.tailscale_checked > 60:
            self.tailscale, self.tailscale_checked = tailscale_ipv4(), time.monotonic()
        reply = '%s pi 1 name=%s%s' % (MAGIC, socket.gethostname(),
                                       ' tailscale=' + self.tailscale if self.tailscale else '')
        try:
            self.sock.sendto(reply.encode(), src)
        except OSError:
            pass

    # -- copies -------------------------------------------------------------
    def update_copies(self):
        active = self.streaming and self.secure()
        ctrls = controllers() if active else []
        want, self.skipped = plan_copies(ctrls, self.copies, active)
        present = {c['key'] for c in ctrls}
        now = time.monotonic()
        for key in [k for k in self.copies if k not in want]:
            copy = self.copies[key]
            if not active:
                self.remove_copy(key, 'the screen went away')
            elif key in present:
                self.remove_copy(key, "a copy can't read it now")
            elif copy.missing is None:
                # Gone for now: it may be moving to the PC (USB sharing). Keep
                # the copy, with nothing pressed, for a moment.
                copy.missing = now
                self.close_source(copy)
                copy.pad.write(NEUTRAL)
            elif now - copy.missing > MISSING_SECONDS:
                self.remove_copy(key, 'its controller was disconnected')
        for key, c in want.items():
            copy = self.copies.get(key)
            if not copy:
                free = set(range(MAX_COPIES)) - self.numbers
                if not free:
                    continue        # a copy whose controller is missing still has its number
                number = min(free)
                try:
                    pad = XboxPad(number)
                except OSError as e:
                    if 'uinput' not in self.warned:
                        log("Couldn't create an Xbox copy: %s" % e)
                        self.warned.add('uinput')
                    continue
                self.numbers.add(number)
                copy = self.copies[key] = Copy(c, pad, number)
                log('Xbox copy of %s (%s) created' % (c['name'], c['connection']))
            copy.ctrl = c
            copy.missing = None
            sid = source_id(c)
            if copy.source != sid:
                self.close_source(copy)
                self.open_source(copy, c, sid)
            self.check_reports(copy)
        self.usbmon_handlers = {(c.ctrl['bus'], c.ctrl['devnum']): c.reader
                                for c in self.copies.values() if c.reader}
        if self.usbmon_handlers and self.usbmon.fd is None:
            try:
                self.usbmon.open()
                self.usbmon_failed = False
            except OSError:
                usb_sharing.run(['modprobe', 'usbmon'])
                try:
                    self.usbmon.open()
                except OSError as e:
                    if not self.usbmon_failed:
                        log("Can't read controllers on the PC (usbmon): %s" % e)
                    self.usbmon_failed = True
        elif not self.usbmon_handlers and self.usbmon.fd is not None:
            self.usbmon.close()

    def open_source(self, copy, c, sid):
        copy.source = sid
        copy.since = time.monotonic()
        copy.states = 0
        if sid[0] == 'input':
            try:
                copy.input = InputSource(c['event'])
            except OSError as e:
                if ('open', c['event']) not in self.warned:
                    self.warned.add(('open', c['event']))
                    log("Couldn't read %s (%s): %s" % (c['name'], c['event'], e))
                copy.source = None
                return
            self.warned.discard(('open', c['event']))
            copy.pad.write(copy.input.state())
            log('%s: the copy reads it on the Pi (%s)' % (c['name'], c['event']))
        else:
            decode = usb_combo.STATE_PADS[c['pad']][1]

            def reader(data, copy=copy, decode=decode):
                state = decode(data)
                if state is not None:
                    copy.states += 1
                    copy.pad.write(state)
            copy.reader = reader
            log('%s: the copy reads it on the PC (USB %s)' % (c['name'], c['usb']))

    def close_source(self, copy):
        if copy.input:
            copy.input.close()
        copy.input = copy.reader = None
        copy.source = None

    def check_reports(self, copy):
        """A Switch Pro controller on the PC only sends its full reports once
        something on the PC (like Steam) has started it."""
        if copy.reader and not copy.states and time.monotonic() - copy.since > 3:
            key = ('quiet', copy.ctrl['key'])
            if key not in self.warned:
                self.warned.add(key)
                log("%s isn't sending reports a copy can read%s" % (
                    copy.ctrl['name'], ' (on the PC, a Switch Pro controller only sends them once something '
                    'like Steam has started it: set it to Never in USB sharing)' if copy.ctrl['pad'] == 'switchpro'
                    else ''))

    def remove_copy(self, key, why):
        copy = self.copies.pop(key)
        self.close_source(copy)
        copy.pad.close()
        self.numbers.discard(copy.number)
        self.warned.discard(('quiet', key))
        log('Xbox copy of %s removed (%s)' % (copy.ctrl['name'], why))

    def read_inputs(self, ready):
        for copy in list(self.copies.values()):
            if copy.input and copy.input.fd in ready:
                try:
                    state = copy.input.read()
                except OSError:
                    self.close_source(copy)     # unplugged: the next step deals with it
                    self.next_step = 0
                    continue
                if state is not None:
                    copy.pad.write(state)

    # -- status -------------------------------------------------------------
    def write_status(self):
        now = time.time()
        agents = sorted(({'address': a, 'host': v['host'], 'seen': int(v['seen'])} for a, v in self.agents.items()
                         if now - v['seen'] < 3600), key=lambda a: -a['seen'])
        status = {'streaming': self.streaming, 'pc': self.pc if self.streaming else None,
                  'secure': self.streaming and self.secure(),
                  'copies': [{'name': c.ctrl['name'], 'connection': c.ctrl['connection'],
                              'source': (c.source or ('none',))[0], 'reading': bool(c.states or c.input)}
                             for c in self.copies.values()],
                  'skipped': [{'name': c['name'], 'why': why} for c, why in self.skipped]}
        # Rewritten when something changes, and every 10 seconds while a PC checks in.
        if status != self.status or (agents and now - self.status_written >= 10):
            self.status, self.status_written = status, now
            usb_sharing.write_json(STATUS, dict(status, agents=agents, updated=int(now)))

    # -- main loop ----------------------------------------------------------
    def step(self):
        self.follow_log()
        pc = self.pc if self.streaming else None
        if pc != self.pc_looked_up:
            self.pc_looked_up = pc
            self.pc_addrs = usb_sharing.pc_addresses(pc) if pc else set()
        self.update_copies()
        self.write_status()

    def run(self):
        signal.signal(signal.SIGTERM, lambda *a: setattr(self, 'stop', True))
        signal.signal(signal.SIGINT, lambda *a: setattr(self, 'stop', True))
        try:
            self.sock = open_socket()
        except OSError as e:
            log("Couldn't listen on UDP port %d: %s" % (PORT, e))
            return 1
        self.streaming, self.pc = self.watcher.initial()
        log('Listening for the PC on UDP port %d%s%s' % (
            PORT, ' (a stream is running)' if self.streaming else '',
            '; acting as if the PC showed a sign-in screen (--force-secure)' if self.force_secure else ''))
        try:
            while not self.stop:
                if time.monotonic() >= self.next_step:
                    try:
                        self.step()
                    except Exception as e:      # keep going: the next step may work
                        log('Error: %s' % e)
                    self.next_step = time.monotonic() + STEP_SECONDS
                fds = [self.sock] + [c.input.fd for c in self.copies.values() if c.input]
                if self.usbmon.fd is not None:
                    fds.append(self.usbmon.fd)
                try:
                    ready = select.select(fds, [], [], max(0, self.next_step - time.monotonic()))[0]
                except InterruptedError:
                    continue
                if self.sock in ready and self.receive():
                    self.next_step = 0          # a screen came up or went away: act now
                if self.usbmon.fd is not None and self.usbmon.fd in ready:
                    try:
                        self.usbmon.read(self.usbmon_handlers)
                    except OSError as e:
                        log('Stopped reading controllers on the PC: %s' % e)
                        self.usbmon.close()
                self.read_inputs(set(ready))
        finally:
            for key in list(self.copies):
                self.remove_copy(key, 'the service stopped')
            self.usbmon.close()
            if self.sock:
                self.sock.close()
            try:
                os.remove(STATUS)
            except OSError:
                pass
            log('Stopped')
        return 0


# ---------------------------------------------------------------------------
# For the menu and the command line
# ---------------------------------------------------------------------------
def load_status(path=STATUS):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def describe(snapshot=None):
    """The controllers connected now, for the menu: [(name, gets a copy, why)].
    A USB controller that USB sharing sends to the PC during a stream is
    described as it will be then."""
    goes = {}
    if os.path.exists(USB_SHARING_SERVICE):
        snapshot = usb_sharing.snapshot() if snapshot is None else snapshot
        goes = {d['address']: d.get('goes') for d in snapshot['devices']}
    out = []
    for c in controllers():
        if c['xbox']:
            out.append((c['name'], False, 'Xbox controllers work on these screens by themselves'))
            continue
        on_pc = c['connection'] == 'usb-pc' or bool(c['usb'] and goes.get(c['usb']))
        pad = c.get('pad') if c['connection'] == 'usb-pc' else \
            usb_combo.state_pad_type(_usb_model(c['usb'])) if c['usb'] else None
        if not on_pc:
            out.append((c['name'], True, 'Gets an Xbox copy'))
        elif pad == 'switchpro':
            out.append((c['name'], True, 'Gets one only if Steam is open on the PC: to always get one, set it '
                        'to Never in USB sharing'))
        elif pad:
            out.append((c['name'], True, 'Gets an Xbox copy (it goes to the PC)'))
        else:
            out.append((c['name'], False, "It goes to the PC, where a copy can't read it: set it to Never in "
                        'USB sharing'))
    return out


BUTTON_NAMES = ((SOUTH, 'A'), (EAST, 'B'), (WEST, 'X'), (NORTH, 'Y'), (L1, 'LB'), (R1, 'RB'), (SELECT, 'View'),
                (START, 'Menu'), (GUIDE, 'Guide'), (L3, 'LS'), (R3, 'RS'))


def format_state(s):
    """A state as the Xbox copy shows it."""
    pressed = ' '.join(n for bit, n in BUTTON_NAMES if s[0] & bit) or '-'
    dpad = {(0, -1): 'up', (0, 1): 'down', (-1, 0): 'left', (1, 0): 'right', (-1, -1): 'up-left',
            (1, -1): 'up-right', (-1, 1): 'down-left', (1, 1): 'down-right'}.get((s[7], s[8]), '-')
    return 'buttons %s | d-pad %s | LT %d RT %d | left %d,%d | right %d,%d' % (
        pressed, dpad, s[1], s[2], s[3], s[4], s[5], s[6])


def capture(raw, seconds):
    """Prints what each controller's copy would read (raw: the reports or
    events themselves), until Ctrl+C."""
    stop = []
    signal.signal(signal.SIGINT, lambda *a: stop.append(1))
    sources, handlers, mon = {}, {}, usb_combo.Usbmon()
    for c in controllers():
        if c['xbox']:
            continue
        if c['connection'] == 'usb-pc':
            if not c['pad'] and not raw:
                print("%s: on the PC, a copy can't read it" % c['name'])
                continue
            decode = usb_combo.STATE_PADS[c['pad']][1] if c['pad'] else None

            def show(data, name=c['name'], decode=decode, last=[None]):
                state = None if raw else decode(data)
                if raw:
                    print('%s: %s' % (name, data.hex()), flush=True)
                elif state is not None and state != last[0]:
                    last[0] = state
                    print('%s: %s' % (name, format_state(state)), flush=True)
            handlers[(c['bus'], c['devnum'])] = show
            print('%s: on the PC (USB %s), reading its USB reports' % (c['name'], c['usb']))
        else:
            try:
                src = InputSource(c['event'])
            except OSError as e:
                print("%s: can't read %s: %s" % (c['name'], c['event'], e))
                continue
            sources[src.fd] = (c['name'], src)
            print('%s: on the Pi (%s): %s' % (c['name'], c['event'], format_state(src.state())))
    if handlers:
        try:
            mon.open()
        except OSError as e:
            print("Can't read USB reports (run it with sudo): %s" % e)
            handlers = {}
    if not sources and not handlers:
        print('No controllers to read.')
        return 1
    print('Press buttons (Ctrl+C to stop)...')
    end = time.monotonic() + seconds
    while not stop and time.monotonic() < end:
        fds = list(sources) + ([mon.fd] if handlers else [])
        try:
            ready = select.select(fds, [], [], 0.5)[0]
        except InterruptedError:
            continue
        for fd in ready:
            if fd == mon.fd:
                mon.read(handlers)
                continue
            name, src = sources[fd]
            if raw:
                data = os.read(fd, EVENT.size * 64)
                for _, _, t, code, v in EVENT.iter_unpack(data[:len(data) - len(data) % EVENT.size]):
                    if t:
                        print('%s: type %d code 0x%03x value %d' % (name, t, code, v), flush=True)
                continue
            state = src.read()
            if state is not None:
                print('%s: %s' % (name, format_state(state)), flush=True)
    mon.close()
    return 0


def main(args):
    if args[:1] == ['serve']:
        return Service(force_secure='--force-secure' in args).run()
    if args[:1] == ['status']:
        st = load_status()
        if not st:
            print("The Xbox copy service isn't running.")
            return 1
        print(json.dumps(st, indent=2))
        return 0
    if args[:1] == ['controllers']:
        for name, gets, why in describe():
            print('%-34s %s' % (name, why))
        return 0
    if args[:1] == ['capture']:
        seconds = 3600.0
        if '--seconds' in args:
            try:
                seconds = float(args[args.index('--seconds') + 1])
            except (IndexError, ValueError):
                pass
        return capture('--raw' in args, seconds)
    print('Use: xbox_copy.py serve [--force-secure] | status | controllers | capture [--raw] [--seconds N]',
          file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
