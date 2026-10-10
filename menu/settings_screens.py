"""Settings, Quick setup, Tailscale, Updates and About screens for the menu."""
import collections
import getpass
import re
import threading
import time

import pygame

import settings
import ui
from ui import (W, MARGIN, CONTENT_Y, FOOTER_Y, PANEL_HI, ACCENT, TEXT, DIM, FAINT, GOOD, WARN, BAD,
                Item, ListScreen, Screen, draw_text, draw_wrapped, rrect, dot)

ISSUES_URL = 'github.com/joshmichael/moonlight-pi-setup/issues'
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
    'quit': 'When the Pi is switched off, for example with its power button, ask your PC to quit the game, so '
            'the PC isn\'t left stuck on the stream. The game closes without saving.',
    'usb': 'On: a controller plugged into the Pi works here and in Moonlight between streams, and moves to your '
           'PC while you\'re streaming. Off: USB devices stay on the PC all the time.',
    'tailscale': 'Stream when you\'re away from home: see whether Tailscale is connected, sign in with your '
                 'phone, or turn it off.',
    'tailscale_install': 'Tailscale lets you stream when you\'re away from home. It\'s free for personal use.',
    'powersave': 'Power saving makes the Wi-Fi doze between packets, which causes stutter and lag spikes when '
                 'streaming. Best left off.',
    'country': 'Wi-Fi uses different channels in each country. The Pi needs to know where it is to find every '
               'network.',
    'quiet': 'Hide the text and the splash screen shown while the Pi starts. Takes effect after a restart.',
    'updates': 'Update Moonlight and the system, or this setup.',
    'about': 'Network addresses, versions, temperature and power.',
    'quick': 'Go through the first-time questions again: speakers, quitting games at shutdown, and more.',
}


def on_off(v):
    return 'on' if v else 'off'


# ---------------------------------------------------------------------------
# Speaker test (Settings and Quick setup)
# ---------------------------------------------------------------------------
def play_speaker_test(app, mode):
    if mode not in settings.SPEAKER_CHANNELS:
        mode = 'stereo'

    def wrong(i):
        if i == 1:
            app.message('Speakers in the wrong place',
                        'Check each speaker is plugged into the right output on your receiver or TV. If they are, '
                        'please report it, with your TV and receiver models, at %s' % ISSUES_URL)

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
    app.run_task(lambda: app.settings.speaker_test(mode), done,
                 busy='Speaker test: each speaker says where it is…')


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
                    self.list.index = i
                    self.list._clamp_scroll()

    def extra_items(self):
        return []

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
        return [Item('Play a speaker test', self._test, key='test',
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
        app.run_task(lambda: app.settings.speakers(mode), done, busy='Changing the speakers…')

    def _test(self):
        play_speaker_test(self.app, self.current)


class CountryScreen(ListScreen):
    title = 'Wi-Fi country'

    def __init__(self, app, current, on_changed=None):
        super().__init__(app)
        self.current = current
        self.on_changed = on_changed
        self.countries = settings.countries()
        self.initial = current or settings.guess_country()
        self.placed = False

    def refresh(self):
        self.list.set_items([Item(name, lambda c=code: self._pick(c), key=code,
                                  detail='Current' if code == self.current else code,
                                  detail_color=GOOD if code == self.current else FAINT)
                             for code, name in self.countries])
        if not self.placed:
            self.placed = True
            self._jump_to(self.initial)

    def _jump_to(self, code):
        for i, (c, _) in enumerate(self.countries):
            if c == code:
                self.list.index = i
                self.list._clamp_scroll()

    def _page(self, d):
        n = len(self.list.items)
        if n:
            self.list.index = max(0, min(n - 1, self.list.index + d * self.list.visible_count()))
            self.list._clamp_scroll()

    def handle(self, action):
        if action in ('left', 'lb'):
            self._page(-1)
        elif action in ('right', 'rb'):
            self._page(1)
        else:
            super().handle(action)

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
            if self.on_changed:
                self.on_changed()
            if app.top is self:
                app.pop()
        app.run_task(lambda: app.settings.set('wifi-country', code), done, busy='Setting the Wi-Fi country…')

    def draw_panel(self, surf, rect):
        y = draw_wrapped(surf, 'Where is the Pi?', (rect.x, rect.y), rect.width, 36, TEXT, bold=True) + 12
        y = draw_wrapped(surf, DESCRIPTIONS['country'], (rect.x, y), rect.width, 29, DIM, line_gap=6) + 24
        if self.current:
            draw_text(surf, 'Now: %s' % settings.country_name(self.current), (rect.x, y), 30, GOOD,
                      max_width=rect.width)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def tailscale_summary(ts):
    """(text, colour) for the Tailscale row."""
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


class SettingsScreen(ListScreen):
    title = 'Settings'

    def __init__(self, app):
        super().__init__(app)
        self.status = None
        self.ts = None
        self.error = ''
        self.loading = False

    def enter(self):
        self.load()

    def load(self):
        if self.loading:
            return
        self.loading = True
        app = self.app
        app.run_task(lambda: (app.settings.status(), app.settings.tailscale_status()), self._loaded)

    def _loaded(self, res, err):
        self.loading = False
        if err:
            self.error = str(err)
            self.status = {}
        else:
            self.error = ''
            self.status, self.ts = res
            if self.app.quiet_boot_at_start is None:
                self.app.quiet_boot_at_start = self.status.get('quiet_boot')
        self.refresh()

    def restart_needed(self):
        st = self.status or {}
        start = self.app.quiet_boot_at_start
        return start is not None and st.get('quiet_boot') not in (None, start)

    def refresh(self):
        app = self.app
        if self.status is None:
            self.list.set_items([Item('Loading…', selectable=False, spinner=True)])
            return
        if self.error:
            self.list.set_items([
                Item("Settings aren't available", selectable=False, color=WARN, desc=self.error),
                Item('About this Pi', lambda: app.push(AboutScreen(app)), key='about', desc=DESCRIPTIONS['about']),
            ])
            return
        st = self.status
        speakers = settings.SPEAKER_NAMES.get(st.get('speakers'), 'Not set')
        ts_text, ts_color = tailscale_summary(self.ts)
        items = [
            Item('Speakers', lambda: app.push(SpeakersScreen(app, st.get('speakers'), self.load)), key='speakers',
                 detail=speakers, desc=DESCRIPTIONS['speakers']),
            Item('Quit game on PC at shutdown', lambda: self._toggle('quit-on-shutdown', 'quit_on_shutdown',
                                                                    'Quit game at shutdown'),
                 key='quit', toggle=st.get('quit_on_shutdown') == '1', desc=DESCRIPTIONS['quit']),
        ]
        if st.get('virtualhere') == '1':
            items.append(Item('Share USB only while streaming',
                              lambda: self._toggle('usb-handoff', 'usb_handoff', 'Share USB only while streaming'),
                              key='usb', toggle=st.get('usb_handoff') == '1', desc=DESCRIPTIONS['usb']))
        items.append(Item('Tailscale', lambda: app.push(TailscaleScreen(app)), key='tailscale', detail=ts_text,
                          detail_color=ts_color, desc=DESCRIPTIONS['tailscale']))
        if st.get('wifi') == '1':
            items.append(Item('Wi-Fi power saving', lambda: self._toggle('wifi-powersave', 'wifi_powersave',
                                                                        'Wi-Fi power saving'),
                              key='powersave', toggle=st.get('wifi_powersave') == '1', desc=DESCRIPTIONS['powersave']))
            code = st.get('wifi_country', '')
            items.append(Item('Wi-Fi country', lambda: app.push(CountryScreen(app, code, self.load)), key='country',
                              detail=settings.country_name(code) if code else 'Not set',
                              detail_color=None if code else WARN, desc=DESCRIPTIONS['country']))
        items.append(Item('Hide boot messages', lambda: self._toggle('quiet-boot', 'quiet_boot', 'Hide boot messages'),
                          key='quiet', toggle=st.get('quiet_boot') == '1',
                          detail='Restart needed' if self.restart_needed() else '', detail_color=WARN,
                          desc=DESCRIPTIONS['quiet']))
        items += [
            Item('Updates', lambda: app.push(UpdatesScreen(app)), key='updates',
                 detail='Version %s' % app.settings.setup_version() if app.settings.setup_version() else '',
                 desc=DESCRIPTIONS['updates']),
            Item('About this Pi', lambda: app.push(AboutScreen(app)), key='about', desc=DESCRIPTIONS['about']),
            Item('Run Quick setup again', lambda: QuickSetup(app).start(), key='quick', desc=DESCRIPTIONS['quick']),
        ]
        self.list.set_items(items)

    def _toggle(self, name, key, label):
        app = self.app
        on = (self.status or {}).get(key) != '1'

        def done(res, err):
            if err:
                app.message("Couldn't change that", str(err))
            else:
                self.status[key] = '1' if on else '0'
                note = ' (after a restart)' if name == 'quiet-boot' else ''
                app.show_toast('%s: %s%s' % (label, 'On' if on else 'Off', note), GOOD)
            self.load()
        app.run_task(lambda: app.settings.set(name, on_off(on)), done,
                     busy='Turning on…' if on else 'Turning off…')

    def handle(self, action):
        if action in ('back', 'backspace', 'escape') and self.restart_needed() and not self.app.restart_prompted:
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
        y = draw_wrapped(surf, it.label, (rect.x, rect.y), rect.width, 36, TEXT, bold=True) + 12
        if it.desc:
            y = draw_wrapped(surf, it.desc, (rect.x, y), rect.width, 30, DIM, line_gap=6) + 20
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
        self.loading = False
        self.last = 0

    def enter(self):
        self.load()

    def load(self):
        if self.loading:
            return
        self.loading = True
        self.last = time.monotonic()
        self.app.run_task(self.app.settings.tailscale_status, self._loaded)

    def _loaded(self, res, err):
        self.loading = False
        self.ts = res or {'installed': False}
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
        items = []
        if state == 'Running':
            items.append(Item('Turn off', self._down, key='onoff'))
        elif state == 'Stopped':
            items.append(Item('Turn on', self._up, key='onoff'))
        elif state in ('NeedsLogin', 'NoState'):
            items.append(Item('Sign in', self._up, key='signin'))
        elif state == 'NeedsMachineAuth':
            items.append(Item('Waiting for approval', selectable=False))
        else:
            items.append(Item('Tailscale is starting…', selectable=False, spinner=True))
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

    def _up(self):
        self.app.push(TailscaleLoginScreen(self.app))

    def _down(self):
        app = self.app
        app.run_task(lambda: app.settings.tailscale('down'),
                     lambda r, e: (app.message("Couldn't turn Tailscale off", str(e)) if e
                                   else app.show_toast('Tailscale is off'), self.load()),
                     busy='Turning Tailscale off…')

    def _logout(self):
        app = self.app
        app.confirm('Sign out of Tailscale?', "You'll need to sign in again to stream away from home.", 'Sign out',
                    lambda: app.run_task(lambda: app.settings.tailscale('logout'),
                                         lambda r, e: (e and app.message("Couldn't sign out", str(e)), self.load()),
                                         busy='Signing out…'), danger=True)

    def draw_panel(self, surf, rect):
        ts = self.ts or {}
        y = rect.y
        text, color = tailscale_summary(ts)
        dot(surf, color or DIM, (rect.x + 10, y + 21), 9)
        y = draw_text(surf, text, (rect.x + 36, y), 34, TEXT, bold=True).bottom + 20
        for label, value in (('Address', ts.get('ip')), ('Device name', ts.get('name')),
                             ('Account', ts.get('tailnet'))):
            if value and ts.get('state') == 'Running':
                draw_text(surf, label, (rect.x, y), 26, FAINT, bold=True)
                draw_text(surf, value, (rect.x, y + 32), 30, TEXT, max_width=rect.width)
                y += 84
        y += 10
        y = draw_wrapped(surf, "Tailscale lets you stream when you're away from home. Install it on your gaming PC "
                         'too, signed in to the same account.', (rect.x, y), rect.width, 28, DIM, line_gap=6) + 14
        y = draw_wrapped(surf, "Away from home, add your PC in Moonlight using its Tailscale address (100.x.x.x), "
                         'and lower the bitrate.', (rect.x, y), rect.width, 28, DIM, line_gap=6) + 14
        draw_wrapped(surf, 'USB sharing (VirtualHere) only works at home.', (rect.x, y), rect.width, 28, FAINT)


class TailscaleLoginScreen(Screen):
    title = 'Sign in to Tailscale'

    def __init__(self, app):
        super().__init__(app)
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
            ok, err = app.settings.stream(['tailscale', 'up'], on_line)
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
            return False
        if self.state in ('starting', 'url') and not self.polling and now - self.last_poll > 2:
            self.polling = True
            self.last_poll = now
            self.app.run_task(self.app.settings.tailscale_status, self._polled)
        return False

    def _polled(self, res, err):
        self.polling = False
        if res and res.get('state') == 'Running':
            self._success()

    def handle(self, action):
        if action in ('back', 'backspace', 'escape'):
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
            return
        if self.state == 'done':
            pygame.draw.circle(surf, GOOD, left.center, 120)
            pygame.draw.lines(surf, (255, 255, 255), False, [(left.centerx - 55, left.centery),
                                                            (left.centerx - 15, left.centery + 45),
                                                            (left.centerx + 60, left.centery - 45)], 18)
            draw_text(surf, 'Tailscale is connected', (right_x, CONTENT_Y), 44, TEXT, bold=True)
            draw_wrapped(surf, 'You can stream from away from home now.', (right_x, CONTENT_Y + 80), width, 32)
            return
        if self.state == 'starting':
            ui.spinner(surf, left.center, 50, ACCENT, 10)
            draw_text(surf, 'Connecting to Tailscale…', (right_x, CONTENT_Y), 44, TEXT, bold=True)
            draw_wrapped(surf, 'If the Pi needs signing in, a code to scan appears here.', (right_x, CONTENT_Y + 80),
                         width, 32)
            return
        rrect(surf, (255, 255, 255), left, 24)
        if self.qr:
            surf.blit(self.qr, self.qr.get_rect(center=left.center))
        y = CONTENT_Y
        for num, head, body in (
                ('1', 'Scan the code with your phone', 'Or open the link below on any phone or computer.'),
                ('2', 'Sign in to Tailscale', 'Use the same account as on your gaming PC. If you don\'t have one, '
                                              'you can create one for free.'),
                ('3', 'This screen updates by itself', 'when the Pi is signed in.')):
            pygame.draw.circle(surf, ACCENT, (right_x + 30, y + 30), 30)
            draw_text(surf, num, (right_x + 30, y + 30), 34, (255, 255, 255), bold=True, anchor='center')
            draw_text(surf, head, (right_x + 84, y + 8), 36, TEXT, bold=True, max_width=width - 84)
            y = draw_wrapped(surf, body, (right_x + 84, y + 60), width - 84, 30) + 28
        draw_wrapped(surf, self.url or '', (right_x + 84, y + 10), width - 84, 28, FAINT)


# ---------------------------------------------------------------------------
# Long-running jobs (updates, installing Tailscale)
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
            ok, err = app.settings.stream(self.args, lambda line: app.call_soon(lambda: self._line(line)))
            app.call_soon(lambda: self._finish(ok, err))
        threading.Thread(target=work, daemon=True).start()

    def _line(self, line):
        prog = settings.apt_progress(line)
        if prog:
            self.percent, self.step = prog
            return
        line = ANSI_RE.sub('', line).strip()
        if not line or line.startswith('{'):
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
        if action in ('back', 'backspace', 'escape', 'select', 'enter'):
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
        rrect(surf, ui.PANEL, box, 18)
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
            self.latest, self.latest_error = (res, False) if not err else (None, True)
            self.refresh()
        self.app.run_task(self.app.settings.latest_version, done)

    def _newer(self):
        return self.latest and settings.version_tuple(self.latest) > settings.version_tuple(
            self.app.settings.setup_version())

    def refresh(self):
        n = self.system_updates
        if n is None:
            sys_detail, sys_color = 'Check now', None
        elif n == 0:
            sys_detail, sys_color = 'Up to date', GOOD
        else:
            sys_detail, sys_color = '%d update%s' % (n, '' if n == 1 else 's'), WARN
        current = self.app.settings.setup_version() or 'unknown'
        if self.checking_latest:
            setup_item = Item('This setup', self._setup, key='setup', detail='Checking…', spinner=True)
        elif self.latest_error:
            setup_item = Item('This setup', self._setup, key='setup', detail='Version %s' % current)
        elif self._newer():
            setup_item = Item('This setup', self._setup, key='setup', detail='%s available' % self.latest,
                              detail_color=WARN)
        else:
            setup_item = Item('This setup', self._setup, key='setup', detail='Up to date (%s)' % current,
                              detail_color=GOOD)
        setup_item.desc = ('This menu and the Moonlight Pi setup, from GitHub. Your settings are kept. Version '
                           '%s is installed.' % current)
        self.list.set_items([
            Item('Moonlight and system', self._system, key='system', detail=sys_detail, detail_color=sys_color,
                 desc='New versions of Moonlight and of Raspberry Pi OS and its programs. Installing them '
                      'takes a few minutes.'),
            setup_item,
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
            app.confirm('Install %d update%s?' % (n, '' if n == 1 else 's'),
                        moonlight + "It can take several minutes. Don't switch the Pi off until it finishes.",
                        'Install', lambda: app.push(ProgressScreen(app, 'Installing updates', ['updates', 'install'],
                                                                   self._system_done)))
        app.run_task(app.settings.check_updates, done, busy='Checking for updates…')

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
            y = draw_wrapped(surf, it.label, (rect.x, rect.y), rect.width, 36, TEXT, bold=True) + 12
            draw_wrapped(surf, it.desc, (rect.x, y), rect.width, 30, DIM, line_gap=6)


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
            self.app.run_task(self.app.settings.about, self._got)
        return False

    def _got(self, res, err):
        self.loading = False
        if res:
            self.info = res
        self.app.dirty = True

    def handle(self, action):
        if action in ('back', 'backspace', 'escape'):
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
            ui.spinner(surf, (W // 2, ui.H // 2 - 40), 40, ACCENT, 8)
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
# Quick setup: the first-time questions, one per screen
# ---------------------------------------------------------------------------
class QuickSetupPage(ChoiceScreen):
    def __init__(self, flow, index, **kw):
        super().__init__(flow.app, 'Quick setup', **kw)
        self.flow = flow
        self.index = index

    def handle(self, action):
        if action in ('back', 'backspace', 'escape') and self.index == 0:
            self.app.confirm('Skip Quick setup?', 'You can go through it any time from Settings.', 'Skip',
                             self.flow.finish)
            return
        super().handle(action)

    def draw_more(self, surf, rect, y):
        if self.index == 0 and (self.app.autopair or not self.app.controllers):
            ui.pairing_box(surf, pygame.Rect(rect.x - 16, max(y + 10, rect.bottom - 300), rect.width + 32, 300 + 16))


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
        self.app.run_task(self.app.settings.status, self._loaded, busy='Getting ready…')

    def _loaded(self, res, err):
        self.status = res or {}
        self.speakers = self.status.get('speakers') if self.status.get('speakers') in settings.SPEAKER_NAMES \
            else 'stereo'
        self.quiet_at_start = self.status.get('quiet_boot')
        st = self.app.net_status or {}
        on_ethernet = bool((st.get('ethernet') or {}).get('connected'))
        self.pages = ['welcome', 'speakers', 'test', 'quit', 'quiet']
        if self.status.get('wifi') == '1' and not on_ethernet:
            self.pages.append('powersave')
        if self.status.get('virtualhere') == '1':
            self.pages.append('usb')
        self.pages.append('done')
        self.show(0)

    def show(self, i):
        self.app.push(self.page(i))

    def page(self, i):
        name = self.pages[i]
        st = self.status
        step = 'Step %d of %d' % (i, len(self.pages) - 2) if 0 < i < len(self.pages) - 1 else ''
        kw = dict(step=step, show_current=False, on_pick=lambda v: self.pick(i, v))
        if name == 'welcome':
            return QuickSetupPage(self, i, heading='Welcome to your Moonlight Pi',
                                  intro="A few quick questions to finish setting up. It takes about a minute, and "
                                        "you can change any of it later in Settings.",
                                  options=[('start', 'Start', '', ''),
                                           ('skip', 'Skip for now', 'You can go through it any time from Settings.',
                                            '')], **kw)
        if name == 'speakers':
            return QuickSetupPage(self, i, heading='Which speakers do you use?',
                                  intro="This sets the sound for streaming. Moonlight's own audio setting is "
                                        "changed to match.",
                                  options=SPEAKER_OPTIONS, initial=self.speakers, **kw)
        if name == 'test':
            return QuickSetupPage(self, i, heading='Test your speakers',
                                  intro='Each speaker says where it is, for example "Front left". Make sure the TV '
                                        '(and receiver, if you have one) is on.',
                                  options=[('play', 'Play a speaker test', '', ''),
                                           ('next', 'Continue', '', '')], **kw)
        if name == 'quit':
            return QuickSetupPage(self, i, heading='Quit the game on your PC when the Pi shuts down?',
                                  intro='When the Pi is switched off, for example with its power button, the game '
                                        'keeps running on your PC, and the PC can stay stuck on the stream.',
                                  options=[('on', 'Yes, quit the game', 'Save before switching the Pi off: the game '
                                            'closes without saving.', 'Recommended'),
                                           ('off', 'No, leave it running', 'Quit the game on the PC yourself.', '')],
                                  initial=on_off(st.get('quit_on_shutdown', '1') == '1'), **kw)
        if name == 'quiet':
            return QuickSetupPage(self, i, heading='Hide boot messages?',
                                  intro='While the Pi starts, it can show a screen full of text, or nothing until '
                                        'Moonlight appears.',
                                  options=[('on', 'Hide them', 'A cleaner start. Takes effect after a restart.',
                                            'Recommended'),
                                           ('off', 'Show them', 'Useful if something goes wrong while starting.', '')],
                                  initial=on_off(st.get('quiet_boot', '1') == '1'), **kw)
        if name == 'powersave':
            return QuickSetupPage(self, i, heading='Turn off Wi-Fi power saving?',
                                  intro='Power saving makes the Wi-Fi doze between packets, which causes stutter and '
                                        'lag spikes when streaming.',
                                  options=[('off', 'Turn it off', 'Smoother streaming over Wi-Fi.', 'Recommended'),
                                           ('on', 'Leave it on', 'Uses slightly less power.', '')],
                                  initial=on_off(st.get('wifi_powersave') == '1'), **kw)
        if name == 'usb':
            return QuickSetupPage(self, i, heading='When should USB devices go to your PC?',
                                  intro='VirtualHere shares USB devices plugged into the Pi, like a wired controller, '
                                        'with your PC.',
                                  options=[('on', 'Only while streaming', "A controller works here and in Moonlight "
                                            "between streams, and moves to your PC while you're streaming.",
                                            'Recommended'),
                                           ('off', 'All the time', 'Devices stay on the PC, even between streams.', '')],
                                  initial=on_off(st.get('usb_handoff', '1') == '1'), **kw)
        restart = self.restart_needed()
        options = [('finish', 'Finish', '', '')]
        if restart:
            options = [('restart', 'Restart now', 'Starts again with boot messages %s.' % (
                'hidden' if self.status.get('quiet_boot') == '1' else 'showing'), ''),
                ('finish', 'Finish', 'Restart later.', '')]
        return QuickSetupPage(self, i, heading='All set',
                              intro='You can change any of this in Settings, on the main menu.' +
                                    (' The boot messages change after a restart.' if restart else ''),
                              options=options, **kw)

    def restart_needed(self):
        return self.quiet_at_start is not None and self.status.get('quiet_boot') != self.quiet_at_start

    def pick(self, i, value):
        app = self.app
        name = self.pages[i]

        def next_page():
            self.show(i + 1)

        def apply(fn, key, new):
            def done(res, err):
                if err:
                    app.message("Couldn't save that", str(err))
                    return
                if key:
                    self.status[key] = new
                next_page()
            app.run_task(fn, done, busy='Saving…')

        st = self.status
        if name == 'welcome':
            if value == 'start':
                next_page()
            else:
                self.finish()
        elif name == 'speakers':
            if value == st.get('speakers'):
                next_page()
            else:
                self.speakers = value
                apply(lambda: app.settings.speakers(value), 'speakers', value)
        elif name == 'test':
            if value == 'play':
                play_speaker_test(app, self.speakers)
            else:
                next_page()
        elif name in ('quit', 'quiet', 'powersave', 'usb'):
            setting, key = {'quit': ('quit-on-shutdown', 'quit_on_shutdown'),
                            'quiet': ('quiet-boot', 'quiet_boot'),
                            'powersave': ('wifi-powersave', 'wifi_powersave'),
                            'usb': ('usb-handoff', 'usb_handoff')}[name]
            new = '1' if value == 'on' else '0'
            if st.get(key) == new:
                next_page()
            else:
                apply(lambda: app.settings.set(setting, value), key, new)
        elif name == 'done':
            self.finish()
            if value == 'restart':
                app.power('reboot')

    def finish(self):
        app = self.app
        try:
            app.settings.finish_quick_setup()
        except OSError:
            pass
        while isinstance(app.top, QuickSetupPage):
            app.pop()
        if self.then:
            self.then()
