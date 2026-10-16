"""Settings, Quick setup, Tailscale, Updates, About and Uninstall screens for the menu."""
import collections
import getpass
import re
import threading
import time

import pygame

import settings
import ui
from ui import (W, H, MARGIN, CONTENT_Y, FOOTER_Y, BACK, PANEL, PANEL_HI, ACCENT, TEXT, DIM, FAINT, GOOD, WARN,
                BAD, Item, ListScreen, Screen, draw_text, draw_wrapped, rrect, dot)

REPO = 'github.com/joshmichael/moonlight-pi-setup'
ANSI_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')

SPEAKER_OPTIONS = [
    ('stereo', 'Stereo', 'TV speakers, a soundbar or headphones. Choose this if you\'re not sure.', ''),
    ('5.1', '5.1 surround', 'Six speakers, through a receiver or a surround soundbar. If the Pi is plugged into '
     'the TV, the TV needs eARC to pass surround sound on.', ''),
    ('7.1', '7.1 surround', 'Eight speakers. This is experimental: if a speaker plays the wrong channel, please '
     'report it on GitHub.', 'Experimental'),
]

DESCRIPTIONS = {
    'speakers': 'Stereo, 5.1 or 7.1 surround, and a speaker test. Moonlight\'s own audio setting is changed '
                'to match.',
    'quit': 'When the Pi is switched off, for example with its power button, it tells your PC to end the stream, '
            'so the PC isn\'t left stuck on it. The game you\'re streaming closes, so save first.',
    'usb': 'Send USB devices plugged into the Pi, like a wired controller, to your PC while you stream, as if '
           'they were plugged into it. Choose which devices and ports, and set up your PC.',
    'tailscale': 'Stream when you\'re away from home: see whether Tailscale is connected, sign in with your '
                 'phone, or turn it off.',
    'powersave': 'Power saving can make Wi-Fi doze between packets, which can cause stutter and lag spikes when '
                 'streaming. Keeping this off is recommended.',
    'country': 'Wi-Fi uses different channels in each country. The Pi needs to know where it is to find every '
               'network.',
    'quiet': 'Hide the text and the splash screen shown while the Pi starts. Takes effect after a restart.',
    'updates': 'Update Moonlight and the system, or this setup.',
    'about': 'Network addresses, versions, temperature and power.',
    'quick': 'Go through the first-time questions again: speakers, ending the stream at shutdown, and more.',
    'uninstall': 'Remove this setup: the TV menu, its settings and auto-start. You can also remove Moonlight '
                 'and Tailscale. The Pi then restarts the way it started before the setup.',
}


def usb_screens():
    """The USB sharing screens (imported when first needed: they use this module)."""
    import usb_screens as module
    return module


def on_off(v):
    return 'on' if v else 'off'


def describe(surf, rect, it, size=30):
    """The highlighted item's name and description, at the top of the panel.
    Returns the y below them."""
    y = draw_wrapped(surf, it.label, (rect.x, rect.y), rect.width, 36, TEXT, bold=True) + 12
    if it.desc:
        y = draw_wrapped(surf, it.desc, (rect.x, y), rect.width, size, DIM, line_gap=6) + 20
    if it.disabled and it.reason:
        y = draw_wrapped(surf, it.reason, (rect.x, y), rect.width, size, WARN, line_gap=6) + 20
    return y


def play_speaker_test(app, mode):
    if mode not in settings.SPEAKER_CHANNELS:
        mode = 'stereo'

    def wrong(i):
        if i == 1:
            app.message('Speakers in the wrong place',
                        'Check each speaker is plugged into the right output on your receiver or TV. If they are, '
                        'please report it, with your TV and receiver models, at %s/issues' % REPO)

    def done(res, err):
        if err:
            msg = str(err)
            if mode != 'stereo':
                msg += (' Your TV may only accept stereo from the Pi. For surround sound through a TV to a '
                        'receiver or soundbar, the TV needs eARC, or plug the Pi into the receiver.')
            app.message("The speaker test didn't play", msg)
            return
        app.choose('Did each speaker say the right place?',
                   '"Front left" should come from the front left speaker, and so on.', ['Yes', 'No'], wrong)
    app.run_task(lambda: settings.speaker_test(mode), done, busy='Speaker test: each speaker says where it is…')


# ---------------------------------------------------------------------------
# A list of choices, with the question and what each choice means in the panel
# ---------------------------------------------------------------------------
class ChoiceScreen(ListScreen):
    """options: [(value, label, description, tag)]. The current value is
    marked "Current" (or, with show_current=False, each option shows its tag)."""

    def __init__(self, app, title, heading, intro, options, current=None, on_pick=None,
                 show_current=True, initial=None, step=''):
        super().__init__(app)
        self.title = title
        self.heading = heading
        self.intro = intro
        self.options = options
        self.current = current
        self.on_pick = on_pick
        self.show_current = show_current
        self.initial = initial if initial is not None else current
        self.step = step
        self.placed = False

    def refresh(self):
        items = []
        for value, label, desc, tag in self.options:
            detail, color = tag, FAINT
            if self.show_current and value == self.current:
                detail, color = 'Current', GOOD
            items.append(Item(label, lambda v=value: self.on_pick(v), key=value, detail=detail,
                              detail_color=color, desc=desc))
        items += self.extra_items()
        self.list.set_items(items)
        if not self.placed:
            self.placed = True
            for i, it in enumerate(items):
                if it.key == self.initial:
                    self.list.select(i)

    def extra_items(self):
        return []

    def long(self):
        return len(self.list.items) > self.list.visible_count()

    def handle(self, action):
        # Long lists (countries) page up and down with left/right or the shoulder buttons.
        if self.long() and action in ('left', 'lb', 'right', 'rb'):
            self.list.page(-1 if action in ('left', 'lb') else 1)
        else:
            super().handle(action)

    def hints(self):
        if self.long():
            return [('A', 'Select'), (('LB', 'RB'), 'Page'), ('B', 'Back')]
        return super().hints()

    def draw_panel(self, surf, rect):
        y = rect.y
        if self.step:
            y = draw_text(surf, self.step, (rect.x, y), 26, FAINT, bold=True).bottom + 14
        if self.heading:
            y = draw_wrapped(surf, self.heading, (rect.x, y), rect.width, 36, TEXT, bold=True, line_gap=6) + 12
        if self.intro:
            y = draw_wrapped(surf, self.intro, (rect.x, y), rect.width, 29, DIM, line_gap=6) + 22
        it = self.list.selected
        if it and it.desc:
            pygame.draw.line(surf, PANEL_HI, (rect.x, y), (rect.right, y), 2)
            y += 22
            y = draw_text(surf, it.label, (rect.x, y), 29, ACCENT, bold=True, max_width=rect.width).bottom + 8
            y = draw_wrapped(surf, it.desc, (rect.x, y), rect.width, 29, TEXT, line_gap=6) + 10
            if it.disabled and it.reason:
                y = draw_wrapped(surf, it.reason, (rect.x, y + 6), rect.width, 29, WARN, line_gap=6) + 10
        self.draw_more(surf, rect, y)

    def draw_more(self, surf, rect, y):
        pass


class SpeakersScreen(ChoiceScreen):
    def __init__(self, app, current, on_changed=None):
        super().__init__(app, 'Speakers', 'Which speakers do you use?',
                         'This sets the sound for streaming. Moonlight\'s own audio setting is changed to match.',
                         SPEAKER_OPTIONS, current, self._pick)
        self.on_changed = on_changed

    def extra_items(self):
        return [Item('Play a speaker test', lambda: play_speaker_test(self.app, self.current), key='test',
                     desc='Each speaker says where it is, for example "Front left". Make sure the TV (and '
                          'receiver, if you have one) is on.')]

    def _pick(self, mode):
        app = self.app
        if mode == self.current:
            app.show_toast('Already set to %s' % settings.SPEAKER_NAMES[mode])
            return

        def done(res, err):
            if err:
                app.message("Couldn't change the speakers", str(err))
                return
            self.current = mode
            self.refresh()
            app.show_toast('Speakers: %s' % settings.SPEAKER_NAMES[mode], GOOD)
            if self.on_changed:
                self.on_changed()
        app.run_task(lambda: settings.set_speakers(mode), done, busy='Changing the speakers…')


class CountryScreen(ChoiceScreen):
    def __init__(self, app, current, on_changed=None):
        super().__init__(app, 'Wi-Fi country', 'Where is the Pi?', DESCRIPTIONS['country'],
                         [(code, name, '', code) for code, name in settings.countries()], current, self._pick,
                         initial=current or settings.guess_country())
        self.on_changed = on_changed

    def hints(self):
        return [('A', 'Choose'), (('LB', 'RB'), 'Page'), ('B', 'Back')]

    def _pick(self, code):
        app = self.app
        if code == self.current:
            app.pop()
            return

        def done(res, err):
            if err:
                app.message("Couldn't set the Wi-Fi country", str(err))
                return
            app.show_toast('Wi-Fi country: %s' % settings.country_name(code), GOOD)
            if app.top is self:
                app.pop()
            if self.on_changed:
                self.on_changed()
        app.run_task(lambda: settings.change('wifi-country', code), done, busy='Setting the Wi-Fi country…')

    def draw_more(self, surf, rect, y):
        if self.current:
            draw_text(surf, 'Now: %s' % settings.country_name(self.current), (rect.x, y), 30, GOOD,
                      max_width=rect.width)
        else:
            draw_wrapped(surf, 'Wi-Fi stays off until you choose one.', (rect.x, y), rect.width, 30, WARN)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def tailscale_summary(ts):
    """(text, colour) for Tailscale's state."""
    if not ts or not ts.get('installed'):
        return 'Not installed', None
    return {
        'Running': ('Connected', GOOD),
        'Stopped': ('Off', None),
        'NeedsLogin': ('Not signed in', WARN),
        'NoState': ('Not signed in', WARN),
        'NeedsMachineAuth': ('Waiting for approval', WARN),
        'Starting': ('Starting…', None),
    }.get(ts.get('state'), ('Not running', WARN))


class SettingsListScreen(ListScreen):
    """A list of settings, read from the helper (and Tailscale) when it opens."""

    def __init__(self, app):
        super().__init__(app)
        self.status = None
        self.ts = None
        self.error = ''
        self.loading = False

    def enter(self):
        self.load()

    def load(self):
        if not self.loading:
            self.loading = True
            self.app.run_task(lambda: (settings.status(), settings.tailscale_status()), self._loaded)

    def _loaded(self, res, err):
        self.loading = False
        self.error = str(err) if err else ''
        self.status, self.ts = res or ({}, {})
        self.loaded()
        self.refresh()

    def loaded(self):
        """Called when the settings have been read, before refresh()."""

    def switch_setting(self, name, key, on, label):
        """Turns a setting on or off, then reads the settings again."""
        app = self.app

        def done(res, err):
            if err:
                app.message("Couldn't change that", str(err))
            else:
                self.status[key] = '1' if on else '0'
                note = ' (after a restart)' if name == 'quiet-boot' else ''
                app.show_toast('%s: %s%s' % (label, 'On' if on else 'Off', note), GOOD)
            self.load()
        app.run_task(lambda: settings.change(name, on_off(on)), done,
                     busy='Turning on…' if on else 'Turning off…')

    def toggle(self, name, key, label):
        self.switch_setting(name, key, self.status.get(key) != '1', label)


class SettingsScreen(SettingsListScreen):
    title = 'Settings'

    def loaded(self):
        if not self.error and self.app.quiet_boot_at_start is None:
            self.app.quiet_boot_at_start = self.status.get('quiet_boot')

    def restart_needed(self):
        st = self.status or {}
        start = self.app.quiet_boot_at_start
        return start is not None and st.get('quiet_boot') not in (None, start)

    def refresh(self):
        app = self.app
        if self.status is None:
            self.list.set_items([Item('Loading…', selectable=False, spinner=True)])
            return
        about = Item('About this Pi', lambda: app.push(AboutScreen(app)), key='about', desc=DESCRIPTIONS['about'])
        if self.error:
            self.list.set_items([Item("Settings aren't available", selectable=False, color=WARN, desc=self.error),
                                 about])
            return
        st = self.status
        ts_text, ts_color = tailscale_summary(self.ts)
        items = [
            Item('Speakers', lambda: app.push(SpeakersScreen(app, st.get('speakers'), self.load)), key='speakers',
                 detail=settings.SPEAKER_NAMES.get(st.get('speakers'), 'Not set'), desc=DESCRIPTIONS['speakers']),
            Item('End stream on PC at shutdown',
                 lambda: self.toggle('quit-on-shutdown', 'quit_on_shutdown', 'End stream at shutdown'),
                 key='quit', toggle=st.get('quit_on_shutdown') == '1', desc=DESCRIPTIONS['quit']),
        ]
        usb_on = st.get('usb_sharing') == '1'
        items.append(Item('USB sharing', lambda: app.push(usb_screens().UsbSharingScreen(app)), key='usb',
                          detail='On' if usb_on else 'Off', detail_color=GOOD if usb_on else None,
                          desc=DESCRIPTIONS['usb']))
        items.append(Item('Tailscale', lambda: app.push(TailscaleScreen(app)), key='tailscale', detail=ts_text,
                          detail_color=ts_color, desc=DESCRIPTIONS['tailscale']))
        wifi = st.get('wifi') == '1'
        code = st.get('wifi_country', '')
        items += [
            Item('Wi-Fi power saving', lambda: self.toggle('wifi-powersave', 'wifi_powersave', 'Wi-Fi power saving'),
                 key='powersave', toggle=wifi and st.get('wifi_powersave') == '1', desc=DESCRIPTIONS['powersave'],
                 disabled=not wifi, reason='This Pi has no Wi-Fi.'),
            Item('Wi-Fi country', lambda: app.push(CountryScreen(app, code, self.load)), key='country',
                 detail=(settings.country_name(code) if code else 'Not set') if wifi else '',
                 detail_color=None if code else WARN, desc=DESCRIPTIONS['country'],
                 disabled=not wifi, reason='This Pi has no Wi-Fi.'),
        ]
        version = settings.setup_version()
        items += [
            Item('Hide boot messages', lambda: self.toggle('quiet-boot', 'quiet_boot', 'Hide boot messages'),
                 key='quiet', toggle=st.get('quiet_boot') == '1',
                 detail='Restart needed' if self.restart_needed() else '', detail_color=WARN,
                 desc=DESCRIPTIONS['quiet']),
            Item('Updates', lambda: app.push(UpdatesScreen(app)), key='updates',
                 detail='Version %s' % version if version else '', desc=DESCRIPTIONS['updates']),
            about,
            Item('Run Quick setup again', lambda: QuickSetup(app).start(), key='quick', desc=DESCRIPTIONS['quick']),
            Item('Uninstall this setup', lambda: app.push(UninstallScreen(app, st)), key='uninstall', color=BAD,
                 desc=DESCRIPTIONS['uninstall']),
        ]
        self.list.set_items(items)

    def handle(self, action):
        if action in BACK and self.restart_needed() and not self.app.restart_prompted:
            self.app.restart_prompted = True
            self.app.choose('Restart now?', 'Hiding or showing boot messages takes effect after a restart.',
                            ['Restart now', 'Later'],
                            lambda i: self.app.power('reboot') if i == 0 else self.app.pop())
            return
        super().handle(action)

    def draw_panel(self, surf, rect):
        it = self.list.selected
        if not it:
            return
        y = describe(surf, rect, it)
        if it.key == 'tailscale' and self.ts and self.ts.get('ip'):
            draw_text(surf, 'Address: %s' % self.ts['ip'], (rect.x, y), 30, TEXT, max_width=rect.width)
        elif it.key == 'quiet' and self.restart_needed():
            draw_wrapped(surf, 'Restart the Pi to apply this change.', (rect.x, y), rect.width, 30, WARN)


# ---------------------------------------------------------------------------
# Tailscale
# ---------------------------------------------------------------------------
class TailscaleScreen(ListScreen):
    title = 'Tailscale'

    def __init__(self, app):
        super().__init__(app)
        self.ts = None
        self.hosts = []
        self.loading = False
        self.last = 0

    def enter(self):
        self.load()

    def load(self):
        if self.loading:
            return
        self.loading = True
        self.last = time.monotonic()
        self.app.run_task(lambda: (settings.tailscale_status(), settings.moonlight_hosts()), self._loaded)

    def _loaded(self, res, err):
        self.loading = False
        self.ts, self.hosts = res or ({'installed': False}, [])
        self.refresh()

    def tick(self):
        if time.monotonic() - self.last > 3:
            self.load()
        return False

    def refresh(self):
        ts = self.ts
        if ts is None:
            self.list.set_items([Item('Loading…', selectable=False, spinner=True)])
            return
        if not ts.get('installed'):
            self.list.set_items([Item('Install Tailscale', self._install, key='install')])
            return
        state = ts.get('state')
        if state == 'Running':
            items = [Item('Turn off', self._down, key='onoff')]
        elif state == 'Stopped':
            items = [Item('Turn on', self._sign_in, key='onoff')]
        elif state in ('NeedsLogin', 'NoState'):
            items = [Item('Sign in', self._sign_in, key='signin')]
        elif state == 'NeedsMachineAuth':
            items = [Item('Waiting for approval', selectable=False)]
        else:
            items = [Item('Tailscale is starting…', selectable=False, spinner=True)]
        if state in ('Running', 'Stopped', 'NeedsMachineAuth'):
            items.append(Item('Sign out', self._logout, key='logout', color=BAD))
        self.list.set_items(items)

    def _install(self):
        app = self.app
        app.confirm('Install Tailscale?', "It's downloaded from tailscale.com and takes a minute or two.", 'Install',
                    lambda: app.push(ProgressScreen(app, 'Installing Tailscale', ['tailscale', 'install'],
                                                    self._installed)))

    def _installed(self, ok, err):
        if not ok:
            self.app.message("Tailscale wasn't installed", err)
            return
        self.load()
        self.app.push(TailscaleLoginScreen(self.app))

    def _sign_in(self):
        self.app.push(TailscaleLoginScreen(self.app))

    def _down(self):
        app = self.app
        app.run_task(lambda: settings.tailscale('down'),
                     lambda r, e: (app.message("Couldn't turn Tailscale off", str(e)) if e
                                   else app.show_toast('Tailscale is off'), self.load()),
                     busy='Turning Tailscale off…')

    def _logout(self):
        app = self.app
        app.confirm('Sign out of Tailscale?', "You'll need to sign in again to stream away from home.", 'Sign out',
                    lambda: app.run_task(lambda: settings.tailscale('logout'),
                                         lambda r, e: (e and app.message("Couldn't sign out", str(e)), self.load()),
                                         busy='Signing out…'), danger=True)

    def draw_panel(self, surf, rect):
        ts = self.ts or {}
        y = rect.y
        text, color = tailscale_summary(ts)
        dot(surf, color or DIM, (rect.x + 10, y + 21), 9)
        y = draw_text(surf, text, (rect.x + 36, y), 34, TEXT, bold=True).bottom + 20
        running = ts.get('state') == 'Running'
        for label, value in (('Address', ts.get('ip')), ('Account', ts.get('tailnet'))):
            if value and running:
                draw_text(surf, label, (rect.x, y), 26, FAINT, bold=True)
                draw_text(surf, value, (rect.x, y + 32), 30, TEXT, max_width=rect.width)
                y += 84
        if running:
            # The Pi gives Moonlight each PC's Tailscale address (settings.moonlight_tailscale_sync).
            y = draw_text(surf, 'Your PC in Moonlight', (rect.x, y), 26, FAINT, bold=True).bottom + 10
            if not self.hosts:
                y = draw_wrapped(surf, 'Pair your PC in Moonlight first.', (rect.x, y), rect.width, 28, DIM) + 14
            missing = False
            for h in self.hosts[:3]:
                ready = settings.is_tailscale_ip(h.get('manualaddress', ''))
                missing = missing or not ready
                dot(surf, GOOD if ready else WARN, (rect.x + 10, y + 19), 8)
                draw_text(surf, '%s: %s' % (h.get('hostname') or 'PC', 'ready away from home' if ready
                                            else 'not found on Tailscale yet'),
                          (rect.x + 32, y), 28, TEXT, max_width=rect.width - 32)
                y += 44
            if missing:
                y = draw_wrapped(surf, 'Switch the PC on, signed in to Tailscale, and the Pi adds it by itself.',
                                 (rect.x, y), rect.width, 26, FAINT, line_gap=4) + 6
            y += 16
        else:
            y = draw_wrapped(surf, "Tailscale lets you stream when you're away from home. Install it on your gaming "
                             'PC too, signed in to the same account.', (rect.x, y), rect.width, 28, DIM,
                             line_gap=6) + 14
        y = draw_wrapped(surf, "Away from home, lower Moonlight's bitrate.", (rect.x, y), rect.width, 28, DIM,
                         line_gap=6) + 14
        draw_wrapped(surf, 'USB sharing only works at home, unless it\'s allowed in Settings > USB sharing (not '
                     'recommended).', (rect.x, y), rect.width, 28, FAINT)


class TailscaleLoginScreen(Screen):
    """Signs in to Tailscale (showing a code to scan if needed). Once connected
    it closes itself and calls on_done()."""
    title = 'Sign in to Tailscale'

    def __init__(self, app, on_done=None):
        super().__init__(app)
        self.on_done = on_done
        self.state = 'starting'      # starting, url, done, error
        self.url = None
        self.qr = None
        self.error = ''
        self.started = False
        self.polling = False
        self.last_poll = 0
        self.done_at = None

    @property
    def animating(self):
        return self.state == 'starting'

    def enter(self):
        if not self.started:
            self._start()

    def _start(self):
        self.started = True
        self.state, self.error, self.url, self.qr = 'starting', '', None, None
        app = self.app

        def on_line(line):
            url = settings.find_url(line)
            if url:
                app.call_soon(lambda: self._got_url(url))

        def work():
            ok, err = settings.stream(['tailscale', 'up'], on_line)
            app.call_soon(lambda: self._finished(ok, err))
        threading.Thread(target=work, daemon=True).start()

    def _got_url(self, url):
        if self.state in ('starting', 'url') and url != self.url:
            self.url = url
            self.qr = ui.qr_surface(url, 520)
            self.state = 'url'

    def _finished(self, ok, err):
        if self.state == 'done':
            return
        if ok:
            self._success()
        else:
            self.state, self.error = 'error', err or "Couldn't connect to Tailscale."

    def _success(self):
        if self.state != 'done':
            self.state = 'done'
            self.done_at = time.monotonic()
            self.app.dirty = True

    def tick(self):
        now = time.monotonic()
        if self.state == 'done':
            if now - self.done_at > 2.5 and self.app.top is self:
                self.app.pop()
                self.app.show_toast('Tailscale is connected', GOOD)
                self.app.sync_moonlight_tailscale(wait=10)
                if self.on_done:
                    self.on_done()
            return False
        # Signed in on the phone: Tailscale's own status may say so before `tailscale up` returns.
        if self.state in ('starting', 'url') and not self.polling and now - self.last_poll > 2:
            self.polling = True
            self.last_poll = now
            self.app.run_task(settings.tailscale_status, self._polled)
        return False

    def _polled(self, res, err):
        self.polling = False
        if res and res.get('state') == 'Running':
            self._success()

    def handle(self, action):
        if action in BACK:
            # Tailscale keeps waiting for the sign-in in the background (for up to
            # 10 minutes), which does no harm.
            self.app.pop()
        elif action in ('select', 'enter') and self.state == 'error':
            self._start()

    def hints(self):
        if self.state == 'error':
            return [('A', 'Try again'), ('B', 'Back')]
        return [('B', 'Back')]

    def draw(self, surf):
        left = pygame.Rect(MARGIN, CONTENT_Y, 600, 600)
        right_x = MARGIN + 680
        width = W - MARGIN - right_x
        if self.state == 'error':
            draw_text(surf, "Couldn't connect to Tailscale", (right_x, CONTENT_Y), 44, TEXT, bold=True)
            draw_wrapped(surf, self.error, (right_x, CONTENT_Y + 80), width, 32, BAD)
        elif self.state == 'done':
            ui.tick_mark(surf, left.center)
            draw_text(surf, 'Tailscale is connected', (right_x, CONTENT_Y), 44, TEXT, bold=True)
            draw_wrapped(surf, 'You can stream from away from home now.', (right_x, CONTENT_Y + 80), width, 32)
        elif self.state == 'starting':
            ui.spinner(surf, left.center, 50, ACCENT, 10)
            draw_text(surf, 'Connecting to Tailscale…', (right_x, CONTENT_Y), 44, TEXT, bold=True)
            draw_wrapped(surf, 'If the Pi needs signing in, a code to scan appears here.', (right_x, CONTENT_Y + 80),
                         width, 32)
        else:
            rrect(surf, ui.WHITE, left, 24)
            if self.qr:
                surf.blit(self.qr, self.qr.get_rect(center=left.center))
            y = ui.numbered_steps(surf, right_x, CONTENT_Y, width, [
                ('Scan the code with your phone', 'Or open the link below on any phone or computer.'),
                ('Sign in to Tailscale', 'Use the same account as on your gaming PC. If you don\'t have one, '
                                         'you can create one for free.'),
                ('This screen updates by itself', 'when the Pi is signed in.')])
            draw_wrapped(surf, self.url or '', (right_x + 84, y + 10), width - 84, 28, FAINT)


# ---------------------------------------------------------------------------
# Long-running jobs (updates, installing Tailscale, uninstalling)
# ---------------------------------------------------------------------------
class ProgressScreen(Screen):
    """Runs a helper command, showing its progress. Then pops itself and calls
    on_done(ok, error)."""

    def __init__(self, app, title, args, on_done):
        super().__init__(app)
        self.title = title
        self.args = args
        self.on_done = on_done
        self.lines = collections.deque(maxlen=6)
        self.step = 'Starting…'
        self.percent = None
        self.running = False
        self.started = False

    @property
    def animating(self):
        return self.running

    def enter(self):
        if self.started:
            return
        self.started = self.running = True
        app = self.app

        def work():
            ok, err = settings.stream(self.args, lambda line: app.call_soon(lambda: self._line(line)))
            app.call_soon(lambda: self._finish(ok, err))
        threading.Thread(target=work, daemon=True).start()

    def _line(self, line):
        prog = settings.apt_progress(line)
        if prog:
            self.percent, self.step = prog
            return
        line = ANSI_RE.sub('', line).strip()
        if not line or line.startswith('{') or re.match(r'[A-Z_]+=', line):    # JSON, or KEY=value for the menu
            return
        if line.startswith('==> '):
            self.step = line[4:]
            self.percent = None
        self.lines.append(line)

    def _finish(self, ok, err):
        self.running = False
        if self.app.top is self:
            self.app.pop()
        self.on_done(ok, err)

    def handle(self, action):
        if action in BACK or action in ('select', 'enter'):
            self.app.show_toast('Please wait until this finishes')

    def hints(self):
        return []

    def draw(self, surf):
        x = MARGIN
        ui.spinner(surf, (x + 40, CONTENT_Y + 40), 30, ACCENT, 7)
        y = draw_wrapped(surf, self.step, (x + 110, CONTENT_Y + 10), W - 2 * MARGIN - 110, 40, TEXT, bold=True)
        y = max(y, CONTENT_Y + 100) + 20
        if self.percent is not None:
            bar = pygame.Rect(x, y, W - 2 * MARGIN, 26)
            rrect(surf, PANEL_HI, bar, 13)
            fill = bar.copy()
            fill.width = max(26, int(bar.width * min(100, self.percent) / 100))
            rrect(surf, ACCENT, fill, 13)
            draw_text(surf, '%d%%' % self.percent, (bar.right, bar.bottom + 12), 28, DIM, anchor='topright')
            y = bar.bottom + 60
        draw_wrapped(surf, "Don't switch the Pi off until this finishes.", (x, y), W - 2 * MARGIN, 30, WARN)
        y += 80
        box = pygame.Rect(x, y, W - 2 * MARGIN, FOOTER_Y - 50 - y)
        rrect(surf, PANEL, box, 18)
        yy = box.y + 22
        for line in self.lines:
            draw_text(surf, line, (box.x + 28, yy), 26, FAINT, max_width=box.width - 56)
            yy += 40


# ---------------------------------------------------------------------------
# Updates
# ---------------------------------------------------------------------------
class UpdatesScreen(ListScreen):
    title = 'Updates'

    def __init__(self, app):
        super().__init__(app)
        self.latest = None
        self.latest_error = False
        self.checking_latest = False
        self.system_updates = None

    def enter(self):
        if self.latest is None and not self.checking_latest:
            self._check_latest()

    def _check_latest(self):
        self.checking_latest = True
        self.latest_error = False

        def done(res, err):
            self.checking_latest = False
            self.latest, self.latest_error = (None, True) if err else (res, False)
            self.refresh()
        self.app.run_task(settings.latest_version, done)

    def _newer(self):
        return self.latest and settings.version_tuple(self.latest) > settings.version_tuple(settings.setup_version())

    def refresh(self):
        n = self.system_updates
        if n is None:
            sys_detail, sys_color = 'Check now', None
        elif n == 0:
            sys_detail, sys_color = 'Up to date', GOOD
        else:
            sys_detail, sys_color = settings.plural(n, 'update'), WARN
        current = settings.setup_version() or 'unknown'
        if self.checking_latest:
            detail, color = 'Checking…', None
        elif self.latest_error:
            detail, color = 'Version %s' % current, None
        elif self._newer():
            detail, color = '%s available' % self.latest, WARN
        else:
            detail, color = 'Up to date (%s)' % current, GOOD
        self.list.set_items([
            Item('Moonlight and system', self._system, key='system', detail=sys_detail, detail_color=sys_color,
                 desc='New versions of Moonlight and of Raspberry Pi OS and its programs. Installing them '
                      'takes a few minutes.'),
            Item('This setup', self._setup, key='setup', detail=detail, detail_color=color,
                 spinner=self.checking_latest,
                 desc='This menu and the Moonlight Pi setup, from GitHub. Your settings are kept. Version '
                      '%s is installed.' % current),
        ])

    def _system(self):
        app = self.app

        def done(res, err):
            if err:
                app.message("Couldn't check for updates", str(err))
                return
            n, pkgs = res
            self.system_updates = n
            self.refresh()
            if n == 0:
                app.show_toast('Everything is up to date', GOOD)
                return
            moonlight = 'Includes a new version of Moonlight. ' if 'moonlight-qt' in pkgs else ''
            app.confirm('Install %s?' % settings.plural(n, 'update'),
                        moonlight + "It can take several minutes. Don't switch the Pi off until it finishes.",
                        'Install', lambda: app.push(ProgressScreen(app, 'Installing updates', ['updates', 'install'],
                                                                   self._system_done)))
        app.run_task(settings.check_updates, done, busy='Checking for updates…')

    def _system_done(self, ok, err):
        app = self.app
        if not ok:
            app.message("The updates didn't finish", err)
            return
        self.system_updates = 0
        self.refresh()
        app.choose('Updates installed', 'Restart the Pi now, so everything uses the new versions?',
                   ['Restart now', 'Later'], lambda i: app.power('reboot') if i == 0 else None)

    def _setup(self):
        app = self.app
        if self.checking_latest:
            app.show_toast('Still checking for a new version…')
        elif self.latest_error:
            app.message("Couldn't check for a new version", 'Is the Pi connected to the internet?')
            self._check_latest()
        elif self._newer():
            app.confirm('Update to version %s?' % self.latest,
                        'The setup is downloaded from GitHub and run again with your current settings. It takes '
                        'a few minutes.', 'Update',
                        lambda: app.push(ProgressScreen(app, 'Updating the setup', ['update-setup'], self._setup_done)))
        else:
            app.show_toast('This setup is up to date', GOOD)

    def _setup_done(self, ok, err):
        app = self.app
        if not ok:
            app.message("The update didn't finish", err)
            return
        self.refresh()
        app.choose('Setup updated', 'Restart now to finish? The menu and Moonlight start again afterwards.',
                   ['Restart now', 'Later'], lambda i: app.power('reboot') if i == 0 else None)

    def draw_panel(self, surf, rect):
        it = self.list.selected
        if it:
            describe(surf, rect, it)


# ---------------------------------------------------------------------------
# About
# ---------------------------------------------------------------------------
def _gb(n):
    return '%.1f GB' % (n / 1e9)


class AboutScreen(Screen):
    title = 'About this Pi'

    def __init__(self, app):
        super().__init__(app)
        self.info = None
        self.loading = False
        self.last = 0

    def tick(self):
        if not self.loading and time.monotonic() - self.last > 3:
            self.loading = True
            self.last = time.monotonic()
            self.app.run_task(settings.about, self._got)
        return False

    def _got(self, res, err):
        self.loading = False
        if res:
            self.info = res
        self.app.dirty = True

    def handle(self, action):
        if action in BACK:
            self.app.pop()

    def hints(self):
        return [('B', 'Back')]

    def rows(self):
        info = self.info or {}
        st = self.app.net_status or {}
        rows = []
        host = info.get('hostname', '')
        eth, wifi = st.get('ethernet') or {}, st.get('wifi') or {}
        if eth.get('connected'):
            rows.append(('Ethernet address', eth.get('ip') or 'Connected', TEXT))
        if wifi.get('connected'):
            rows.append(('Wi-Fi address', '%s (%s)' % (wifi.get('ip') or 'connected', wifi.get('ssid', '')), TEXT))
        if not rows:
            rows.append(('Network', 'Not connected', BAD))
        if st.get('tailscale'):
            rows.append(('Tailscale address', st['tailscale'], TEXT))
        if host:
            rows.append(('Connect from a computer', 'ssh %s@%s.local' % (getpass.getuser(), host), TEXT))
        rows.append(('Setup version', info.get('version') or 'Unknown', TEXT))
        rows.append(('Moonlight', info.get('moonlight') or 'Not installed', TEXT))
        rows.append(('Tailscale', info.get('tailscale_version') or 'Not installed', TEXT))
        temp = info.get('temp')
        if temp is None:
            rows.append(('Temperature', 'Unknown', DIM))
        else:
            # A Pi 5 slows itself down at 85 °C.
            rows.append(('Temperature', '%d °C' % round(temp), BAD if temp >= 85 else WARN if temp >= 80 else TEXT))
        level, text = settings.power_status(info.get('throttled'))
        rows.append(('Power', text, {'bad': BAD, 'warn': WARN, 'good': GOOD}.get(level, DIM)))
        if info.get('disk_total'):
            rows.append(('Storage', '%s free of %s' % (_gb(info['disk_free']), _gb(info['disk_total'])), TEXT))
        rows.append(('System', info.get('os') or 'Unknown', TEXT))
        rows.append(('Model', info.get('model') or 'Unknown', TEXT))
        rows.append(('Running for', settings.fmt_uptime(info.get('uptime')), TEXT))
        return rows

    def draw(self, surf):
        if self.info is None:
            ui.spinner(surf, (W // 2, H // 2 - 40), 40, ACCENT, 8)
            return
        rows = self.rows()
        col_w = (W - 2 * MARGIN - 80) // 2
        per_col = (len(rows) + 1) // 2
        for i, (label, value, color) in enumerate(rows):
            x = MARGIN + (i // per_col) * (col_w + 80)
            y = CONTENT_Y + (i % per_col) * 104
            draw_text(surf, label, (x, y), 26, FAINT, bold=True)
            draw_text(surf, value, (x, y + 34), 34, color, max_width=col_w)


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------
# (name, status key saying it can be removed, label, description, why it can't be)
REMOVE_CHOICES = [
    ('moonlight', 'moonlight_installed', 'Also remove Moonlight',
     'Uninstalls the Moonlight app. Its settings and pairing with your PC are kept, in case you install it '
     'again.', "Moonlight isn't installed."),
    ('tailscale', 'tailscale_by_setup', 'Also remove Tailscale', 'Signs the Pi out of Tailscale and uninstalls it.',
     "Tailscale wasn't installed by this setup, so it's left as it is."),
]


class UninstallScreen(ListScreen):
    title = 'Uninstall this setup'

    def __init__(self, app, status):
        super().__init__(app)
        self.status = status or {}
        self.remove = set()

    def refresh(self):
        items = [Item(label, lambda n=name: self._flip(n), key=name, toggle=name in self.remove, desc=desc,
                      disabled=self.status.get(key) != '1', reason=reason)
                 for name, key, label, desc, reason in REMOVE_CHOICES]
        items.append(Item('Uninstall', self._confirm, key='go', color=BAD,
                          desc='Removes this setup, and anything switched on above, then restarts the Pi.'))
        self.list.set_items(items)

    def _flip(self, name):
        self.remove ^= {name}
        self.refresh()

    def _chosen(self):
        return [(name, label) for name, _, label, _, _ in REMOVE_CHOICES if name in self.remove]

    def _confirm(self):
        also = [label.split()[-1] for _, label in self._chosen()]
        extra = (', and uninstalls %s' % ' and '.join(also)) if also else ''
        self.app.confirm('Uninstall this setup?',
                         'This removes the TV menu, its settings and auto-start%s. The Pi then restarts the way it '
                         'started before the setup. To set it up again, you\'ll need to run the install command '
                         'over SSH.' % extra, 'Uninstall', self._go, danger=True, default=1)

    def _go(self):
        value = ','.join(name for name, _ in self._chosen()) or 'none'
        self.app.push(ProgressScreen(self.app, 'Uninstalling', ['uninstall', value], self._done))

    def _done(self, ok, err):
        if not ok:
            self.app.message("The uninstall didn't finish",
                             '%s Finish it over SSH with: ~/.local/share/moonlight-pi-setup/moonlight-pi-setup.sh '
                             '--uninstall' % err)
            return
        self.app.push(UninstalledScreen(self.app))

    def draw_panel(self, surf, rect):
        y = draw_wrapped(surf, 'What this does', (rect.x, rect.y), rect.width, 36, TEXT, bold=True) + 12
        for head, body in (
                ('Removes', 'The TV menu and its settings, auto-start (the Pi stops going straight into Moonlight), '
                            'USB sharing, and the setup\'s services and boot changes.'),
                ('Keeps', 'Your Moonlight settings and pairing, and backups of the files the setup changed.'),
                ('Afterwards', 'The Pi restarts the way it started before the setup: on Raspberry Pi OS Lite, a '
                               'text login prompt.')):
            y = draw_text(surf, head, (rect.x, y), 26, FAINT, bold=True).bottom + 4
            y = draw_wrapped(surf, body, (rect.x, y), rect.width, 28, DIM, line_gap=5) + 16
        it = self.list.selected
        if it and it.desc:
            pygame.draw.line(surf, PANEL_HI, (rect.x, y), (rect.right, y), 2)
            y += 18
            y = draw_wrapped(surf, it.desc, (rect.x, y), rect.width, 28, TEXT, line_gap=5) + 10
            if it.disabled and it.reason:
                draw_wrapped(surf, it.reason, (rect.x, y), rect.width, 28, WARN, line_gap=5)


class UninstalledScreen(Screen):
    """The last screen: the settings helper restarts the Pi 15 seconds after
    the uninstall, and there's no menu to go back to."""
    title = 'Uninstalled'

    def __init__(self, app, seconds=15):
        super().__init__(app)
        self.restart_at = time.monotonic() + seconds
        self.shown = None

    def seconds_left(self):
        return max(0, int(self.restart_at - time.monotonic() + 0.999))

    def tick(self):
        left = self.seconds_left()
        if left != self.shown:
            self.shown = left
            return True
        return False

    def handle(self, action):
        pass

    def hints(self):
        return []

    def draw(self, surf):
        left = pygame.Rect(MARGIN, CONTENT_Y, 600, 600)
        right_x = MARGIN + 680
        width = W - MARGIN - right_x
        ui.tick_mark(surf, left.center)
        y = draw_text(surf, 'This setup has been removed', (right_x, CONTENT_Y), 44, TEXT, bold=True).bottom + 30
        n = self.seconds_left()
        when = 'The Pi restarts in %s' % settings.plural(n, 'second') if n else 'Restarting…'
        y = draw_wrapped(surf, when + ', the way it started before the setup: on Raspberry Pi OS Lite, a text login '
                         'prompt.', (right_x, y), width, 32, TEXT, line_gap=8) + 24
        y = draw_wrapped(surf, 'To set it up again, connect to the Pi over SSH and run the install command from '
                         '%s.' % REPO, (right_x, y), width, 30, DIM, line_gap=6) + 24
        if time.monotonic() - self.restart_at > 45:
            draw_wrapped(surf, "If the Pi hasn't restarted, unplug it and plug it back in.", (right_x, y), width, 30,
                         WARN)


# ---------------------------------------------------------------------------
# Quick setup: the first-time questions, one per screen
# ---------------------------------------------------------------------------
class QuickSetupPage(ChoiceScreen):
    def __init__(self, flow, index, **kw):
        super().__init__(flow.app, 'Quick setup', **kw)
        self.flow = flow
        self.index = index

    def handle(self, action):
        if action in BACK and self.index == 0:
            self.app.confirm('Skip Quick setup?', 'You can go through it any time from Settings.', 'Skip',
                             self.flow.finish)
            return
        super().handle(action)

    def draw_more(self, surf, rect, y):
        if self.index == 0 and (self.app.autopair or not self.app.controllers):
            ui.pairing_box(surf, pygame.Rect(rect.x - 16, max(y + 10, rect.bottom - 300), rect.width + 32, 300 + 16))
        elif self.flow.pages[self.index] == 'pcsetup':
            usb_screens().draw_pc_command(surf, rect.x, y + 10, rect.width, self.app)


# The on/off questions: page name -> (helper setting, status key).
QUICK_SWITCHES = {
    'quit': ('quit-on-shutdown', 'quit_on_shutdown'),
    'quiet': ('quiet-boot', 'quiet_boot'),
    'powersave': ('wifi-powersave', 'wifi_powersave'),
    'usb': ('usb-sharing', 'usb_sharing'),
}


class QuickSetup:
    """Asks the first-time questions (each applied straight away), then calls then()."""

    def __init__(self, app, then=None):
        self.app = app
        self.then = then
        self.status = {}
        self.pages = []
        self.speakers = 'stereo'
        self.quiet_at_start = None

    def start(self):
        self.app.run_task(lambda: (settings.status(), settings.tailscale_status()), self._loaded,
                          busy='Getting ready…')

    def _loaded(self, res, err):
        self.status, ts = res or ({}, {})
        st = self.status
        self.speakers = st.get('speakers') if st.get('speakers') in settings.SPEAKER_NAMES else 'stereo'
        self.quiet_at_start = st.get('quiet_boot')
        self.pages = ['welcome']
        # Raspberry Pi OS keeps Wi-Fi off until it knows the country.
        if st.get('wifi') == '1' and not st.get('wifi_country'):
            self.pages.append('country')
        self.pages += ['speakers', 'test', 'quit', 'quiet']
        if st.get('wifi') == '1':
            self.pages.append('powersave')
        if st.get('usbip') == '1':
            self.pages += ['usb', 'pcsetup']
        if ts.get('installed') and ts.get('state') in ('NeedsLogin', 'NoState'):
            self.pages.append('tailscale')
        self.pages.append('done')
        self.show(0)

    def skip(self, name):
        """Questions that turn out not to apply by the time they're reached (the
        network often isn't up yet when Quick setup starts)."""
        if name == 'powersave':
            return bool(((self.app.net_status or {}).get('ethernet') or {}).get('connected'))
        if name == 'tailscale':
            return not self.app.online()     # signing in needs the internet
        if name == 'pcsetup':
            return self.status.get('usb_sharing') != '1'
        return False

    def show(self, i):
        while self.skip(self.pages[i]):
            del self.pages[i]                # so the later steps are numbered right
        self.app.push(self.page(i))

    def page(self, i):
        name = self.pages[i]
        st = self.status
        step = 'Step %d of %d' % (i, len(self.pages) - 2) if 0 < i < len(self.pages) - 1 else ''

        def page(heading, intro, options, initial=None):
            return QuickSetupPage(self, i, heading=heading, intro=intro, options=options, initial=initial,
                                  step=step, show_current=False, on_pick=lambda v: self.pick(i, v))

        def current(key, default='1'):
            return on_off(st.get(key, default) == '1')

        if name == 'welcome':
            return page('Welcome to your Moonlight Pi',
                        "A few quick questions to finish setting up. It takes about a minute, and you can change "
                        "any of it later in Settings.",
                        [('start', 'Start', '', ''),
                         ('skip', 'Skip for now', 'You can go through it any time from Settings.', '')])
        if name == 'country':
            guess = settings.guess_country()
            return page('Which country is the Pi in?',
                        'Wi-Fi uses different channels in each country, so the Pi keeps its Wi-Fi switched off '
                        'until it knows where it is.' +
                        (" The country of the Pi's time zone is highlighted." if guess else ''),
                        [(code, name, '', code) for code, name in settings.countries()], guess)
        if name == 'speakers':
            return page('Which speakers do you use?',
                        "This sets the sound for streaming. Moonlight's own audio setting is changed to match.",
                        SPEAKER_OPTIONS, self.speakers)
        if name == 'test':
            return page('Test your speakers',
                        'Each speaker says where it is, for example "Front left". Make sure the TV (and receiver, '
                        'if you have one) is on.',
                        [('play', 'Play a speaker test', '', ''), ('next', 'Continue', '', '')])
        if name == 'quit':
            return page('End the stream on your PC when the Pi shuts down?',
                        'When the Pi is switched off, for example with its power button, the stream keeps running '
                        'on your PC, and the PC can stay stuck on it.',
                        [('on', 'Yes, end the stream on shutdown', 'The game you\'re streaming closes, so save '
                          'before switching the Pi off.', 'Recommended'),
                         ('off', 'No, leave the stream running', 'End it on the PC yourself.', '')],
                        current('quit_on_shutdown'))
        if name == 'quiet':
            return page('Hide boot messages?',
                        'While the Pi starts, it can show a screen full of text, or nothing until Moonlight '
                        'appears.',
                        [('on', 'Hide boot messages', 'A cleaner start. Takes effect after a restart.',
                          'Recommended'),
                         ('off', 'Show boot messages', 'Useful if something goes wrong while starting.', '')],
                        current('quiet_boot'))
        if name == 'powersave':
            return page('Turn off Wi-Fi power saving?',
                        'Power saving makes the Wi-Fi doze between packets, which causes stutter and lag spikes '
                        'when streaming.',
                        [('off', 'Turn it off', 'Smoother streaming over Wi-Fi.', 'Recommended'),
                         ('on', 'Leave it on', 'Uses slightly less power.', '')],
                        current('wifi_powersave', '0'))
        if name == 'usb':
            return page('Send USB devices to your PC while you stream?',
                        'USB devices plugged into the Pi, like a wired controller, can go to your PC during a '
                        'stream, as if they were plugged into it. Your PC needs setting up for this too.',
                        [('on', 'Yes, while I stream', "Controllers and headsets go to your PC during a stream, and "
                          "come back to the Pi afterwards, so they work here and in Moonlight. Choose others in "
                          "Settings > USB sharing.", 'Recommended'),
                         ('off', 'No, keep them on the Pi', 'Controllers still work in games, through Moonlight.',
                          '')],
                        current('usb_sharing'))
        if name == 'pcsetup':
            return page('Set up your gaming PC',
                        "On the PC, open PowerShell and run this command. It lets the PC receive this Pi's USB "
                        "devices. It's also in Settings > USB sharing > Set up your PC.",
                        [('next', 'Continue', '', '')])
        if name == 'tailscale':
            return page('Stream away from home?',
                        'Tailscale lets you stream from your PC when you take the Pi somewhere else. Sign the Pi in '
                        'with your phone; it takes a minute. You can also do it later in Settings > Tailscale.',
                        [('signin', 'Sign in to Tailscale', 'Scan a code with your phone and sign in. Use the same '
                          'account as on your gaming PC, or create a free one.', ''),
                         ('later', 'Not now', 'Sign in any time in Settings > Tailscale.', '')])
        if self.restart_needed():
            boot = 'hidden' if st.get('quiet_boot') == '1' else 'showing'
            return page('All set', 'You can change any of this in Settings, on the main menu. The boot messages '
                                   'change after a restart.',
                        [('restart', 'Restart now', 'Starts again with boot messages %s.' % boot, ''),
                         ('finish', 'Finish', 'Restart later.', '')])
        return page('All set', 'You can change any of this in Settings, on the main menu.',
                    [('finish', 'Finish', '', '')])

    def restart_needed(self):
        return self.quiet_at_start is not None and self.status.get('quiet_boot') != self.quiet_at_start

    def pick(self, i, value):
        app = self.app
        name = self.pages[i]
        st = self.status

        def next_page():
            self.show(i + 1)

        def apply(fn, key, new):
            def done(res, err):
                if err:
                    app.message("Couldn't save that", str(err))
                    return
                st[key] = new
                next_page()
            app.run_task(fn, done, busy='Saving…')

        if name == 'welcome':
            if value == 'start':
                next_page()
            else:
                self.finish()
        elif name == 'country':
            apply(lambda: settings.change('wifi-country', value), 'wifi_country', value)
        elif name == 'speakers':
            if value == st.get('speakers'):
                next_page()
            else:
                self.speakers = value
                apply(lambda: settings.set_speakers(value), 'speakers', value)
        elif name == 'test':
            if value == 'play':
                play_speaker_test(app, self.speakers)
            else:
                next_page()
        elif name == 'pcsetup':
            next_page()
        elif name == 'tailscale':
            if value == 'signin':
                # Backing out of the sign-in returns here, to try again or choose Not now.
                app.push(TailscaleLoginScreen(app, on_done=next_page))
            else:
                next_page()
        elif name in QUICK_SWITCHES:
            setting, key = QUICK_SWITCHES[name]
            new = '1' if value == 'on' else '0'
            if st.get(key) == new:
                next_page()
            else:
                apply(lambda: settings.change(setting, value), key, new)
        elif name == 'done':
            self.finish()
            if value == 'restart':
                app.power('reboot')

    def finish(self):
        app = self.app
        try:
            settings.finish_quick_setup()
        except OSError:
            pass
        while isinstance(app.top, QuickSetupPage):
            app.pop()
        if self.then:
            self.then()
