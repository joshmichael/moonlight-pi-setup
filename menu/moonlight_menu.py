#!/usr/bin/env python3
"""Moonlight Pi menu: controllers (Bluetooth), Wi-Fi, settings and power, on the TV.

Shown by the auto-start loop in ~/.bash_profile when Moonlight closes (and at
boot when the Pi is offline, has no controller, or hasn't had its Quick setup
yet). Works with a controller, a keyboard or a mouse.

Exit codes: 0 = start Moonlight, 10 = command line, 20 = restarting/shutting
down. Anything else means the menu failed, and the loop falls back to the
plain prompt.

Installed by moonlight-pi-setup.
"""
import argparse
import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')

import ui  # noqa: E402
from ui import (W, MARGIN, CONTENT_Y, FOOTER_Y, HEADER_Y, BG, PANEL, ACCENT, TEXT, DIM, FAINT,  # noqa: E402
                GOOD, WARN, BAD, Item, ListScreen, Screen, Dialog, KeyboardScreen,
                draw_text, draw_wrapped, rrect, dot)
import settings  # noqa: E402
from settings_screens import QuickSetup, SettingsScreen  # noqa: E402

log = logging.getLogger('moonlight-menu')

EXIT_MOONLIGHT = 0
EXIT_SHELL = 10
EXIT_POWER = 20

PHONE_SERVICE = 'moonlight-wifi-setup.service'
PHONE_STATUS = '/run/moonlight-wifi-setup/status.json'
SHOT_PATH = '/tmp/moonlight-menu-shot.png'

REPEAT_DELAY = 0.40
REPEAT_RATE = 0.09


# ---------------------------------------------------------------------------
# Start-up decision (no display needed)
# ---------------------------------------------------------------------------
def _has_letter_keys(bits):
    value = 0
    for word in bits.split():
        value = (value << 64) | int(word, 16)
    return all((value >> k) & 1 for k in (16, 30, 44))   # KEY_Q, KEY_A, KEY_Z


def input_devices():
    """(gamepad connected, keyboard connected), from /proc/bus/input/devices."""
    gamepad = keyboard = False
    try:
        with open('/proc/bus/input/devices') as f:
            blocks = f.read().split('\n\n')
    except OSError:
        return False, False
    for block in blocks:
        handlers, keybits = '', ''
        for line in block.splitlines():
            if line.startswith('H: Handlers='):
                handlers = line[len('H: Handlers='):].split()
            elif line.startswith('B: KEY='):
                keybits = line[len('B: KEY='):]
        if any(h.startswith('js') for h in handlers):
            gamepad = True
        try:
            if 'kbd' in handlers and keybits and _has_letter_keys(keybits):
                keyboard = True
        except ValueError:
            pass
    return gamepad, keyboard


def boot_decision(net):
    """None to go straight to Moonlight, or why the menu should open instead."""
    if settings.state_get('quick_setup') == '1':
        return 'quick-setup'
    net.wait_startup(20)
    if not net.online():
        return 'offline'
    gamepad, keyboard = input_devices()
    if gamepad or keyboard:
        return None
    import bluetooth
    if bluetooth.paired_gamepad_count() > 0:
        return None
    return 'no-controller'


# ---------------------------------------------------------------------------
# Phone setup service control
# ---------------------------------------------------------------------------
class PhoneSetup:
    def start(self):
        p = subprocess.run(['systemctl', 'start', PHONE_SERVICE], capture_output=True, text=True, timeout=30)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip() or 'Could not start phone setup')

    def stop(self):
        subprocess.run(['systemctl', 'stop', PHONE_SERVICE], capture_output=True, timeout=60)

    def status(self, since):
        try:
            with open(PHONE_STATUS) as f:
                st = json.load(f)
        except (OSError, ValueError):
            return None
        return st if st.get('updated', 0) >= since else None


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------
def style_for(name):
    """Button style for a controller: 'ps', 'xbox', 'switch' or 'steam'. Anything
    else uses the Xbox style."""
    n = (name or '').lower()
    if any(w in n for w in ('ps3', 'ps4', 'ps5', 'dualsense', 'dualshock', 'sony', 'playstation')):
        return 'ps'
    if n == 'wireless controller':     # what Sony pads call themselves over Bluetooth
        return 'ps'
    if any(w in n for w in ('nintendo', 'switch', 'pro controller', 'joy-con', 'joycon')):
        return 'switch'
    if any(w in n for w in ('steam', 'valve')):
        return 'steam'
    return 'xbox'


class App:
    def __init__(self, args, reason=None):
        self.args = args
        self.reason = reason
        self.test = args.test_input is not None
        os.environ.setdefault('SDL_VIDEODRIVER', 'offscreen' if self.test else 'kmsdrm')
        os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
        import pygame
        from pygame._sdl2 import controller as sdl_controller
        self.pg = pygame
        self.sdl_controller = sdl_controller
        pygame.display.init()
        pygame.font.init()
        pygame.joystick.init()
        sdl_controller.init()
        self._init_display()
        pygame.key.set_repeat(400, 60)
        self.WAKE = pygame.event.custom_type()

        if args.fake:
            import fakes
            self.net, self.bt, self.phone = fakes.FakeNetwork(), fakes.FakeBluetooth(), fakes.FakePhoneSetup()
            self.settings = fakes.FakeSettings()
        else:
            import bluetooth
            import network
            self.net = network.Network()
            self.phone = PhoneSetup()
            self.settings = settings.Settings()
            try:
                self.bt = bluetooth.Bluetooth()
                if not self.bt.available():
                    self.bt = None
            except Exception as e:
                log.warning('Bluetooth unavailable: %s', e)
                self.bt = None

        self.net_status = None
        self.bt_devices = []
        self.bt_loaded = False
        self.stack = []
        self.dialog = None
        self.busy = None
        self.toast = None
        self.callbacks = queue.Queue()
        self.controllers = {}          # joystick instance id -> (controller, name)
        self.joysticks = {}            # non-controller joysticks
        self.holds = {}
        self.triggers = {}
        self.style = args.style or 'xbox'
        self.exit_code = None
        self.dirty = True
        self.stopping = threading.Event()
        self.poke = threading.Event()
        self.autopair = False
        self.autopair_paused = False
        self.fast_poll = False
        self.quiet_boot_at_start = None   # to tell when a change needs a restart
        self.restart_prompted = False
        self.shot_requested = False
        self.test_steps = []
        self.test_next = 0
        if self.test:
            self.test_steps = [s.strip() for s in args.test_input.split(',') if s.strip()]
        signal.signal(signal.SIGUSR1, self._on_sigusr1)

    # -- display ------------------------------------------------------------
    def _init_display(self):
        pg = self.pg
        if self.test:
            self.screen = self.canvas = pg.display.set_mode((W, ui.H))
            return
        self.canvas = None
        for kwargs in ({'vsync': 1}, {}):
            try:
                self.screen = pg.display.set_mode((W, ui.H), pg.FULLSCREEN | pg.SCALED, **kwargs)
                self.canvas = self.screen
                break
            except pg.error as e:
                log.warning('Scaled display unavailable (%s)', e)
        if self.canvas is None:
            self.screen = pg.display.set_mode((0, 0), pg.FULLSCREEN)
            self.canvas = pg.Surface((W, ui.H))
        pg.mouse.set_visible(False)

    def _to_canvas(self, pos):
        if self.canvas is self.screen:
            return pos
        sw, sh = self.screen.get_size()
        return (int(pos[0] * W / sw), int(pos[1] * ui.H / sh))

    def present(self):
        if self.canvas is not self.screen:
            self.pg.transform.smoothscale(self.canvas, self.screen.get_size(), self.screen)
        self.pg.display.flip()

    def _on_sigusr1(self, signum, frame):
        self.shot_requested = True
        self.wake()

    # -- threads and tasks --------------------------------------------------
    def wake(self):
        try:
            self.pg.event.post(self.pg.event.Event(self.WAKE))
        except Exception:
            pass

    def call_soon(self, fn):
        self.callbacks.put(fn)
        self.wake()

    def run_task(self, fn, done=None, busy=None):
        """Runs fn in a thread, then done(result, error) back on the UI thread."""
        if busy:
            self.busy = busy
            self.dirty = True

        def worker():
            try:
                res, err = fn(), None
            except Exception as e:
                log.info('task failed: %s', e)
                res, err = None, e
            self.call_soon(lambda: self._task_done(busy, done, res, err))
        threading.Thread(target=worker, daemon=True).start()

    def _task_done(self, busy, done, res, err):
        if busy:
            self.busy = None
        if done:
            done(res, err)
        elif err:
            self.message('Something went wrong', str(err))

    def poll_now(self):
        self.poke.set()

    def _poll_loop(self):
        while not self.stopping.is_set():
            try:
                st = self.net.status()
                self.call_soon(lambda st=st: self._set_net(st))
            except Exception as e:
                log.warning('network status: %s', e)
            if self.bt:
                try:
                    devs = self.bt.devices()
                    self.call_soon(lambda d=devs: self._set_bt(d))
                except Exception as e:
                    log.warning('bluetooth status: %s', e)
                self._autopair_step()
            self.poke.wait(1.0 if (self.fast_poll or self.autopair) else 2.5)
            self.poke.clear()

    def _set_net(self, st):
        self.net_status = st
        self.top.refresh()
        self.dirty = True

    def _set_bt(self, devs):
        self.bt_devices = devs
        self.bt_loaded = True
        self.top.refresh()
        self.dirty = True

    def _autopair_step(self):
        if not self.autopair or self.autopair_paused:
            return
        if self.controllers:
            self.autopair = False
            self.bt.stop_discovery()
            self.call_soon(lambda: self.top.refresh())
            return
        try:
            if not self.bt.discovering:
                self.bt.start_discovery()
            pads = self.bt.pairable_gamepads()
            if pads:
                pad = pads[0]
                self.call_soon(lambda: self.show_toast('Pairing with %s…' % pad['name']))
                self.bt.pair(pad['address'])
                self.autopair = False
                self.call_soon(lambda: (self.show_toast('%s connected' % pad['name'], GOOD), self.top.refresh()))
        except Exception as e:
            log.info('auto-pair: %s', e)

    # -- navigation and messages -------------------------------------------
    @property
    def top(self):
        return self.stack[-1]

    def push(self, screen):
        if self.stack:
            self.top.leave()
        self.stack.append(screen)
        screen.enter()
        screen.refresh()
        self.dirty = True

    def pop(self):
        if len(self.stack) <= 1:
            return
        screen = self.stack.pop()
        screen.leave()
        screen.close()
        self.top.enter()
        self.top.refresh()
        self.dirty = True

    def pop_to_home(self):
        while len(self.stack) > 1:
            self.pop()

    def show_toast(self, text, color=ACCENT, seconds=3.5):
        self.toast = (text, time.monotonic() + seconds, color)
        self.dirty = True

    def message(self, title, text):
        self.dialog = Dialog(title, text, ['OK'])
        self.dirty = True

    def confirm(self, title, text, yes, on_yes, danger=False, no='Cancel'):
        def closed(i):
            if i == 0:
                on_yes()
        self.dialog = Dialog(title, text, [yes, no], on_close=closed, danger=0 if danger else None)
        self.dirty = True

    def choose(self, title, text, options, on_choice):
        """Dialog with several buttons; on_choice(index) unless cancelled."""
        def closed(i):
            if i is not None and i >= 0:
                on_choice(i)
        self.dialog = Dialog(title, text, options, on_close=closed)
        self.dirty = True

    def _close_dialog(self, index):
        d, self.dialog = self.dialog, None
        if d.on_close:
            d.on_close(index if index >= 0 else None)
        self.dirty = True

    # -- actions ------------------------------------------------------------
    def online(self):
        st = self.net_status
        return bool(st and st.get('online'))

    def start_moonlight(self):
        if self.net_status is not None and not self.online():
            self.confirm('Not connected', "The Pi isn't connected to a network, so Moonlight won't "
                         "find your PC. Start Moonlight anyway?", 'Start', self._exit_moonlight)
        else:
            self._exit_moonlight()

    def _exit_moonlight(self):
        self.exit_code = EXIT_MOONLIGHT

    def command_line(self):
        self.exit_code = EXIT_SHELL

    def power(self, verb):
        def done(res, err):
            if err:
                self.message("Couldn't %s" % ('restart' if verb == 'reboot' else 'shut down'), str(err))
            else:
                self.busy = 'Restarting…' if verb == 'reboot' else 'Shutting down…'
                self.exit_code = EXIT_POWER

        def act():
            if self.args.fake:
                return
            p = subprocess.run(['systemctl', verb], capture_output=True, text=True, timeout=30)
            if p.returncode != 0:
                raise RuntimeError(p.stderr.strip() or 'systemctl failed')
        self.run_task(act, done)

    # -- input --------------------------------------------------------------
    def dispatch(self, action):
        self.dirty = True
        if self.busy:
            return
        if self.dialog:
            res = self.dialog.handle(action)
            if res is not None:
                self._close_dialog(res)
            return
        self.top.handle(action)

    def _press_dir(self, source, action):
        self.holds[source] = (action, time.monotonic() + REPEAT_DELAY, None)
        self.dispatch(action)

    def _press_button(self, source, action):
        """Press a button; held down, it repeats if the screen wants (delete on the keyboard)."""
        screen = self.top
        repeat = None if self.dialog or self.busy else screen.repeat_action(action)
        self.dispatch(action)
        if repeat:
            self.holds[source] = (repeat, time.monotonic() + REPEAT_DELAY, screen)

    def _release(self, source):
        self.holds.pop(source, None)

    def _repeat_holds(self):
        now = time.monotonic()
        for src, (action, t, screen) in list(self.holds.items()):
            if screen and (screen is not self.top or self.dialog):
                del self.holds[src]         # the screen it was repeating on has gone
            elif now >= t:
                self.holds[src] = (action, now + REPEAT_RATE, screen)
                self.dispatch(action)

    def _controller_input(self):
        self.pg.mouse.set_visible(False)

    def _add_controller(self, index):
        try:
            c = self.sdl_controller.Controller(index)
        except Exception as e:
            log.warning('controller %d: %s', index, e)
            return
        joy = c.as_joystick()
        name = getattr(c, 'name', None) or joy.get_name()
        self.controllers[joy.get_instance_id()] = (c, name)
        self.style = style_for(name)
        log.info('controller connected: %s', name)
        if self.autopair:
            self.autopair = False
            self.poll_now()
        self.top.refresh()
        self.dirty = True

    def _remove_controller(self, instance_id):
        self.controllers.pop(instance_id, None)
        for src in [s for s in self.holds if s[1] == instance_id]:
            self._release(src)
        for key in [k for k in self.triggers if k[0] == instance_id]:
            del self.triggers[key]
        self.top.refresh()
        self.dirty = True

    def _event(self, e):
        pg = self.pg
        t = e.type
        if t == pg.CONTROLLERDEVICEADDED:
            self._add_controller(e.device_index)
        elif t == pg.CONTROLLERDEVICEREMOVED:
            self._remove_controller(e.instance_id)
        elif t == pg.JOYDEVICEADDED:
            if not self.sdl_controller.is_controller(e.device_index):
                j = pg.joystick.Joystick(e.device_index)
                self.joysticks[j.get_instance_id()] = j
        elif t == pg.JOYDEVICEREMOVED:
            self.joysticks.pop(e.instance_id, None)
        elif t in (pg.CONTROLLERBUTTONDOWN, pg.CONTROLLERBUTTONUP):
            if e.instance_id in self.controllers:
                self._controller_input()
                self.style = style_for(self.controllers[e.instance_id][1])
                self._pad_button(e.instance_id, e.button, t == pg.CONTROLLERBUTTONDOWN)
        elif t == pg.CONTROLLERAXISMOTION:
            if e.instance_id not in self.controllers:
                return
            if e.axis in (pg.CONTROLLER_AXIS_LEFTX, pg.CONTROLLER_AXIS_LEFTY):
                style = style_for(self.controllers[e.instance_id][1])
                self._axis(('axis', e.instance_id, e.axis), e.axis == pg.CONTROLLER_AXIS_LEFTY,
                           e.value / 32768, style)
            elif e.axis in (pg.CONTROLLER_AXIS_TRIGGERLEFT, pg.CONTROLLER_AXIS_TRIGGERRIGHT):
                self._trigger(e.instance_id, e.axis, e.value / 32767)
        elif t in (pg.JOYBUTTONDOWN, pg.JOYHATMOTION, pg.JOYAXISMOTION, pg.JOYBUTTONUP):
            if e.instance_id in self.joysticks:
                self._generic_joystick(e)
        elif t == pg.KEYDOWN:
            self._key(e)
        elif t == pg.MOUSEMOTION:
            pg.mouse.set_visible(True)
            pos = self._to_canvas(e.pos)
            (self.dialog or self.top).hover(pos)
            self.dirty = True
        elif t == pg.MOUSEBUTTONDOWN:
            pg.mouse.set_visible(True)
            pos = self._to_canvas(e.pos)
            if self.busy:
                return
            if e.button == 1:
                if self.dialog:
                    i = self.dialog.click(pos)
                    if i is not None:
                        self._close_dialog(i)
                else:
                    self.top.click(pos)
            elif e.button == 3:
                self.dispatch('back')
            self.dirty = True
        elif t == pg.MOUSEWHEEL:
            if not self.dialog and not self.busy:
                self.top.wheel(e.y)
            self.dirty = True

    def _pad_button(self, jid, button, down):
        pg = self.pg
        dirs = {pg.CONTROLLER_BUTTON_DPAD_UP: 'up', pg.CONTROLLER_BUTTON_DPAD_DOWN: 'down',
                pg.CONTROLLER_BUTTON_DPAD_LEFT: 'left', pg.CONTROLLER_BUTTON_DPAD_RIGHT: 'right'}
        actions = {pg.CONTROLLER_BUTTON_A: 'select', pg.CONTROLLER_BUTTON_B: 'back',
                   pg.CONTROLLER_BUTTON_X: 'x', pg.CONTROLLER_BUTTON_Y: 'y',
                   pg.CONTROLLER_BUTTON_START: 'start', pg.CONTROLLER_BUTTON_BACK: 'view',
                   pg.CONTROLLER_BUTTON_LEFTSHOULDER: 'lb', pg.CONTROLLER_BUTTON_RIGHTSHOULDER: 'rb',
                   pg.CONTROLLER_BUTTON_LEFTSTICK: 'ls'}
        if button in dirs:
            src = ('btn', jid, button)
            if down:
                self._press_dir(src, dirs[button])
            else:
                self._release(src)
        elif button in actions:
            src = ('btn', jid, button)
            if down:
                self._press_button(src, actions[button])
            else:
                self._release(src)

    def _axis(self, src, vertical, v, style='xbox'):
        if abs(v) > 0.6:
            action = ('down' if v > 0 else 'up') if vertical else ('right' if v > 0 else 'left')
            if self.holds.get(src, (None,))[0] != action:
                self._controller_input()
                self.style = style
                self._release(src)
                self._press_dir(src, action)
        elif abs(v) < 0.35:
            self._release(src)

    def _trigger(self, jid, axis, v):
        """Triggers are analogue; treat a firm press as a button press."""
        key = (jid, axis)
        pressed = self.triggers.get(key, False)
        if v > 0.6 and not pressed:
            self.triggers[key] = True
            self._controller_input()
            self.style = style_for(self.controllers[jid][1])
            self._press_button(('trig', jid, axis), 'lt' if axis == self.pg.CONTROLLER_AXIS_TRIGGERLEFT else 'rt')
        elif v < 0.3 and pressed:
            self.triggers[key] = False
            self._release(('trig', jid, axis))

    def _generic_joystick(self, e):
        pg = self.pg
        jid = e.instance_id
        if e.type == pg.JOYHATMOTION:
            x, y = e.value
            for axis, val, neg, pos in (('hx', x, 'left', 'right'), ('hy', -y, 'up', 'down')):
                src = ('hat', jid, axis)
                if val:
                    self.style = 'xbox'
                    self._press_dir(src, pos if val > 0 else neg)
                else:
                    self._release(src)
        elif e.type == pg.JOYAXISMOTION and e.axis in (0, 1):
            self._axis(('jaxis', jid, e.axis), e.axis == 1, e.value)
        elif e.type == pg.JOYBUTTONDOWN:
            self._controller_input()
            self.style = 'xbox'
            action = {0: 'select', 1: 'back', 2: 'x', 3: 'y'}.get(e.button, 'start' if e.button >= 7 else None)
            if action:
                self._press_button(('jbtn', jid, e.button), action)
        elif e.type == pg.JOYBUTTONUP:
            self._release(('jbtn', jid, e.button))

    def _key(self, e):
        pg = self.pg
        self.style = 'keyboard'
        pg.mouse.set_visible(False)
        on_keyboard = isinstance(self.top, KeyboardScreen) and not self.dialog
        if on_keyboard:
            # Typing goes into the field; only the arrows, Enter, Backspace and Esc
            # do anything else (move around the keys, press the highlighted key,
            # delete, go back).
            keys = {pg.K_UP: 'up', pg.K_DOWN: 'down', pg.K_LEFT: 'left', pg.K_RIGHT: 'right',
                    pg.K_RETURN: 'enter', pg.K_KP_ENTER: 'enter', pg.K_BACKSPACE: 'backspace',
                    pg.K_ESCAPE: 'escape'}
            if e.key in keys:
                self.dispatch(keys[e.key])
            elif e.unicode and e.unicode.isprintable():
                self.dispatch(('char', e.unicode))
            return
        keys = {pg.K_UP: 'up', pg.K_DOWN: 'down', pg.K_LEFT: 'left', pg.K_RIGHT: 'right',
                pg.K_RETURN: 'enter', pg.K_KP_ENTER: 'enter', pg.K_ESCAPE: 'escape',
                pg.K_BACKSPACE: 'backspace', pg.K_SPACE: 'select'}
        if e.key == pg.K_TAB:
            self.dispatch('up' if e.mod & pg.KMOD_SHIFT else 'down')
        elif e.key in keys:
            self.dispatch(keys[e.key])

    # -- drawing ------------------------------------------------------------
    def header_status(self):
        st = self.net_status
        if st is None:
            return FAINT, 'Checking network…'
        if st.get('hotspot'):
            return WARN, 'Phone setup'
        w, eth = st.get('wifi'), st.get('ethernet')
        if eth and eth['connected']:
            return GOOD, 'Ethernet'
        if w and w['connected']:
            return GOOD, 'Wi-Fi: %s' % w['ssid']
        return BAD, 'Not connected'

    def draw(self):
        pg = self.pg
        c = self.canvas
        c.fill(BG)
        top = self.top
        color, label = self.header_status()
        r = draw_text(c, time.strftime('%H:%M'), (W - MARGIN, HEADER_Y + 36), 36, DIM, anchor='midright')
        r = draw_text(c, label, (r.left - 48, HEADER_Y + 36), 32, TEXT, anchor='midright', max_width=600)
        dot(c, color, (r.left - 22, r.centery), 9)
        # Long titles get a smaller font before they get cut short.
        room = r.left - 22 - 48 - MARGIN
        size = next((s for s in (60, 54, 48) if ui.font(s, True).size(top.title)[0] <= room), 48)
        draw_text(c, top.title, (MARGIN, HEADER_Y + 36), size, TEXT, bold=True, anchor='midleft', max_width=room)
        top.draw(c)
        pg.draw.line(c, PANEL, (MARGIN, FOOTER_Y - 34), (W - MARGIN, FOOTER_Y - 34), 2)
        hints = [('A', 'OK'), ('B', 'Cancel')] if self.dialog else top.hints()
        if not self.busy:
            ui.draw_hints(c, hints, self.style)
        if self.toast:
            text, until, tcolor = self.toast
            if time.monotonic() > until:
                self.toast = None
            else:
                f = ui.font(32, True)
                w = f.size(text)[0] + 80
                box = pg.Rect((W - w) // 2, FOOTER_Y - 130, w, 70)
                rrect(c, tcolor, box, 35)
                draw_text(c, text, box.center, 32, (255, 255, 255), bold=True, anchor='center')
        if self.dialog:
            self.dialog.draw(c)
        if self.busy:
            ui.shade(c, 185)
            ui.spinner(c, (W // 2, ui.H // 2 - 50), 44, ACCENT, 9)
            draw_text(c, self.busy, (W // 2, ui.H // 2 + 50), 40, TEXT, anchor='center')

    @property
    def animating(self):
        return bool(self.busy or self.toast or self.holds or getattr(self.top, 'animating', False))

    # -- main loop ----------------------------------------------------------
    def _offline_screens(self, after_setup=False):
        """At boot without a network, go straight to Wi-Fi setup from a phone."""
        if not self.net.wifi_dev:
            return
        if self.reason == 'offline' or (after_setup and self.net_status is not None and not self.online()):
            self.push(NetworkScreen(self))
            self.push(PhoneSetupScreen(self))

    def run(self):
        pg = self.pg
        self.push(HomeScreen(self))
        if self.settings.quick_setup_pending():
            QuickSetup(self, then=lambda: self._offline_screens(after_setup=True)).start()
        else:
            self._offline_screens()
        threading.Thread(target=self._poll_loop, daemon=True).start()
        started = time.monotonic()
        autopair_checked = False
        fps_log = [] if os.environ.get('MENU_DEBUG_FPS') else None   # logs the frame rate
        clock = pg.time.Clock()
        try:
            while self.exit_code is None:
                if self.animating:
                    # 60 fps while something moves (spinners). Don't block waiting for
                    # events as well: on top of drawing and the display's vsync, that
                    # would miss every other frame.
                    clock.tick(60)
                    events = pg.event.get()
                else:
                    events = [pg.event.wait(250)] + pg.event.get()
                for ev in events:
                    if ev.type != pg.NOEVENT:
                        self._event(ev)
                        if ev.type != self.WAKE:
                            self.dirty = True
                self._repeat_holds()
                while not self.callbacks.empty():
                    self.callbacks.get()()
                    self.dirty = True
                # Give already-paired controllers a moment to show up first.
                if not autopair_checked and self.bt_loaded and time.monotonic() - started > 1.5:
                    autopair_checked = True
                    self._check_autopair()
                if hasattr(self.top, 'tick'):
                    if self.top.tick():
                        self.dirty = True
                if self.test:
                    self._test_step()
                if self.dirty or self.animating:
                    self.draw()
                    self.present()
                    self.dirty = False
                    if fps_log is not None:
                        fps_log.append(time.monotonic())
                        if fps_log[-1] - fps_log[0] >= 3:
                            log.info('frames: %.1f per second', (len(fps_log) - 1) / (fps_log[-1] - fps_log[0]))
                            del fps_log[:-1]
                if self.shot_requested:
                    self.shot_requested = False
                    pg.image.save(self.canvas, SHOT_PATH)
        finally:
            self.stopping.set()
            self.poke.set()
            for s in reversed(self.stack):
                try:
                    s.leave()
                    s.close()
                except Exception:
                    log.exception('closing %s', type(s).__name__)
            if self.bt:
                try:
                    self.bt.close()
                except Exception:
                    pass
            pg.quit()
        return self.exit_code

    def _check_autopair(self):
        """With no controller connected and none paired, pair the first one put in pairing mode."""
        if not self.bt or self.controllers:
            return
        if not any(d['paired'] and d['bonded'] and d['gamepad'] for d in self.bt_devices):
            self.autopair = True
            self.poll_now()
            self.top.refresh()

    def _test_step(self):
        if self.test_next > time.monotonic():
            return
        if not self.test_steps:
            self.exit_code = 99
            return
        if self.busy and not self.test_steps[0].startswith(('shot:', 'wait:')):
            return
        step = self.test_steps.pop(0)
        log.info('test step: %s (screen: %s)', step, type(self.top).__name__)
        delay = 0.25
        if step.startswith('wait:'):
            delay = float(step[5:])
        elif step.startswith('shot:'):
            self.draw()
            path = os.path.join(self.args.shots, step[5:] + '.png')
            self.pg.image.save(self.canvas, path)
            log.info('saved %s', path)
        elif step.startswith('char:'):
            for ch in step[5:]:
                self.dispatch(('char', ch))
        elif step.startswith('mods:'):          # e.g. mods:shift, mods:none
            self.pg.key.set_mods(self.pg.KMOD_LSHIFT if step[5:] == 'shift' else 0)
        elif step.startswith('hold:'):          # hold a button down until 'release'
            self._press_button(('test', None), step[5:])
        elif step == 'release':
            self._release(('test', None))
        elif step == 'quit':
            self.exit_code = 99
        else:
            self.dispatch(step)
        self.test_next = time.monotonic() + delay


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------
def net_summary(st):
    if st is None:
        return ''
    if st.get('ethernet') and st['ethernet']['connected']:
        return 'Ethernet'
    w = st.get('wifi')
    if w and w['connected']:
        return w['ssid']
    if st.get('hotspot'):
        return 'Phone setup'
    return 'Not connected'


class HomeScreen(ListScreen):
    title = 'Moonlight'

    def refresh(self):
        app = self.app
        pads = len(app.controllers)
        self.list.set_items([
            Item('Start Moonlight', app.start_moonlight, key='start'),
            Item('Controllers', lambda: app.push(ControllersScreen(app)), key='ctl',
                 detail='%d connected' % pads if pads else 'None connected',
                 detail_color=GOOD if pads else WARN),
            Item('Wi-Fi & network', lambda: app.push(NetworkScreen(app)), key='net',
                 detail=net_summary(app.net_status),
                 detail_color=GOOD if app.online() else WARN),
            Item('Settings', lambda: app.push(SettingsScreen(app)), key='settings'),
            Item('Restart or shut down', lambda: app.push(PowerScreen(app)), key='power'),
            Item('Command line', self._shell, key='shell'),
        ])

    def _shell(self):
        self.app.confirm('Command line', 'Leave the menu for a text command line? You will need a keyboard. '
                         'Type exit to come back.', 'Command line', self.app.command_line)

    def handle(self, action):
        if action == 'start':
            self.app.start_moonlight()
        elif action in ('back', 'escape', 'backspace'):
            pass
        else:
            super().handle(action)

    def hints(self):
        return [('A', 'Select'), ('START', 'Start Moonlight')]

    def draw_panel(self, surf, rect):
        app = self.app
        y = rect.y
        draw_text(surf, 'Network', (rect.x, y), 30, FAINT, bold=True)
        y += 50
        for color, line in network_lines(app.net_status, short=True):
            dot(surf, color, (rect.x + 10, y + 21), 8)
            draw_text(surf, line, (rect.x + 36, y), 32, TEXT, max_width=rect.width - 36)
            y += 48
        y += 30
        draw_text(surf, 'Controllers', (rect.x, y), 30, FAINT, bold=True)
        y += 50
        names = [n for _, n in app.controllers.values()]
        if not names:
            draw_text(surf, 'None connected', (rect.x, y), 32, DIM)
            y += 48
        for n in names[:4]:
            dot(surf, GOOD, (rect.x + 10, y + 21), 8)
            draw_text(surf, n, (rect.x + 36, y), 32, TEXT, max_width=rect.width - 36)
            y += 48
        if app.autopair:
            y += 24
            ui.pairing_box(surf, ui.pygame.Rect(rect.x - 16, y, rect.width + 32, rect.bottom - y + 16))

    @property
    def animating(self):
        return self.app.autopair


def network_lines(st, short=False):
    """(colour, text) lines describing the network state."""
    if st is None:
        return [(FAINT, 'Checking…')]
    lines = []
    eth, w = st.get('ethernet'), st.get('wifi')
    if eth:
        if eth['connected']:
            lines.append((GOOD, 'Ethernet: %s' % (eth['ip'] or 'connected')))
        elif not short:
            lines.append((FAINT, 'Ethernet: not plugged in'))
    if st.get('hotspot'):
        lines.append((WARN, 'Wi-Fi: phone setup running'))
    elif w:
        if w['connected']:
            lines.append((GOOD, 'Wi-Fi: %s (%d%%)' % (w['ssid'], w['signal'])))
            if w['ip'] and not short:
                lines.append((GOOD, 'Wi-Fi address: %s' % w['ip']))
        elif not w['enabled']:
            lines.append((FAINT, 'Wi-Fi: off'))
        else:
            lines.append((WARN, 'Wi-Fi: not connected'))
    if not st.get('online'):
        lines.append((BAD, 'Not connected to a network'))
        return lines
    if not short:
        internet = st.get('internet')
        if internet in ('none', 'limited'):
            lines.append((WARN, 'No internet (home network only)'))
        elif internet == 'portal':
            lines.append((WARN, 'This network needs you to sign in'))
        if st.get('tailscale'):
            lines.append((GOOD, 'Tailscale: %s' % st['tailscale']))
    return lines


class ControllersScreen(ListScreen):
    title = 'Controllers'

    def refresh(self):
        app = self.app
        if not app.bt:
            self.list.set_items([Item("Bluetooth isn't available on this Pi", selectable=False)])
            return
        items = [Item('Pair a new controller', lambda: app.push(PairScreen(app)), key='pair')]
        paired = [d for d in app.bt_devices if d['paired']]
        paired.sort(key=lambda d: (not d['gamepad'], not d['connected'], d['name'].lower()))
        for d in paired:
            if not d['bonded']:
                detail, color = 'Pair again', WARN
            elif d['connected']:
                detail = 'Connected' + (' · %d%%' % d['battery'] if d['battery'] is not None else '')
                color = GOOD
            else:
                detail, color = 'Not connected', None
            items.append(Item(d['name'], lambda d=d: app.push(DeviceScreen(app, d['address'])),
                              detail=detail, key=d['address'], detail_color=color))
        self.list.set_items(items)

    def draw_panel(self, surf, rect):
        y = rect.y
        draw_text(surf, 'Connected now', (rect.x, y), 30, FAINT, bold=True)
        y += 50
        names = [n for _, n in self.app.controllers.values()]
        if not names:
            draw_text(surf, 'None', (rect.x, y), 32, DIM)
            y += 48
        for n in names[:5]:
            dot(surf, GOOD, (rect.x + 10, y + 21), 8)
            draw_text(surf, n, (rect.x + 36, y), 32, TEXT, max_width=rect.width - 36)
            y += 48
        y += 30
        draw_wrapped(surf, 'Bluetooth controllers you pair here reconnect by themselves when you switch '
                     'them on. Controllers plugged in with a USB cable work without pairing.',
                     (rect.x, y), rect.width, 30)


class PairScreen(ListScreen):
    title = 'Pair a controller'

    def __init__(self, app):
        super().__init__(app)
        self.auto_tried = False

    def enter(self):
        app = self.app
        app.autopair_paused = True
        app.fast_poll = True
        app.run_task(app.bt.start_discovery, self._started)
        app.poll_now()

    def _started(self, res, err):
        if err:
            self.app.message("Couldn't search for controllers", str(err))

    def leave(self):
        app = self.app
        app.autopair_paused = False
        app.fast_poll = False
        app.run_task(app.bt.stop_discovery, lambda r, e: None)

    def refresh(self):
        app = self.app
        found = [d for d in app.bt_devices
                 if d['gamepad'] and (not d['paired'] or not d['bonded']) and d['rssi'] is not None]
        if not found:
            self.list.set_items([Item('Looking for controllers…', selectable=False, spinner=True)])
            return
        self.list.set_items([Item(d['name'], lambda d=d: self._pair(d), key=d['address'],
                                  detail='Ready to pair') for d in found])
        # With no controller connected there's nothing to select it with (for
        # example, the controller being paired was just unplugged), so pair the
        # first one found.
        if not app.controllers and not app.busy and not app.dialog and not self.auto_tried:
            self.auto_tried = True
            d = found[0]
            app.call_soon(lambda: self._pair(d) if app.top is self else None)

    def _pair(self, d):
        app = self.app

        def done(res, err):
            if err:
                app.message("Couldn't pair %s" % d['name'], str(err))
            else:
                app.show_toast('%s connected' % d['name'], GOOD)
                app.pop()
            app.poll_now()
        app.run_task(lambda: app.bt.pair(d['address']), done, busy='Pairing with %s…' % d['name'])

    def draw_panel(self, surf, rect):
        y = draw_wrapped(surf, 'Put it in pairing mode', (rect.x, rect.y), rect.width, 32, TEXT, bold=True) + 16
        for head, body in (
                ('PlayStation (DualSense, DualShock 4)',
                 'Hold Create (Share) and the PS button until the light flashes.'),
                ('Xbox', 'Switch it on, then hold the small pair button on top until the Xbox button '
                         'flashes quickly.'),
                ('Nintendo Switch Pro', 'Hold the small sync button on top.'),
                ('Others', "See the controller's manual for its Bluetooth pairing mode.")):
            y = draw_text(surf, head, (rect.x, y), 28, ACCENT, bold=True).bottom + 6
            y = draw_wrapped(surf, body, (rect.x, y), rect.width, 28) + 18
        draw_wrapped(surf, 'Select it when it appears. With no other controller connected, it pairs by '
                     'itself.', (rect.x, y + 6), rect.width, 28, FAINT)


class DeviceScreen(ListScreen):
    def __init__(self, app, address):
        super().__init__(app)
        self.address = address
        self.title = 'Controller'

    def device(self):
        for d in self.app.bt_devices:
            if d['address'] == self.address:
                return d
        return None

    def refresh(self):
        d = self.device()
        if d is None or not d['paired']:
            self.app.call_soon(self._gone)
            return
        self.title = d['name']
        items = []
        if not d['bonded']:
            items.append(Item('Pair again', self._repair, key='repair', color=WARN))
        elif d['connected']:
            items.append(Item('Disconnect', self._disconnect, key='conn'))
        else:
            items.append(Item('Connect', self._connect, key='conn'))
        items.append(Item('Forget this controller', self._forget, key='forget', color=BAD))
        self.list.set_items(items)

    def _gone(self):
        if self.app.top is self:
            self.app.pop()

    def _run(self, fn, busy, ok):
        app = self.app

        def done(res, err):
            if err:
                app.message('Something went wrong', str(err))
            elif ok:
                app.show_toast(ok, GOOD)
            app.poll_now()
        app.run_task(fn, done, busy)

    def _connect(self):
        d = self.device()
        self._run(lambda: self.app.bt.connect(self.address), 'Connecting to %s…' % d['name'],
                  '%s connected' % d['name'])

    def _disconnect(self):
        self._run(lambda: self.app.bt.disconnect(self.address), 'Disconnecting…', None)

    def _repair(self):
        """Forget the broken pairing, then go straight to pairing."""
        app = self.app

        def done(res, err):
            if err:
                app.message('Something went wrong', str(err))
                return
            app.pop()
            app.push(PairScreen(app))
            app.poll_now()
        app.run_task(lambda: app.bt.forget(self.address), done, 'Removing the old pairing…')

    def _forget(self):
        d = self.device()
        self.app.confirm('Forget %s?' % d['name'], 'You will need to pair it again to use it over Bluetooth.',
                         'Forget', lambda: self._run(lambda: self.app.bt.forget(self.address),
                                                     'Forgetting…', '%s forgotten' % d['name']),
                         danger=True)

    def draw_panel(self, surf, rect):
        d = self.device()
        if not d:
            return
        y = rect.y
        if not d['bonded']:
            draw_wrapped(surf, "The Pi didn't save this controller's pairing, so it can't reconnect. Choose "
                         'Pair again, then put the controller in pairing mode.', (rect.x, y), rect.width, 30, WARN)
            return
        for label, value, color in (
                ('Status', 'Connected' if d['connected'] else 'Not connected', GOOD if d['connected'] else DIM),
                ('Battery', '%d%%' % d['battery'] if d['battery'] is not None else 'Unknown', TEXT),
                ('Address', d['address'], TEXT)):
            draw_text(surf, label, (rect.x, y), 28, FAINT, bold=True)
            draw_text(surf, value, (rect.x, y + 38), 34, color)
            y += 110
        draw_wrapped(surf, 'It reconnects by itself when you switch it on.', (rect.x, y), rect.width, 28)


class NetworkScreen(ListScreen):
    title = 'Wi-Fi & network'

    def refresh(self):
        app = self.app
        st = app.net_status
        if not app.net.wifi_dev:
            self.list.set_items([Item('This Pi has no Wi-Fi', selectable=False)])
            return
        w = (st or {}).get('wifi') or {}
        enabled = w.get('enabled', True)
        items = [Item('Wi-Fi', self._toggle, key='toggle', detail='On' if enabled else 'Off',
                      detail_color=GOOD if enabled else None)]
        if enabled:
            items.append(Item('Choose a Wi-Fi network', lambda: app.push(WifiListScreen(app)), key='choose'))
        items.append(Item('Set up Wi-Fi from your phone', lambda: app.push(PhoneSetupScreen(app)), key='phone'))
        items.append(Item('Saved Wi-Fi networks', lambda: app.push(SavedScreen(app)), key='saved'))
        if w.get('connected'):
            items.append(Item('Disconnect from %s' % w['ssid'], self._disconnect, key='disc'))
        self.list.set_items(items)

    def _toggle(self):
        app = self.app
        st = app.net_status or {}
        on = not ((st.get('wifi') or {}).get('enabled', True))

        def done(res, err):
            if err:
                app.message("Couldn't change Wi-Fi", str(err))
            app.poll_now()
        app.run_task(lambda: app.net.set_wifi_enabled(on), done,
                     busy='Turning Wi-Fi on…' if on else 'Turning Wi-Fi off…')

    def _disconnect(self):
        app = self.app
        uid = app.net.active_wifi_uuid()
        if not uid:
            return
        app.run_task(lambda: app.net.disconnect(uid), lambda r, e: (e and app.message('Something went wrong', str(e)),
                                                                     app.poll_now()), busy='Disconnecting…')

    def draw_panel(self, surf, rect):
        y = rect.y
        for color, line in network_lines(self.app.net_status):
            dot(surf, color, (rect.x + 10, y + 21), 8)
            draw_text(surf, line, (rect.x + 36, y), 32, TEXT, max_width=rect.width - 36)
            y += 52


class WifiListScreen(ListScreen):
    title = 'Choose a Wi-Fi network'

    def __init__(self, app):
        super().__init__(app)
        self.networks = None
        self.error = ''

    def enter(self):
        if self.networks is None:
            self.scan()

    def scan(self):
        self.networks = None
        self.error = ''
        self.refresh()
        self.app.run_task(lambda: self.app.net.scan(True), self._scanned)

    def _scanned(self, res, err):
        if err:
            self.error = str(err)
            self.networks = []
        else:
            self.networks = res
        self.refresh()

    def refresh(self):
        if self.networks is None:
            self.list.set_items([Item('Looking for networks…', selectable=False, spinner=True)])
            return
        items = []
        for n in self.networks:
            detail = 'Connected' if n['in_use'] else ('Saved' if n['saved'] else '')
            items.append(Item(n['ssid'], lambda n=n: self.choose(n), detail=detail, key=n['ssid'],
                              detail_color=GOOD if n['in_use'] else None, signal=n['signal'],
                              locked=bool(n['security'])))
        if not self.networks:
            items.append(Item(self.error or 'No networks found', selectable=False))
        items.append(Item('Other (hidden) network…', self.other, key='other'))
        items.append(Item('Search again', self.scan, key='rescan'))
        self.list.set_items(items)

    def choose(self, n):
        app = self.app
        import network
        if n['in_use']:
            uid = app.net.active_wifi_uuid()
            app.choose(n['ssid'], 'You are connected to this network.', ['Disconnect', 'Forget', 'Cancel'],
                       lambda i: self._connected_action(i, uid, n))
            return
        if n['saved']:
            uid = next((s['uuid'] for s in app.net.saved() if s['ssid'] == n['ssid']), None)
            if uid:
                self._connect(lambda: app.net.connect_saved(uid), n['ssid'], retry=lambda: self._ask_password(n))
                return
        try:
            km = network.key_mgmt_for(n['security'])
        except network.NetworkError as e:
            app.message("Can't connect", str(e))
            return
        if km is None:
            app.confirm('Open network', '"%s" has no password, so others nearby could see your traffic. '
                        'Connect anyway?' % n['ssid'], 'Connect',
                        lambda: self._connect(lambda: app.net.connect_new(n['ssid'], '', ''), n['ssid']))
        else:
            self._ask_password(n)

    def _ask_password(self, n, initial=''):
        import network
        app = self.app

        def done(pw):
            if pw is not None:
                self._connect(lambda: app.net.connect_new(n['ssid'], pw, n['security']), n['ssid'],
                              retry=lambda: self._ask_password(n, pw))
        app.push(KeyboardScreen(app, 'Wi-Fi password', 'Password for "%s"' % n['ssid'], done, secret=True,
                                initial=initial, validate=lambda t: network.validate_password(n['security'], t)))

    def _connect(self, fn, ssid, retry=None):
        app = self.app

        def done(res, err):
            app.poll_now()
            if err:
                if retry:
                    app.choose("Couldn't connect to %s" % ssid, str(err), ['Try again', 'Cancel'],
                               lambda i: i == 0 and retry())
                else:
                    app.message("Couldn't connect to %s" % ssid, str(err))
            else:
                app.show_toast('Connected to %s' % ssid, GOOD)
                if app.top is self:
                    app.pop()
        app.run_task(fn, done, busy='Connecting to %s…' % ssid)

    def _connected_action(self, i, uid, n):
        app = self.app
        if uid is None or i == 2:
            return
        if i == 0:
            app.run_task(lambda: app.net.disconnect(uid), lambda r, e: (app.poll_now(), self.scan()),
                         busy='Disconnecting…')
        elif i == 1:
            app.confirm('Forget %s?' % n['ssid'], "The Pi won't join it again until you enter the password.",
                        'Forget', lambda: app.run_task(lambda: app.net.forget(uid),
                                                       lambda r, e: (app.poll_now(), self.scan()),
                                                       busy='Forgetting…'), danger=True)

    def other(self):
        app = self.app

        def got_ssid(ssid):
            if not ssid:
                return
            app.choose('Password', 'Does "%s" have a password?' % ssid, ['Yes', 'No'],
                       lambda i: got_kind(ssid, i == 0))

        def got_kind(ssid, secured):
            if not secured:
                self._connect(lambda: app.net.connect_new(ssid, '', '', hidden=True), ssid)
                return
            n = {'ssid': ssid, 'security': 'WPA2'}
            import network

            def done(pw):
                if pw is not None:
                    self._connect(lambda: app.net.connect_new(ssid, pw, 'WPA2', hidden=True), ssid)
            app.push(KeyboardScreen(app, 'Wi-Fi password', 'Password for "%s"' % ssid, done, secret=True,
                                    validate=lambda t: network.validate_password(n['security'], t)))
        app.push(KeyboardScreen(app, 'Network name', 'Type the network name (it is case-sensitive)',
                                got_ssid, max_len=32, validate=lambda t: '' if t else 'Type a name first.'))

    def hints(self):
        return [('A', 'Select'), ('B', 'Back')]

    def draw_panel(self, surf, rect):
        y = draw_text(surf, 'Tips', (rect.x, rect.y), 30, FAINT, bold=True).bottom + 18
        y = draw_wrapped(surf, 'Networks the Pi has joined before reconnect by themselves.', (rect.x, y),
                         rect.width, 30) + 20
        y = draw_wrapped(surf, 'Typing a long password with a controller? Use "Set up Wi-Fi from your phone" '
                         'on the previous screen instead.', (rect.x, y), rect.width, 30) + 20
        draw_wrapped(surf, 'For streaming, a wired Ethernet connection is best.', (rect.x, y), rect.width, 30, FAINT)


class SavedScreen(ListScreen):
    title = 'Saved Wi-Fi networks'

    def __init__(self, app):
        super().__init__(app)
        self.saved = None
        self.active = None

    def enter(self):
        self.load()

    def load(self):
        app = self.app

        def work():
            return app.net.saved(), app.net.active_wifi_uuid()

        def done(res, err):
            self.saved, self.active = res if res else ([], None)
            self.refresh()
        app.run_task(work, done)

    def refresh(self):
        if self.saved is None:
            self.list.set_items([Item('Loading…', selectable=False, spinner=True)])
            return
        if not self.saved:
            self.list.set_items([Item('No saved networks', selectable=False)])
            return
        self.list.set_items([Item(s['ssid'], lambda s=s: self.choose(s), key=s['uuid'],
                                  detail='Connected' if s['uuid'] == self.active else '',
                                  detail_color=GOOD) for s in self.saved])

    def choose(self, s):
        app = self.app
        connected = s['uuid'] == self.active

        def act(i):
            if i == 0 and not connected:
                app.run_task(lambda: app.net.connect_saved(s['uuid']),
                             lambda r, e: (app.message("Couldn't connect", str(e)) if e else
                                           app.show_toast('Connected to %s' % s['ssid'], GOOD),
                                           app.poll_now(), self.load()),
                             busy='Connecting to %s…' % s['ssid'])
            elif i == (0 if connected else 1):
                app.confirm('Forget %s?' % s['ssid'], "The Pi won't join it again until you enter the password.",
                            'Forget', lambda: app.run_task(lambda: app.net.forget(s['uuid']),
                                                           lambda r, e: (app.poll_now(), self.load()),
                                                           busy='Forgetting…'), danger=True)
        options = (['Forget', 'Cancel'] if connected else ['Connect', 'Forget', 'Cancel'])
        app.choose(s['ssid'], 'Connected now.' if connected else 'Saved network.', options, act)

    def draw_panel(self, surf, rect):
        draw_wrapped(surf, 'The Pi joins these networks by itself when they are in range. Forget one to stop '
                     'that.', (rect.x, rect.y), rect.width, 30)


class PhoneSetupScreen(Screen):
    title = 'Set up Wi-Fi from your phone'

    @property
    def animating(self):
        """Only while the spinner shows (starting or connecting)."""
        return not self.error and (self.status or {}).get('state') in (None, 'starting', 'connecting')

    def __init__(self, app):
        super().__init__(app)
        self.since = time.time() - 1
        self.status = None
        self.error = ''
        self.last_read = 0
        self.qr = None
        self.qr_key = None
        self.url_qr = None
        self.done_at = None
        self.stopped = False

    def enter(self):
        if self.stopped:
            return
        self.app.run_task(self.app.phone.start, self._started)

    def _started(self, res, err):
        if err:
            self.error = str(err)

    def close(self):
        self._stop()

    def _stop(self):
        if not self.stopped:
            self.stopped = True
            self.app.run_task(self.app.phone.stop, lambda r, e: self.app.poll_now())

    def tick(self):
        now = time.monotonic()
        if now - self.last_read < 0.5:
            return False
        self.last_read = now
        st = self.app.phone.status(self.since)
        if st != self.status:
            self.status = st
            if st and st.get('state') == 'connected' and self.done_at is None:
                self.done_at = now
                self.stopped = True     # the service stops itself
                self.app.poll_now()
            return True
        if self.done_at and now - self.done_at > 3:
            ssid = (self.status or {}).get('ssid', '')
            self.app.pop_to_home()
            self.app.show_toast('Connected to %s' % ssid, GOOD, 5)
        return False

    def handle(self, action):
        st = (self.status or {}).get('state')
        if action in ('back', 'escape', 'backspace'):
            if st == 'connected':
                self.app.pop_to_home()
                return
            self.app.confirm('Stop phone setup?', 'The setup network will be switched off.', 'Stop',
                             self._leave)
        elif action in ('select', 'enter') and (self.error or st in ('error', 'stopped')):
            self.error = ''
            self.status = None
            self.since = time.time() - 1
            self.stopped = False
            self.app.run_task(self.app.phone.start, self._started)

    def _leave(self):
        self._stop()
        self.app.pop()

    def hints(self):
        st = (self.status or {}).get('state')
        if self.error or st in ('error', 'stopped'):
            return [('A', 'Try again'), ('B', 'Back')]
        return [('B', 'Stop')]

    def draw(self, surf):
        pg = ui.pygame
        st = self.status or {}
        state = st.get('state')
        left = pg.Rect(MARGIN, CONTENT_Y, 640, 640)
        right_x = MARGIN + 720
        width = W - MARGIN - right_x
        if self.error or state in ('error', 'stopped'):
            msg = self.error or st.get('message') or 'Phone setup stopped.'
            draw_text(surf, 'Phone setup isn\'t running', (right_x, CONTENT_Y), 44, TEXT, bold=True)
            draw_wrapped(surf, msg, (right_x, CONTENT_Y + 80), width, 32, BAD)
            return
        if state in (None, 'starting'):
            ui.spinner(surf, left.center, 50, ACCENT, 10)
            draw_text(surf, 'Starting the setup network…', (right_x, CONTENT_Y), 44, TEXT, bold=True)
            draw_wrapped(surf, 'This takes a few seconds. The Pi first looks for nearby networks, then '
                         'starts its own.', (right_x, CONTENT_Y + 80), width, 32)
            return
        if state == 'connecting':
            ui.spinner(surf, left.center, 50, ACCENT, 10)
            draw_text(surf, 'Connecting to %s…' % st.get('ssid', ''), (right_x, CONTENT_Y), 44, TEXT,
                      bold=True, max_width=width)
            draw_wrapped(surf, 'Your phone will disconnect from the setup network. That is normal.',
                         (right_x, CONTENT_Y + 80), width, 32)
            return
        if state == 'connected':
            pg.draw.circle(surf, GOOD, left.center, 120)
            pg.draw.lines(surf, (255, 255, 255), False, [(left.centerx - 55, left.centery),
                                                         (left.centerx - 15, left.centery + 45),
                                                         (left.centerx + 60, left.centery - 45)], 18)
            draw_text(surf, 'Connected to %s' % st.get('ssid', ''), (right_x, CONTENT_Y), 44, TEXT, bold=True,
                      max_width=width)
            draw_wrapped(surf, 'You can put your phone away.', (right_x, CONTENT_Y + 80), width, 32)
            return
        # ready (or failed and back to ready)
        ssid, pw, url = st.get('hotspot_ssid', ''), st.get('hotspot_password', ''), st.get('url', '')
        if self.qr_key != (ssid, pw, url):
            self.qr_key = (ssid, pw, url)
            self.qr = ui.qr_surface(ui.wifi_qr_text(ssid, pw), 560)
            self.url_qr = ui.qr_surface(url, 220) if url else None
        rrect(surf, (255, 255, 255), left, 24)
        if self.qr:
            surf.blit(self.qr, self.qr.get_rect(center=left.center))
        else:
            draw_text(surf, 'Install python3-qrcode for a QR code', left.center, 28, (0, 0, 0), anchor='center')
        y = CONTENT_Y
        if st.get('last_error'):
            box = pg.Rect(right_x, y, width, 90)
            rrect(surf, (90, 36, 36), box, 16)
            draw_text(surf, "Couldn't connect: %s" % st['last_error'], (box.x + 24, box.centery), 30, TEXT,
                      anchor='midleft', max_width=box.width - 48)
            y += 120
        steps = [
            ('1', 'Scan the code with your phone camera', 'It joins the network "%s" (password: %s).' % (ssid, pw)),
            ('2', 'A setup page opens', "If it doesn't, open %s in your phone's browser." % url),
            ('3', 'Choose your Wi-Fi and type its password', 'The Pi connects and this screen updates.'),
        ]
        for num, head, body in steps:
            pg.draw.circle(surf, ACCENT, (right_x + 30, y + 30), 30)
            draw_text(surf, num, (right_x + 30, y + 30), 34, (255, 255, 255), bold=True, anchor='center')
            draw_text(surf, head, (right_x + 84, y + 8), 36, TEXT, bold=True, max_width=width - 84)
            y = draw_wrapped(surf, body, (right_x + 84, y + 60), width - 84, 30) + 28
        if self.url_qr and y + 240 < FOOTER_Y - 40:
            surf.blit(self.url_qr, (right_x + 84, y))
            draw_wrapped(surf, 'Or scan this to open the setup page.', (right_x + 84 + 250, y + 20),
                         width - 84 - 250, 28, FAINT)


class PowerScreen(ListScreen):
    title = 'Restart or shut down'

    def refresh(self):
        app = self.app
        self.list.set_items([
            Item('Restart', lambda: app.confirm('Restart the Pi?', 'Moonlight starts again afterwards.',
                                                'Restart', lambda: app.power('reboot')), key='reboot'),
            Item('Shut down', lambda: app.confirm('Shut down the Pi?', 'Wait until the green light stops '
                                                  'flashing before unplugging it.', 'Shut down',
                                                  lambda: app.power('poweroff'), danger=True), key='off'),
        ])

    def draw_panel(self, surf, rect):
        draw_wrapped(surf, 'Shutting down also ends any game still running on your PC, if "Quit game on PC at '
                     'shutdown" is on in Settings.', (rect.x, rect.y), rect.width, 30)


# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description='Moonlight Pi menu')
    p.add_argument('--boot', action='store_true', help='decide at boot whether the menu is needed')
    p.add_argument('--fake', action='store_true', help='use fake Wi-Fi/Bluetooth data (testing)')
    p.add_argument('--reason', choices=['offline', 'no-controller', 'quick-setup'],
                   help='open as if for this reason (testing)')
    p.add_argument('--test-input', help='comma-separated actions to run headless (testing)')
    p.add_argument('--style', choices=['ps', 'xbox', 'switch', 'steam', 'keyboard'],
                   help='button style to start with (testing)')
    p.add_argument('--shots', default='.', help='where --test-input saves screenshots')
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s: %(message)s')

    reason = args.reason
    if args.boot:
        import network
        reason = boot_decision(network.Network())
        if reason is None:
            log.info('boot: online with a controller, starting Moonlight')
            return EXIT_MOONLIGHT
        log.info('boot: opening the menu (%s)', reason)
    try:
        return App(args, reason).run()
    except Exception:
        log.exception('menu failed')
        return 1


if __name__ == '__main__':
    sys.exit(main())
