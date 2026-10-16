#!/usr/bin/env python3
"""Moonlight's controller shortcut for ending a stream (Select+Start+L1+R1),
for controllers that USB sharing has sent to the PC.

While a controller is on the PC, Moonlight on the Pi can't see it, so the
shortcut would do nothing. Its button presses still pass through the Pi on
their way to the PC, though: the USB sharing service reads them with Linux's
USB monitor (usbmon), without changing or delaying them. When a controller
holds the shortcut, it presses Moonlight's keyboard shortcut for ending a stream
(Ctrl+Alt+Shift+Q) on a virtual keyboard, so Moonlight ends the stream just as
it would for the controller.

Knows the button layouts of PlayStation controllers (DualSense, DualShock 4)
and Xbox controllers (360, One and Series, and controllers that work like them).

Installed by moonlight-pi-setup.
"""
import os
import select
import struct
import threading
import time

SYS_USB = '/sys/bus/usb/devices'
USBMON = '/dev/usbmon0'         # every USB bus
UINPUT = '/dev/uinput'
COOLDOWN = 5                    # seconds before the shortcut can end a stream again


# ---------------------------------------------------------------------------
# Controllers. Each reads one input report: True while it holds Select, Start,
# L1 and R1, False if it doesn't, and None for other messages.
# ---------------------------------------------------------------------------
def _dualsense(d):
    # USB report 0x01. Byte 9: L1 0x01, R1 0x02, Create 0x10, Options 0x20.
    if len(d) < 10 or d[0] != 0x01:
        return None
    return d[9] & 0x33 == 0x33


def _dualshock4(d):
    # USB report 0x01. Byte 6: L1 0x01, R1 0x02, Share 0x10, Options 0x20.
    if len(d) < 7 or d[0] != 0x01:
        return None
    return d[6] & 0x33 == 0x33


def _xbox360(d):
    # Message 0x00. Byte 2: Start 0x10, Back 0x20. Byte 3: LB 0x01, RB 0x02.
    if len(d) < 4 or d[0] != 0x00:
        return None
    return d[2] & 0x30 == 0x30 and d[3] & 0x03 == 0x03


def _xbox_one(d):
    # GIP input message 0x20. Byte 4: Menu 0x04, View 0x08. Byte 5: LB 0x10, RB 0x20.
    if len(d) < 6 or d[0] != 0x20:
        return None
    return d[4] & 0x0c == 0x0c and d[5] & 0x30 == 0x30


PADS = {
    'dualsense': ('DualSense', _dualsense),
    'dualshock4': ('DualShock 4', _dualshock4),
    'xbox360': ('Xbox 360 controller', _xbox360),
    'xboxone': ('Xbox controller', _xbox_one),
}
SONY = {
    '054c:0ce6': 'dualsense', '054c:0df2': 'dualsense',         # DualSense, DualSense Edge
    '054c:05c4': 'dualshock4', '054c:09cc': 'dualshock4',       # DualShock 4, both versions
    '054c:0ba0': 'dualshock4',                                  # its wireless adapter
}
# Xbox controllers, by interface (class, subclass, protocol), as Linux's xpad driver finds them.
XBOX = {(0xff, 0x5d, 0x01): 'xbox360', (0xff, 0x47, 0xd0): 'xboxone'}


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ''


def _interfaces(address):
    """(class, subclass, protocol) of each of a device's interfaces, from the
    descriptors Linux keeps for it (still there while it's on the PC)."""
    try:
        with open('%s/%s/descriptors' % (SYS_USB, address), 'rb') as f:
            raw = f.read()
    except OSError:
        return set()
    found, i = set(), 0
    while i + 2 <= len(raw) and raw[i] >= 2:
        if raw[i + 1] == 4 and raw[i] >= 9 and i + 9 <= len(raw):     # an interface descriptor
            found.add((raw[i + 5], raw[i + 6], raw[i + 7]))
        i += raw[i]
    return found


def pad_type(address):
    """Which of PADS the device at this address is, or None."""
    model = '%s:%s' % (_read('%s/%s/idVendor' % (SYS_USB, address)).lower(),
                       _read('%s/%s/idProduct' % (SYS_USB, address)).lower())
    if model in SONY:
        return SONY[model]
    return next((XBOX[i] for i in sorted(_interfaces(address)) if i in XBOX), None)


# ---------------------------------------------------------------------------
# Watching the controllers (usbmon's binary interface; see the kernel's
# Documentation/usb/usbmon.rst). A read returns one event: a 48-byte header,
# then the data it captured.
# ---------------------------------------------------------------------------
HEADER = struct.Struct('<8xBBBBH14xiII')    # type, transfer, endpoint, device, bus, status, length, captured
HEADER_SIZE = 48
COMPLETE = ord('C')                         # a transfer that finished (an input report arrived)
INTERRUPT = 1
MON_IOCT_RING_SIZE = 0x9204


class Watcher(threading.Thread):
    """Calls pressed(address) when a controller being watched holds the
    shortcut. Reads USB traffic only while there's a controller to watch."""

    def __init__(self, pressed, log):
        super().__init__(name='controller-shortcut', daemon=True)
        self.pressed = pressed
        self.log = log
        self.lock = threading.Lock()
        self.pads = {}          # (bus, device number) -> (address, reads a report)
        self.wake = threading.Event()
        self.stop = False
        self.held = set()       # addresses holding the shortcut now
        self.last = None        # when the shortcut last ended a stream
        self.failed = False

    def watch(self, pads):
        """pads: {(bus, device number): (address, a PADS key)}, or {} to stop watching."""
        with self.lock:
            self.pads = {k: (a, PADS[t][1]) for k, (a, t) in pads.items()}
        self.wake.set()

    def close(self):
        self.stop = True
        self.wake.set()

    def run(self):
        buf = bytearray(256 * 1024)     # more than usbmon captures for one event
        fd = None
        while not self.stop:
            with self.lock:
                pads = self.pads
            if not pads:
                if fd is not None:
                    os.close(fd)
                    fd = None
                    self.held.clear()
                self.wake.wait(1)
                self.wake.clear()
                continue
            if fd is None:
                fd = self.open()
                if fd is None:
                    self.wake.wait(5)
                    self.wake.clear()
                    continue
            try:
                if not select.select([fd], [], [], 0.5)[0]:
                    continue
                while True:
                    try:
                        n = os.readv(fd, [buf])
                    except BlockingIOError:
                        break
                    self.event(buf, n, pads)
            except OSError as e:
                self.log("Stopped watching controllers for Moonlight's shortcut: %s" % e)
                os.close(fd)
                fd = None
                self.wake.wait(5)
                self.wake.clear()
        if fd is not None:
            os.close(fd)

    def open(self):
        try:
            fd = os.open(USBMON, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            if not self.failed:
                self.log("Can't watch controllers for Moonlight's shortcut: %s" % e)
                self.failed = True
            return None
        self.failed = False
        try:
            import fcntl
            fcntl.ioctl(fd, MON_IOCT_RING_SIZE, 1024 * 1024)    # room for a burst of other traffic
        except OSError:
            pass
        return fd

    def event(self, buf, n, pads):
        if n < HEADER_SIZE:
            return
        kind, transfer, endpoint, device, bus, status, _, captured = HEADER.unpack_from(buf)
        if kind != COMPLETE or transfer != INTERRUPT or not endpoint & 0x80 or status or not captured:
            return
        pad = pads.get((bus, device))
        if not pad:
            return
        address, reads = pad
        held = reads(bytes(buf[HEADER_SIZE:min(n, HEADER_SIZE + captured)]))
        if held is None:
            return
        if not held:
            self.held.discard(address)
        elif address not in self.held:
            self.held.add(address)
            now = time.monotonic()
            if self.last is None or now - self.last > COOLDOWN:
                self.last = now
                self.pressed(address)


# ---------------------------------------------------------------------------
# The virtual keyboard (uinput; see the kernel's Documentation/input/uinput.rst)
# ---------------------------------------------------------------------------
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
EV_SYN, EV_KEY = 0, 1
BUS_VIRTUAL = 0x06
KEY_Q, KEY_LEFTCTRL, KEY_LEFTSHIFT, KEY_LEFTALT = 16, 29, 42, 56


class Keyboard:
    """A virtual keyboard that only ever presses Ctrl+Alt+Shift+Q. It has a
    whole keyboard's keys, so Moonlight treats it as a keyboard."""

    def __init__(self):
        import fcntl
        self.fd = os.open(UINPUT, os.O_WRONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
            for code in range(1, 128):
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
            # struct uinput_user_dev: name, id (bus, vendor, product, version),
            # ff_effects_max, and four arrays of 64 axis values (unused).
            os.write(self.fd, struct.pack('<80sHHHHI', b'Moonlight Pi controller shortcut',
                                          BUS_VIRTUAL, 0, 0, 1, 0) + bytes(4 * 64 * 4))
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
        except OSError:
            os.close(self.fd)
            raise

    def _send(self, keys, value):
        os.write(self.fd, b''.join(struct.pack('llHHi', 0, 0, EV_KEY, k, value) for k in keys) +
                 struct.pack('llHHi', 0, 0, EV_SYN, 0, 0))

    def end_stream(self):
        mods = (KEY_LEFTCTRL, KEY_LEFTALT, KEY_LEFTSHIFT)
        self._send(mods, 1)
        time.sleep(0.05)
        self._send((KEY_Q,), 1)
        time.sleep(0.05)
        self._send((KEY_Q,), 0)
        self._send(mods, 0)

    def close(self):
        import fcntl
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self.fd)
