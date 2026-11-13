"""Controllers > Sign-in screens: an Xbox copy of each controller on the PC's
lock screen, sign-in screen and UAC prompts (see xbox_copy.py)."""
import time

import pygame

import ui
import xbox_copy
from settings_screens import SettingsListScreen, describe
from ui import PANEL_HI, TEXT, DIM, GOOD, WARN, Item, draw_wrapped, dot

DESCRIPTION = ("Windows' lock screen, sign-in screen and UAC prompts only work with Xbox controllers. While "
               "your PC shows one during a stream, every other controller (PlayStation, Switch and others) also "
               "shows up on it as an Xbox controller, until the screen goes away. Games keep seeing your "
               "controller as it is.")
PC_STEP = ('Answer Yes to sign-in screens', 'It installs a small Windows service (Windows asks for permission '
                                            'once) that tells the Pi when one of those screens is up.')


class XboxCopyScreen(SettingsListScreen):
    title = 'Sign-in screens'

    def __init__(self, app):
        super().__init__(app)
        self.info = None            # (service status, controllers), read every 2 seconds
        self.reading = False
        self.last_read = 0

    def tick(self):
        if not self.reading and time.monotonic() - self.last_read > 2:
            self.reading = True
            self.last_read = time.monotonic()
            self.app.run_task(lambda: (xbox_copy.load_status(), xbox_copy.describe()), self._read)
        return False

    def _read(self, res, err):
        self.reading = False
        if res and res != self.info:
            self.info = res
            self.app.dirty = True

    def on(self):
        return (self.status or {}).get('xbox_copy') == '1'

    def refresh(self):
        st = self.status
        if st is None:
            self.list.set_items([Item('Loading…', selectable=False, spinner=True)])
            return
        if self.error:
            self.list.set_items([Item("Settings aren't available", selectable=False, color=WARN, desc=self.error)])
            return
        from usb_screens import PcSetupScreen
        self.list.set_items([
            Item('Xbox controller on sign-in screens',
                 lambda: self.toggle('xbox-copy', 'xbox_copy', 'Xbox controller on sign-in screens'),
                 key='on', toggle=self.on(), desc=DESCRIPTION),
            Item('Set up your PC', lambda: self.app.push(PcSetupScreen(self.app, PC_STEP)), key='pc',
                 desc='The command to run on your gaming PC, so it tells the Pi when one of those screens is up.'),
        ])

    def lines(self):
        """(colour, text): whether a PC is set up, and what each controller gets."""
        if not self.on():
            return [(DIM, 'Off: controllers that aren\'t Xbox controllers can\'t use those screens.')]
        if not self.info:
            return []
        status, ctrls = self.info
        out = []
        if not status:
            out.append((WARN, 'Starting…'))
        # A PC checks in every 5 seconds, from each of its addresses (home, Tailscale).
        now = time.time()
        pcs = sorted({a['host'] or a['address'] for a in status.get('agents', []) if now - a['seen'] < 60})
        out += [(GOOD, '%s is set up' % pc) for pc in pcs]
        if status and not pcs:
            out.append((WARN, 'No PC has checked in. Set up your PC, and check it is switched on.'))
        for name, gets, why in ctrls[:4]:
            out.append((GOOD if gets and 'only if' not in why else WARN if not why.startswith('Xbox') else DIM,
                        '%s: %s' % (name, why)))
        if not ctrls:
            out.append((DIM, 'No controllers connected'))
        return out

    def draw_panel(self, surf, rect):
        it = self.list.selected
        if not it:
            return
        y = describe(surf, rect, it, 28)
        if self.status is None or self.error:
            return
        pygame.draw.line(surf, PANEL_HI, (rect.x, y), (rect.right, y), 2)
        y += 22
        for color, text in self.lines():
            dot(surf, color, (rect.x + 10, y + ui.font(28).get_linesize() // 2 + 2), 8)
            y = draw_wrapped(surf, text, (rect.x + 32, y), rect.width - 32, 28, TEXT, line_gap=4) + 12
