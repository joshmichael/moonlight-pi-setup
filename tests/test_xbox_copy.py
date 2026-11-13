"""Tests for the Xbox copies (menu/xbox_copy.py) and the controller reports
they read (menu/usb_combo.py). Run on the Pi (or any Linux):

    python3 -m unittest discover -s tests

The last test creates real virtual controllers, so it only runs as root.
"""
import glob
import os
import struct
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'menu'))

import usb_combo  # noqa: E402
import xbox_copy  # noqa: E402
from usb_combo import SOUTH, EAST, WEST, NORTH, L1, R1, SELECT, START, GUIDE, L3, R3, NEUTRAL  # noqa: E402


def dualsense_report(lx=128, ly=128, rx=128, ry=128, l2=0, r2=0, face=0x08, b1=0, b2=0):
    d = bytearray(64)
    d[0] = 0x01
    d[1:7] = bytes((lx, ly, rx, ry, l2, r2))
    d[7] = 0x55          # a counter: changes with every report
    d[8], d[9], d[10] = face, b1, b2
    return bytes(d)


class DecoderTests(unittest.TestCase):
    def test_dualsense_neutral(self):
        self.assertEqual(usb_combo.dualsense_state(dualsense_report()), (0, 0, 0, 128, 128, 128, 128, 0, 0))

    def test_dualsense_buttons_by_position(self):
        # Cross (bottom) is the Xbox A, Circle B, Square X, Triangle Y.
        for bit, expect in ((0x20, SOUTH), (0x40, EAST), (0x10, WEST), (0x80, NORTH)):
            self.assertEqual(usb_combo.dualsense_state(dualsense_report(face=0x08 | bit))[0], expect)
        s = usb_combo.dualsense_state(dualsense_report(b1=0xf3, b2=0x01))
        self.assertEqual(s[0], L1 | R1 | SELECT | START | L3 | R3 | GUIDE)

    def test_dualsense_sticks_triggers_dpad(self):
        s = usb_combo.dualsense_state(dualsense_report(lx=0, ly=255, rx=255, ry=0, l2=200, r2=255, face=0x01))
        self.assertEqual(s[1:], (200, 255, -32768, 32767, 32767, -32768, 1, -1))   # d-pad up-right
        self.assertEqual(usb_combo.dualsense_state(dualsense_report(face=0x06))[7:], (-1, 0))    # left

    def test_dualsense_other_reports(self):
        self.assertIsNone(usb_combo.dualsense_state(b'\x31' + bytes(77)))
        self.assertIsNone(usb_combo.dualsense_state(b'\x01\x80'))

    def test_dualshock4(self):
        d = bytearray(64)
        d[0], d[1:5], d[5], d[6], d[7], d[8], d[9] = 1, bytes((128, 0, 128, 128)), 0x24, 0x21, 0x05, 10, 20
        s = usb_combo.dualshock4_state(bytes(d))
        self.assertEqual(s, (SOUTH | L1 | START | GUIDE, 10, 20, 128, -32768, 128, 128, 0, 1))

    def test_switch_pro(self):
        d = bytearray(64)
        d[0] = 0x30
        d[3], d[4], d[5] = 0x04 | 0x80, 0x02 | 0x10, 0x02 | 0x40     # B, ZR; Plus, Home; up, L
        # Left stick right and up (12-bit, up larger); right stick in the middle.
        lx, ly, rx, ry = 3448, 3448, 2048, 2048
        d[6], d[7], d[8] = lx & 0xff, (lx >> 8) | (ly & 0x0f) << 4, ly >> 4
        d[9], d[10], d[11] = rx & 0xff, (rx >> 8) | (ry & 0x0f) << 4, ry >> 4
        s = usb_combo.switch_pro_state(bytes(d))
        self.assertEqual(s[0], SOUTH | START | GUIDE | L1)
        self.assertEqual(s[1:3], (0, 255))
        self.assertEqual(s[3:7], (32767, -32768, 0, 0))
        self.assertEqual(s[7:], (0, -1))
        self.assertIsNone(usb_combo.switch_pro_state(b'\x21' + bytes(63)))   # a subcommand reply

    def test_shortcut_unchanged(self):
        self.assertTrue(usb_combo._dualsense(dualsense_report(b1=0x33)))
        self.assertFalse(usb_combo._dualsense(dualsense_report(b1=0x31)))
        self.assertEqual(usb_combo.state_pad_type('054c:0ce6'), 'dualsense')
        self.assertEqual(usb_combo.state_pad_type('057e:2009'), 'switchpro')
        self.assertIsNone(usb_combo.state_pad_type('045e:028e'))


class MessageTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(xbox_copy.parse_message(b'moonlight-pi-setup secure-screen 1 secure=1 host=PC'),
                         ('secure-screen', {'secure': '1', 'host': 'PC'}))
        self.assertEqual(xbox_copy.parse_message(b'moonlight-pi-setup hello 1'), ('hello', {}))
        self.assertIsNone(xbox_copy.parse_message(b'moonlight-pi-setup secure-screen 2 secure=1'))
        self.assertIsNone(xbox_copy.parse_message(b'something else'))
        self.assertIsNone(xbox_copy.parse_message(b'\xff\xfe'))

    def test_addresses(self):
        self.assertEqual(xbox_copy.plain_address('::ffff:192.168.0.46'), '192.168.0.46')
        self.assertEqual(xbox_copy.plain_address('fe80::1%eth0'), 'fe80::1')
        self.assertEqual(xbox_copy.plain_address('fd7a:115c:a1e0::1'), 'fd7a:115c:a1e0::1')


def ctrl(key, connection='bluetooth', xbox=False, pad=None):
    return {'key': key, 'name': key, 'connection': connection, 'xbox': xbox, 'pad': pad, 'usb': None,
            'event': '/dev/input/event9', 'bus': 1, 'devnum': 5}


class PlanTests(unittest.TestCase):
    def test_nothing_without_a_screen(self):
        self.assertEqual(xbox_copy.plan_copies([ctrl('bt:1')], {}, False), ({}, []))

    def test_xbox_controllers_left_alone(self):
        want, skipped = xbox_copy.plan_copies([ctrl('bt:x', xbox=True), ctrl('bt:ds')], {}, True)
        self.assertEqual(list(want), ['bt:ds'])
        self.assertEqual(skipped, [])

    def test_unreadable_on_the_pc(self):
        c = ctrl('usb:1-2', 'usb-pc', pad=None)
        want, skipped = xbox_copy.plan_copies([c], {}, True)
        self.assertEqual(want, {})
        self.assertIn('Never', skipped[0][1])

    def test_at_most_four_keeping_existing(self):
        ctrls = [ctrl('bt:%d' % i) for i in range(6)]
        want, skipped = xbox_copy.plan_copies(ctrls, {'bt:5': object()}, True)
        self.assertEqual(len(want), 4)
        self.assertIn('bt:5', want)
        self.assertEqual(len(skipped), 2)

    def test_moving_to_the_pc_keeps_the_copy(self):
        on_pi = ctrl('usb:1-1', 'usb-pi')
        on_pc = ctrl('usb:1-1', 'usb-pc', pad='dualsense')
        self.assertEqual(xbox_copy.source_id(on_pi), ('input', '/dev/input/event9'))
        self.assertEqual(xbox_copy.source_id(on_pc), ('usbmon', 1, 5))
        want, _ = xbox_copy.plan_copies([on_pc], {'usb:1-1': object()}, True)
        self.assertEqual(list(want), ['usb:1-1'])


class FakePad(xbox_copy.XboxPad):
    """An XboxPad that records what it would send, without uinput."""

    def __init__(self):
        self.state = NEUTRAL
        self.sent = []

    def write(self, state):
        real_write = os.write
        os.write = lambda fd, data: self.sent.append(data) or len(data)
        self.fd = -1
        try:
            super().write(state)
        finally:
            os.write = real_write


def events(data):
    return [(t, c, v) for _, _, t, c, v in xbox_copy.EVENT.iter_unpack(data)]


class PadTests(unittest.TestCase):
    def test_only_changes_are_sent(self):
        pad = FakePad()
        pad.write(NEUTRAL)
        self.assertEqual(pad.sent, [])
        pad.write((SOUTH | NORTH, 0, 0, 0, 0, 0, 0, 0, -1))
        self.assertEqual(events(pad.sent[0]), [(1, 0x130, 1), (1, 0x134, 1), (3, 0x11, -1), (0, 0, 0)])
        pad.write((NORTH, 0, 0, 0, 0, 0, 0, 0, -1))
        self.assertEqual(events(pad.sent[1]), [(1, 0x130, 0), (0, 0, 0)])

    def test_west_is_xbox_x(self):
        # xpad reports the Xbox X button (on the left) as BTN_X (0x133).
        pad = FakePad()
        pad.write((WEST,) + NEUTRAL[1:])
        self.assertEqual(events(pad.sent[0])[0], (1, 0x133, 1))


class ServiceCopyTests(unittest.TestCase):
    """The service's copies, with the devices replaced by stand-ins."""

    def setUp(self):
        self.saved = (xbox_copy.controllers, xbox_copy.XboxPad, xbox_copy.InputSource)
        self.ctrls = []
        self.pads = []
        test = self

        class Pad:
            def __init__(self, number):
                self.state, self.closed, self.number = NEUTRAL, False, number
                test.pads.append(self)

            def write(self, state):
                self.state = state

            def close(self):
                self.closed = True

        class Source:
            def __init__(self, event):
                self.event, self.fd = event, -1

            def state(self):
                return (SOUTH,) + NEUTRAL[1:]

            def close(self):
                pass

        xbox_copy.controllers = lambda: list(self.ctrls)
        xbox_copy.XboxPad, xbox_copy.InputSource = Pad, Source
        self.service = xbox_copy.Service(force_secure=True)
        self.service.streaming = True

    def tearDown(self):
        xbox_copy.controllers, xbox_copy.XboxPad, xbox_copy.InputSource = self.saved

    def test_moving_to_the_pc_keeps_the_copy(self):
        s = self.service
        self.ctrls = [ctrl('usb:1-1', 'usb-pi')]
        s.update_copies()
        copy = s.copies['usb:1-1']
        self.assertEqual(copy.pad.state[0], SOUTH)          # it read the controller straight away
        self.ctrls = []                                    # unbound from the Pi, not yet shared
        s.update_copies()
        self.assertIs(s.copies['usb:1-1'], copy)
        self.assertEqual(copy.pad.state, NEUTRAL)
        self.ctrls = [ctrl('usb:1-1', 'usb-pc', pad='dualsense')]
        s.update_copies()
        self.assertIs(s.copies['usb:1-1'], copy)
        self.assertEqual(copy.source, ('usbmon', 1, 5))
        self.assertIn((1, 5), s.usbmon_handlers)
        s.usbmon_handlers[(1, 5)](dualsense_report(face=0x08 | 0x80))     # Triangle
        self.assertEqual(copy.pad.state[0], NORTH)
        self.assertEqual(len(self.pads), 1)
        s.usbmon.close()

    def test_unplugged_copy_goes_after_a_while(self):
        s = self.service
        self.ctrls = [ctrl('bt:aa')]
        s.update_copies()
        self.ctrls = []
        s.update_copies()
        self.assertIn('bt:aa', s.copies)
        s.copies['bt:aa'].missing -= xbox_copy.MISSING_SECONDS + 1
        s.update_copies()
        self.assertEqual(s.copies, {})
        self.assertTrue(self.pads[0].closed)

    def test_screen_gone_removes_copies(self):
        s = self.service
        self.ctrls = [ctrl('bt:aa'), ctrl('bt:bb')]
        s.update_copies()
        self.assertEqual(len(s.copies), 2)
        s.force_secure = False
        s.update_copies()
        self.assertEqual(s.copies, {})
        self.assertTrue(all(p.closed for p in self.pads))


class ScaleTests(unittest.TestCase):
    def test_scale(self):
        self.assertEqual(xbox_copy._scale(0, (0, 255), -32768, 32767), -32768)
        self.assertEqual(xbox_copy._scale(255, (0, 255), -32768, 32767), 32767)
        self.assertEqual(xbox_copy._scale(128, (0, 255), -32768, 32767), 128)
        self.assertEqual(xbox_copy._scale(32767, (-32767, 32767), 0, 255), 255)
        self.assertEqual(xbox_copy._scale(5, (5, 5), 0, 255), 0)


# ---------------------------------------------------------------------------
# With real devices: a virtual "DualSense" (laid out as Linux's PlayStation
# driver lays one out) read by InputSource, and an Xbox copy.
# ---------------------------------------------------------------------------
@unittest.skipUnless(os.geteuid() == 0 and os.path.exists('/dev/uinput'), 'needs root and /dev/uinput')
class DeviceTests(unittest.TestCase):
    def make_source_device(self):
        import fcntl
        fd = os.open('/dev/uinput', os.O_WRONLY | os.O_NONBLOCK)
        fcntl.ioctl(fd, xbox_copy.UI_SET_EVBIT, xbox_copy.EV_KEY)
        for code in (0x130, 0x131, 0x133, 0x134, 0x136, 0x137, 0x138, 0x139, 0x13a, 0x13b, 0x13c, 0x13d, 0x13e):
            fcntl.ioctl(fd, xbox_copy.UI_SET_KEYBIT, code)
        fcntl.ioctl(fd, xbox_copy.UI_SET_EVBIT, xbox_copy.EV_ABS)
        for code, lo, hi in ((0, 0, 255), (1, 0, 255), (2, 0, 255), (3, 0, 255), (4, 0, 255), (5, 0, 255),
                             (0x10, -1, 1), (0x11, -1, 1)):
            fcntl.ioctl(fd, xbox_copy.UI_SET_ABSBIT, code)
            fcntl.ioctl(fd, xbox_copy.UI_ABS_SETUP, struct.pack('H2x6i', code, 128 if hi == 255 and code not in (2, 5)
                                                                else 0, lo, hi, 0, 0, 0))
        fcntl.ioctl(fd, xbox_copy.UI_DEV_SETUP, struct.pack('HHHH80sI', 0x06, 0x1234, 0x5678, 1,
                                                            b'Test pad (moonlight-pi-setup tests)', 0))
        fcntl.ioctl(fd, xbox_copy.UI_DEV_CREATE)
        for _ in range(50):
            found = [os.path.basename(os.path.dirname(os.path.dirname(f))) for f in
                     glob.glob('/sys/class/input/event*/device/name')
                     if xbox_copy._read(f) == 'Test pad (moonlight-pi-setup tests)']
            if found and os.access('/dev/input/' + found[0], os.R_OK):
                return fd, '/dev/input/' + found[0]
            time.sleep(0.05)
        self.fail('no event device')

    def send(self, fd, evs):
        os.write(fd, b''.join(xbox_copy.EVENT.pack(0, 0, t, c, v) for t, c, v in evs) +
                 xbox_copy.EVENT.pack(0, 0, 0, 0, 0))

    def test_input_source(self):
        import fcntl
        fd, node = self.make_source_device()
        try:
            self.send(fd, [(1, 0x130, 1)])          # held before the copy reads it
            time.sleep(0.05)
            src = xbox_copy.InputSource(node)
            self.assertEqual(src.state()[0], SOUTH)
            self.assertEqual(src.state()[3], 128)
            # Triangle is BTN_NORTH (0x133) on a PlayStation controller.
            self.send(fd, [(1, 0x133, 1), (3, 0, 0), (3, 1, 255), (3, 2, 255), (3, 0x10, -1)])
            time.sleep(0.05)
            s = src.read()
            self.assertEqual(s, (SOUTH | NORTH, 255, 0, -32768, 32767, 128, 128, -1, 0))
            self.assertIsNone(src.read())
            src.close()
        finally:
            fcntl.ioctl(fd, xbox_copy.UI_DEV_DESTROY)
            os.close(fd)

    def test_copy_is_an_xbox_360_pad(self):
        pad = xbox_copy.XboxPad(3)
        try:
            time.sleep(0.2)
            found = [os.path.dirname(f) for f in glob.glob('/sys/class/input/input*/phys')
                     if xbox_copy._read(f) == 'moonlight-xbox-copy/3']
            self.assertEqual(len(found), 1)
            self.assertEqual(xbox_copy._read(found[0] + '/id/vendor'), '045e')
            self.assertEqual(xbox_copy._read(found[0] + '/id/product'), '028e')
            # Left out of the controllers that get copies.
            events = {'/dev/input/' + os.path.basename(e) for e in glob.glob(found[0] + '/event*')}
            self.assertFalse(any(c['event'] in events for c in xbox_copy.input_controllers()))
            pad.write((SOUTH,) + NEUTRAL[1:])
        finally:
            pad.close()


if __name__ == '__main__':
    unittest.main()
