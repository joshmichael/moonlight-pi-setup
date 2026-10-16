"""Settings > USB sharing: which USB devices plugged into the Pi go to the PC
while streaming, and how to set the PC up for it."""
import time

import pygame

import settings
import ui
import usb_sharing
from settings_screens import ChoiceScreen, SettingsListScreen, describe
from ui import (W, MARGIN, CONTENT_Y, PANEL_HI, TEXT, DIM, FAINT, GOOD, WARN, Item, ListScreen, Screen, draw_text,
                draw_wrapped, rrect, dot)

DESCRIPTIONS = {
    'sharing': "USB devices plugged into the Pi, like a wired controller, go to your PC while you stream, as if "
               "they were plugged into it, and come back to the Pi afterwards. Your PC needs setting up for this "
               "too.",
    'devices': 'What each device does: goes to your PC, stays on the Pi, or decides by itself (controllers and '
               'headsets go, keyboards and mice stay).',
    'ports': 'Choose ports where anything you plug in goes to your PC, including everything on a hub plugged '
             'into them.',
    'tailscale': "Not recommended. Lets USB devices go to your PC when you're streaming away from home, over "
                 "Tailscale. It adds input lag and stutter, and away from home controllers work better through "
                 "Moonlight.",
    'pc': 'The command to run on your gaming PC, so it can receive USB devices from the Pi.',
    'latency': "How your PC receives a device's data. Low-latency mode hands each small packet over the moment "
               "it arrives, which suits controllers. The difference is small: try it and see.",
}

LATENCY_OPTIONS = [
    ('off', 'Off', "Every device uses usbip-win2's standard mode, which has had the most use.", ''),
    ('input', 'Controllers, keyboards and mice', 'They use low-latency mode: they send small packets often, '
     'which it hands over as they arrive. Other devices use the standard mode.', ''),
    ('all', 'All devices', 'Every device uses low-latency mode. It copies data once more, so it can be slower '
     'for devices that move a lot of data, like USB drives and cameras.', ''),
]

RULE_NAMES = {'always': 'Always', 'never': 'Never', None: 'Automatic'}


def pi_address(app):
    """The Pi's address on the home network (for typing into the PC setup)."""
    st = app.net_status or {}
    for kind in ('ethernet', 'wifi'):
        d = st.get(kind) or {}
        if d.get('connected') and d.get('ip'):
            return d['ip']
    return ''


def pc_label(addr):
    name = settings.pc_name(addr) if addr else ''
    return '%s (%s)' % (name, addr) if name else (addr or 'your PC')


def last_stream_line(last):
    """(colour, text) about the last stream, or None."""
    if not last:
        return None
    when = time.strftime('%H:%M', time.localtime(last.get('time', 0)))
    pc = pc_label(last.get('pc'))
    if last.get('tailscale_blocked'):
        return WARN, 'At %s, over Tailscale: devices stayed on the Pi (USB sharing over Tailscale is off)' % when
    if not last.get('ready'):
        return WARN, "At %s: %s wasn't ready, so devices stayed on the Pi" % (when, pc)
    if last.get('devices'):
        return GOOD, 'At %s: sent %s to %s' % (when, ', '.join(last['devices']), pc)
    return DIM, 'At %s: nothing to send to %s' % (when, pc)


class SnapshotMixin:
    """Reads the devices and ports again every second while the screen is open,
    and notices when something is plugged in."""

    def init_snapshot(self):
        self.snap = None
        self.snapping = False
        self.last_snap = 0
        self.plugged = None

    def tick(self):
        if not self.snapping and time.monotonic() - self.last_snap > 1:
            self.snapping = True
            self.last_snap = time.monotonic()
            self.app.run_task(usb_sharing.snapshot, self._snapped)
        return False

    def _snapped(self, res, err):
        self.snapping = False
        if not res:
            return
        devs = [d for d in res['devices'] if not d['hub']]
        new = [d for d in devs if self.plugged is not None and d['address'] not in self.plugged]
        self.plugged = {d['address'] for d in devs}
        changed = self.snap is None or self._summary(res) != self._summary(self.snap)
        self.snap = res
        if changed:
            self.refresh()
            self.app.dirty = True
        if new:
            self.plugged_in(new[0])

    @staticmethod
    def _summary(snap):
        return ([(d['address'], d.get('goes'), d['name']) for d in snap['devices']],
                [(p['key'], p['rule'], len(p['devices'])) for p in snap['ports']],
                snap['remembered'], snap['status'])

    def plugged_in(self, dev):
        pass


# ---------------------------------------------------------------------------
# Settings > USB sharing
# ---------------------------------------------------------------------------
class UsbSharingScreen(SnapshotMixin, SettingsListScreen):
    title = 'USB sharing'

    def __init__(self, app):
        super().__init__(app)
        self.init_snapshot()
        self.lines = []

    def load(self):
        if not self.loading:
            self.loading = True
            self.app.run_task(lambda: (settings.status(), settings.tailscale_status(), usb_sharing.snapshot()),
                              self._loaded_all)

    def _loaded_all(self, res, err):
        if res:
            self.snap = res[2]
        self._loaded(res[:2] if res else None, err)

    def sharing_on(self):
        return (self.status or {}).get('usb_sharing') == '1'

    def refresh(self):
        app = self.app
        st = self.status
        if st is None:
            self.list.set_items([Item('Loading…', selectable=False, spinner=True)])
            return
        if self.error:
            self.list.set_items([Item("Settings aren't available", selectable=False, color=WARN, desc=self.error)])
            return
        on = self.sharing_on()
        snap = self.snap or {'devices': [], 'ports': [], 'status': {}}
        self.lines = self.status_lines(snap['status'])
        devs = [d for d in snap['devices'] if not d['hub']]
        pc_ports = sum(1 for p in snap['ports'] if p['rule'])
        ts_on = st.get('usb_tailscale') == '1'
        ts_running = (self.ts or {}).get('state') == 'Running'
        self.list.set_items([
            Item('Share USB devices with your PC', lambda: self.toggle('usb-sharing', 'usb_sharing', 'USB sharing'),
                 key='sharing', toggle=on, desc=DESCRIPTIONS['sharing'], disabled=st.get('usbip') != '1',
                 reason="USB/IP isn't installed. Run the setup script again to install it."),
            Item('Devices', lambda: app.push(UsbDevicesScreen(app, on)), key='devices',
                 detail='%d plugged in' % len(devs) if devs else 'None plugged in', desc=DESCRIPTIONS['devices']),
            Item('Ports', lambda: app.push(UsbPortsScreen(app, on)), key='ports',
                 detail=settings.plural(pc_ports, 'PC port') if pc_ports else 'No PC ports',
                 desc=DESCRIPTIONS['ports']),
            # It can always be turned off, but only turned on when it can work.
            Item('Use over Tailscale', self._tailscale, key='tailscale', toggle=ts_on, desc=DESCRIPTIONS['tailscale'],
                 disabled=not ts_on and (not on or not ts_running),
                 reason='Turn on USB sharing first.' if not on else
                        'Sign in to Tailscale first, in Settings > Tailscale.'),
            Item('Low-latency mode', lambda: app.push(LatencyScreen(app, st.get('usb_latency') or 'off', self.load)),
                 key='latency', detail=usb_sharing.LATENCY.get(st.get('usb_latency'), 'Off'),
                 desc=DESCRIPTIONS['latency']),
            Item('Set up your PC', lambda: app.push(PcSetupScreen(app)), key='pc', desc=DESCRIPTIONS['pc']),
        ])

    def _tailscale(self):
        if self.status.get('usb_tailscale') == '1':
            self.switch_setting('usb-tailscale', 'usb_tailscale', False, 'USB sharing over Tailscale')
            return
        self.app.confirm('Use USB sharing over Tailscale?',
                         "This isn't recommended: it adds input lag and stutter, and away from home controllers "
                         "work better through Moonlight.", 'Turn on',
                         lambda: self.switch_setting('usb-tailscale', 'usb_tailscale', True,
                                                     'USB sharing over Tailscale'))

    def draw_panel(self, surf, rect):
        it = self.list.selected
        if not it:
            return
        y = describe(surf, rect, it, 29)
        if self.status is None or self.error:
            return
        pygame.draw.line(surf, PANEL_HI, (rect.x, y), (rect.right, y), 2)
        y += 22
        for color, text in self.lines:
            dot(surf, color, (rect.x + 10, y + 19), 8)
            y = draw_wrapped(surf, text, (rect.x + 32, y), rect.width - 32, 28, TEXT, line_gap=4) + 12
        addr = pi_address(self.app)
        if addr and self.sharing_on():
            draw_wrapped(surf, "This Pi's address: %s" % addr, (rect.x, y + 8), rect.width, 26, FAINT)

    def status_lines(self, status):
        """(colour, text) lines: which PCs are ready, and what happened last time."""
        if not self.sharing_on():
            return [(DIM, 'Off: USB devices stay on the Pi.')]
        if not status:
            return [(WARN, 'USB sharing is starting…')]
        lines = [(GOOD, '%s is ready' % pc_label(a)) for a in status.get('ready', [])]
        if not lines:
            lines = [(WARN, 'No PC is ready. Set up your PC, and check it is switched on.')]
        last = last_stream_line(status.get('last'))
        if last:
            lines.append(last)
        return lines


class LatencyScreen(ChoiceScreen):
    """Which devices the PC receives in usbip-win2's low-latency mode. The Pi
    tells the PC's helper, which uses it the next time it attaches a device."""

    def __init__(self, app, current, on_changed=None):
        super().__init__(app, 'Low-latency mode', 'Which devices should use low-latency mode?',
                         DESCRIPTIONS['latency'] + ' A change applies from the next stream.',
                         LATENCY_OPTIONS, current, self._pick)
        self.on_changed = on_changed

    def _pick(self, value):
        app = self.app
        if value == self.current:
            app.pop()
            return

        def done(res, err):
            if err:
                app.message("Couldn't change that", str(err))
                return
            self.current = value
            self.refresh()
            app.show_toast('Low-latency mode: %s' % usb_sharing.LATENCY[value], GOOD)
            if self.on_changed:
                self.on_changed()
        app.run_task(lambda: settings.change('usb-latency', value), done, busy='Saving…')


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------
def device_result(dev, sharing_on):
    """(colour, text): what happens to a device while streaming."""
    if 'goes' in dev:
        goes, why = dev['goes'], dev['why']
    else:       # not plugged in: only its own setting and its type are known
        rule = dev.get('rule')
        if rule:
            goes, why = rule == 'always', 'you set it to %s' % RULE_NAMES[rule]
        else:
            _, why, goes = usb_sharing.KINDS.get(dev['kind'], usb_sharing.KINDS['other'])
    if not sharing_on:
        return DIM, 'USB sharing is off, so it stays on the Pi.'
    if goes:
        return GOOD, 'Goes to your PC while you stream: %s.' % why
    return DIM, 'Stays on the Pi: %s.' % why


def short_id(ident):
    """The end of a device's ID, to tell two of the same device apart: '91:D6'."""
    return (ident[-5:] if ':' in ident else ident[-6:]).upper() if ident else ''


def item_names(devices, remembered):
    """List labels, telling two devices with the same name apart: by port for
    those plugged in, by the end of their ID for the others."""
    counts = {}
    for d in devices + remembered:
        counts[d['name']] = counts.get(d['name'], 0) + 1
    names = {}
    for d in devices:
        names['dev:' + d['address']] = d['name'] + (' (%s)' % d['label'] if counts[d['name']] > 1 else '')
    for d in remembered:
        names['seen:' + d['key']] = d['name'] + (' (ID %s)' % short_id(d['ident'])
                                                 if counts[d['name']] > 1 and d['ident'] else '')
    return names


class UsbDevicesScreen(SnapshotMixin, ListScreen):
    title = 'USB devices'

    def __init__(self, app, sharing_on):
        super().__init__(app)
        self.sharing_on = sharing_on
        self.init_snapshot()

    def enter(self):
        self.last_snap = 0      # read again now (a setting may have just changed)

    def refresh(self):
        app = self.app
        if self.snap is None:
            self.list.set_items([Item('Loading…', selectable=False, spinner=True)])
            return
        cfg = self.snap['config']
        devs = [d for d in self.snap['devices'] if not d['hub']]
        names = item_names(devs, self.snap['remembered'])
        items = []
        for d in devs:
            detail, color = ('To your PC', GOOD) if d['goes'] and self.sharing_on else ('Stays on Pi', DIM)
            items.append(Item(names['dev:' + d['address']], lambda d=d: app.push(UsbDeviceScreen(app, d, cfg)),
                              key='dev:' + d['address'], detail=detail, detail_color=color))
        if not items:
            items.append(Item('Nothing is plugged in', selectable=False, key='none'))
        if self.snap['remembered']:
            items.append(Item('Not plugged in now', selectable=False, color=FAINT, key='head'))
            for d in self.snap['remembered']:
                items.append(Item(names['seen:' + d['key']], lambda d=d: app.push(UsbDeviceScreen(app, d, cfg)),
                                  key='seen:' + d['key'], detail=RULE_NAMES[d['rule']], detail_color=FAINT))
        self.list.set_items(items)

    def plugged_in(self, dev):
        for i, it in enumerate(self.list.items):
            if it.key == 'dev:' + dev['address']:
                self.list.select(i)
        self.app.show_toast('%s plugged into %s' % (dev['name'], dev['label']))

    def _device(self):
        it = self.list.selected
        if not it or not self.snap:
            return None
        for d in self.snap['devices']:
            if it.key == 'dev:' + d['address']:
                return d
        for d in self.snap['remembered']:
            if it.key == 'seen:' + d['key']:
                return d
        return None

    def draw_panel(self, surf, rect):
        d = self._device()
        if not d:
            draw_wrapped(surf, 'Plug a USB device into the Pi and it appears here.', (rect.x, rect.y), rect.width,
                         30, DIM)
            return
        y = draw_wrapped(surf, d['name'], (rect.x, rect.y), rect.width, 36, TEXT, bold=True) + 16
        rule = (self.snap['config']['devices'].get(d['key']) if 'address' in d else d.get('rule'))
        rows = [('Type', usb_sharing.kind_label(d['kind'])),
                ('Plugged into', d.get('label') or 'Not plugged in now'),
                ('Setting', '%s (%s)' % (RULE_NAMES[rule], 'this one only' if d.get('ident') else
                                         'every one of this model'))]
        if d.get('ident'):
            rows.insert(1, ('Its own ID ends in', short_id(d['ident'])))
        for label, value in rows:
            draw_text(surf, label, (rect.x, y), 26, FAINT, bold=True)
            draw_text(surf, value, (rect.x, y + 32), 30, TEXT, max_width=rect.width)
            y += 84
        color, text = device_result(d, self.sharing_on)
        y = draw_wrapped(surf, text, (rect.x, y + 6), rect.width, 29, color, line_gap=6)
        if color == GOOD:
            mode = usb_sharing.receive_mode(d, self.snap['config'])
            draw_wrapped(surf, 'On your PC it uses %s.' % ('low-latency mode' if mode == 'low-latency' else
                                                           'the standard mode'),
                         (rect.x, y + 8), rect.width, 27, DIM, line_gap=5)


class UsbDeviceScreen(ChoiceScreen):
    """Always, Never or Automatic for one device, and whether its port is a PC port."""

    def __init__(self, app, dev, cfg):
        self.dev = dev
        self.ports = set(cfg['ports'])
        rule = cfg['devices'].get(dev['key'])
        _, why, goes = usb_sharing.KINDS.get(dev['kind'], usb_sharing.KINDS['other'])
        auto = ('Goes to your PC: %s.' if goes else 'Stays on the Pi: %s.') % why
        where = ('plugged into %s' % dev['label']) if dev.get('label') else 'not plugged in now'
        if dev.get('ident'):
            which = ('This is for this one only (its ID ends in %s), in any port: another of the same '
                     'model can be set differently.' % short_id(dev['ident']))
        else:
            which = "This is for every one of this model: it has no ID of its own to tell them apart."
        super().__init__(app, dev['name'], 'Where should it go while you stream?',
                         '%s, %s. %s' % (usb_sharing.kind_label(dev['kind']), where, which),
                         [('auto', 'Automatic', auto + ' A PC port sends it to your PC too.', ''),
                          ('always', 'Always to your PC', 'In any port, whatever type of device it is.', ''),
                          ('never', 'Never to your PC', 'It stays on the Pi, even in a PC port.', '')],
                         rule or 'auto', self._pick)

    def extra_items(self):
        port = self.dev.get('port')
        on = bool(port) and port in self.ports
        label = self.dev.get('label') or 'its port'
        return [Item('Send anything plugged into this port', self._port, key='port', toggle=on,
                     desc='Anything plugged into %s goes to your PC while you stream, not just this device (and '
                          'everything on a hub plugged into it).' % label,
                     disabled=not port, reason='Plug the device in to choose its port.')]

    def _pick(self, value):
        app = self.app
        if value == self.current:
            app.pop()
            return

        def done(res, err):
            if err:
                app.message("Couldn't change that", str(err))
                return
            self.current = value
            self.refresh()
            app.show_toast('%s: %s' % (self.dev['name'], RULE_NAMES[None if value == 'auto' else value]), GOOD)
        app.run_task(lambda: settings.usb_device(self.dev['key'], value), done, busy='Saving…')

    def _port(self):
        app = self.app
        port = self.dev['port']
        on = port not in self.ports

        def done(res, err):
            if err:
                app.message("Couldn't change that", str(err))
                return
            self.ports ^= {port}
            self.refresh()
            app.show_toast('%s: %s' % (self.dev['label'], 'PC port' if on else 'not a PC port'), GOOD)
        app.run_task(lambda: settings.usb_port(port, on), done, busy='Saving…')


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------
class UsbPortsScreen(SnapshotMixin, ListScreen):
    title = 'USB ports'

    def __init__(self, app, sharing_on):
        super().__init__(app)
        self.sharing_on = sharing_on
        self.init_snapshot()

    def refresh(self):
        if self.snap is None:
            self.list.set_items([Item('Loading…', selectable=False, spinner=True)])
            return
        items = []
        for p in self.snap['ports']:
            names = [d['name'] if not d['hub'] else 'USB hub' for d in p['devices']]
            items.append(Item(p['label'], lambda p=p: self._toggle(p), key=p['key'], toggle=p['rule'],
                              detail=', '.join(names) or 'Empty', detail_color=FAINT if not names else None))
        self.list.set_items(items)

    def plugged_in(self, dev):
        for i, it in enumerate(self.list.items):
            if it.key == dev['port']:
                self.list.select(i)
        self.app.show_toast('%s plugged into %s' % (dev['name'], dev['label']))

    def _toggle(self, p):
        app = self.app
        on = not p['rule']

        def done(res, err):
            if err:
                app.message("Couldn't change that", str(err))
                return
            app.show_toast('%s: %s' % (p['label'], 'PC port' if on else 'not a PC port'), GOOD)
            self.last_snap = 0
        app.run_task(lambda: settings.usb_port(p['key'], on), done, busy='Saving…')

    def draw_panel(self, surf, rect):
        it = self.list.selected
        port = next((p for p in (self.snap or {}).get('ports', []) if it and p['key'] == it.key), None)
        y = rect.y
        if port:
            y = draw_wrapped(surf, port['label'], (rect.x, y), rect.width, 36, TEXT, bold=True) + 12
            y = draw_text(surf, 'PC port' if port['rule'] else 'Not a PC port', (rect.x, y), 30,
                          GOOD if port['rule'] else DIM).bottom + 20
            for d in port['devices']:
                if d['hub']:
                    text, color = 'USB hub: its ports are listed below it', TEXT
                else:
                    color, text = device_result(d, self.sharing_on)
                    text = '%s. %s' % (d['name'], text)
                y = draw_wrapped(surf, text, (rect.x, y), rect.width, 28, color, line_gap=4) + 12
            if not port['devices']:
                y = draw_wrapped(surf, 'Nothing is plugged in.', (rect.x, y), rect.width, 28, DIM) + 12
            pygame.draw.line(surf, PANEL_HI, (rect.x, y + 8), (rect.right, y + 8), 2)
            y += 30
        y = draw_wrapped(surf, 'Anything plugged into a PC port goes to your PC while you stream, including '
                         'everything on a hub plugged into it, unless it\'s set to Never.', (rect.x, y), rect.width,
                         27, DIM, line_gap=5) + 14
        draw_wrapped(surf, 'Plug something in to see which port it is.', (rect.x, y), rect.width, 27, FAINT)


# ---------------------------------------------------------------------------
# Set up your PC
# ---------------------------------------------------------------------------
def wrap_anywhere(text, size, width):
    """Word-wrapped lines, also breaking words too long for a line (like a web
    address), after a '/' where possible."""
    f = ui.font(size)
    lines = []
    for line in ui.wrap(f, text, width):
        while f.size(line)[0] > width:
            cut = len(line)
            while cut > 1 and f.size(line[:cut])[0] > width:
                cut -= 1
            slash = line.rfind('/', 0, cut)
            if slash > cut // 2:
                cut = slash + 1
            lines.append(line[:cut])
            line = line[cut:]
        lines.append(line)
    return lines


def draw_pc_command(surf, x, y, width, app):
    """The PC setup command and this Pi's address. Returns the y below them."""
    lines = wrap_anywhere(settings.PC_SETUP_COMMAND, 26, width - 48)
    box_h = 20 + len(lines) * 36 + 16
    rrect(surf, PANEL_HI, pygame.Rect(x, y, width, box_h), 14)
    for i, line in enumerate(lines):
        draw_text(surf, line, (x + 24, y + 20 + i * 36), 26, TEXT)
    y += box_h + 16
    addr = pi_address(app)
    if addr:
        y = draw_wrapped(surf, "If it asks, this Pi's address is %s." % addr, (x, y), width, 28, DIM) + 8
    return y


class PcSetupScreen(Screen):
    title = 'Set up your PC'

    def __init__(self, app):
        super().__init__(app)
        self.qr = ui.qr_surface(settings.PC_SETUP_URL, 420)

    def handle(self, action):
        if action in ui.BACK or action in ('select', 'enter'):
            self.app.pop()

    def hints(self):
        return [('B', 'Back')]

    def draw(self, surf):
        left = pygame.Rect(MARGIN, CONTENT_Y, 480, 480)
        right_x = MARGIN + 560
        width = W - MARGIN - right_x
        rrect(surf, ui.WHITE, left, 24)
        if self.qr:
            surf.blit(self.qr, self.qr.get_rect(center=left.center))
        draw_wrapped(surf, 'Scan for the instructions', (left.x, left.bottom + 20), left.width, 28, DIM)
        y = ui.numbered_steps(surf, right_x, CONTENT_Y, width, [
            ('On your gaming PC, open PowerShell', 'Search for PowerShell in the Start menu. Your normal account is '
                                                   'fine.'),
            ('Paste this command and press Enter', '')])
        y = draw_pc_command(surf, right_x + 84, y - 56, width - 84, self.app) + 20
        ui.numbered_steps(surf, right_x, y, width, [
            ('Answer Yes to sharing USB devices', 'It installs usbip-win2 (Windows asks for permission once) and '
                                                  'finds this Pi by itself. Restart the PC when it asks.')], first=3)
