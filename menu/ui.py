"""Drawing helpers and widgets for the Moonlight Pi menu.

Everything is drawn on a 1920x1080 canvas; the display scales it to the TV.
"""
import math
import time

import pygame

W, H = 1920, 1080
MARGIN = 96
HEADER_Y = 64
CONTENT_Y = 200
FOOTER_Y = H - 76

BG = (13, 15, 21)
PANEL = (26, 30, 40)
PANEL_HI = (40, 46, 61)
ACCENT = (98, 132, 255)
ACCENT_SOFT = (52, 66, 120)
TEXT = (236, 239, 245)
DIM = (152, 160, 177)
FAINT = (98, 105, 122)
GOOD = (88, 204, 132)
WARN = (240, 186, 70)
BAD = (240, 98, 98)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

FONT_REGULAR = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
FONT_BOLD = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'

_fonts = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        try:
            _fonts[key] = pygame.font.Font(FONT_BOLD if bold else FONT_REGULAR, size)
        except (FileNotFoundError, OSError):
            _fonts[key] = pygame.font.Font(None, int(size * 1.3))
    return _fonts[key]


def ellipsize(f, s, max_w):
    if f.size(s)[0] <= max_w:
        return s
    while s and f.size(s + '…')[0] > max_w:
        s = s[:-1]
    return s + '…'


def wrap(f, s, max_w):
    lines = []
    for para in s.split('\n'):
        words = para.split(' ')
        line = ''
        for w in words:
            trial = (line + ' ' + w).strip()
            if f.size(trial)[0] <= max_w or not line:
                line = trial
            else:
                lines.append(line)
                line = w
        lines.append(line)
    return lines


_rendered = {}


def render(s, size, color, bold=False):
    """Rendered text, cached: the screen is redrawn often and most text doesn't change."""
    key = (s, size, color, bold)
    img = _rendered.get(key)
    if img is None:
        if len(_rendered) > 2000:
            _rendered.clear()
        img = _rendered[key] = font(size, bold).render(s, True, color)
    return img


def draw_text(surf, s, pos, size=38, color=TEXT, bold=False, anchor='topleft', max_width=None):
    if max_width:
        s = ellipsize(font(size, bold), s, max_width)
    img = render(s, size, color, bold)
    r = img.get_rect(**{anchor: pos})
    surf.blit(img, r)
    return r


def draw_wrapped(surf, s, pos, width, size=32, color=DIM, bold=False, line_gap=8):
    """Draws word-wrapped text and returns the y below it."""
    f = font(size, bold)
    x, y = pos
    for line in wrap(f, s, width):
        surf.blit(render(line, size, color, bold), (x, y))
        y += f.get_linesize() + line_gap
    return y


def rrect(surf, color, rect, radius=18, width=0):
    pygame.draw.rect(surf, color, rect, width, border_radius=radius)


def dot(surf, color, center, r=10):
    pygame.draw.circle(surf, color, center, r)


SPIN_PERIOD = 0.9     # seconds per turn
_spinners = {}


def _spinner_image(r, color, width):
    """A 300-degree arc that fades from a solid round head to a clear tail,
    drawn at 4x and scaled down so it's smooth."""
    key = (r, color, width)
    if key not in _spinners:
        size = 2 * r + width + 4
        k = 4
        big = pygame.Surface((size * k, size * k), pygame.SRCALPHA)
        c = size * k / 2
        sweep = math.radians(300)
        steps = 160
        for i in range(steps + 1):                  # tail first, so the head is drawn on top
            t = i / steps
            a = -(1 - t) * sweep                    # head at angle 0, tail behind it
            alpha = int(255 * t ** 1.4)
            pygame.draw.circle(big, (*color[:3], alpha),
                               (c + r * k * math.cos(a), c + r * k * math.sin(a)), width * k / 2)
        _spinners[key] = pygame.transform.smoothscale(big, (size, size))
    return _spinners[key]


def spinner(surf, center, r=26, color=ACCENT, width=6):
    """A smoothly turning loading spinner (needs the screen redrawn every frame)."""
    angle = -(time.monotonic() % SPIN_PERIOD) / SPIN_PERIOD * 360   # clockwise
    img = pygame.transform.rotozoom(_spinner_image(r, color, width), angle, 1)
    surf.blit(img, img.get_rect(center=center))


_shades = {}


def shade(surf, alpha):
    """Darkens the whole screen (behind dialogs and the busy message)."""
    if alpha not in _shades:
        s = pygame.Surface((W, H), pygame.SRCALPHA)
        s.fill((0, 0, 0, alpha))
        _shades[alpha] = s
    surf.blit(_shades[alpha], (0, 0))


# ---------------------------------------------------------------------------
# Icons. Drawn at SS times the size and scaled down, so the edges are smooth,
# then cached.
# ---------------------------------------------------------------------------
SS = 4
_icons = {}


def _icon(key, size, draw):
    if key not in _icons:
        w, h = size
        big = pygame.Surface((w * SS, h * SS), pygame.SRCALPHA)
        draw(big, SS)
        _icons[key] = pygame.transform.smoothscale(big, (w, h))
    return _icons[key]


def blit_centered(surf, img, center):
    """Blits img so the middle of its visible pixels lands on center."""
    box = img.get_bounding_rect()
    surf.blit(img, (center[0] - box.centerx, center[1] - box.centery))


def signal_bars(surf, x, cy, strength, color=TEXT):
    """Four Wi-Fi signal bars (filled by strength 0-100), vertically centred on cy."""
    filled = 0 if strength <= 0 else 1 + min(3, strength // 26)

    def draw(s, k):
        for i in range(4):
            h = (10 + i * 8) * k
            rect = pygame.Rect(i * 12 * k, 34 * k - h, 8 * k, h)
            pygame.draw.rect(s, color if i < filled else FAINT, rect, border_radius=2 * k)
    surf.blit(_icon(('bars', filled, color), (44, 34), draw), (x, cy - 17))
    return 44


def lock_icon(surf, x, cy, color=DIM):
    """A padlock 22 px wide, vertically centred on cy."""
    def draw(s, k):
        # Shackle: the top half of a ring, plus two short legs down to the body.
        pygame.draw.circle(s, color, (11 * k, 10 * k), 7 * k, int(2.6 * k),
                           draw_top_left=True, draw_top_right=True)
        legw = int(2.6 * k)
        pygame.draw.rect(s, color, (4 * k, 10 * k, legw, 4 * k))
        pygame.draw.rect(s, color, (18 * k - legw, 10 * k, legw, 4 * k))
        pygame.draw.rect(s, color, (1 * k, 13 * k, 20 * k, 15 * k), border_radius=3 * k)
    surf.blit(_icon(('lock', color), (22, 28), draw), (x, cy - 14))
    return 22


# ---------------------------------------------------------------------------
# Button hints
# ---------------------------------------------------------------------------
# Styles: 'ps', 'xbox', 'switch', 'steam' (controllers) and 'keyboard'.
# Buttons are named by position, like SDL: A = bottom face button, B = right,
# X = left, Y = top (Nintendo controllers report them by label instead).
XBOX_COLORS = {'A': (96, 180, 80), 'B': (220, 72, 60), 'X': (60, 122, 220), 'Y': (230, 190, 40)}
PS_COLORS = {'A': (120, 155, 235), 'B': (232, 92, 92), 'X': (225, 120, 190), 'Y': (80, 200, 160)}
SHOULDER_LABELS = {
    'ps': {'LB': 'L1', 'RB': 'R1', 'LT': 'L2', 'RT': 'R2', 'LS': 'L3'},
    'switch': {'LB': 'L', 'RB': 'R', 'LT': 'ZL', 'RT': 'ZR', 'LS': 'LS'},
}
KEY_LABELS = {'A': 'Enter', 'B': 'Esc'}


def _face_badge(button, style):
    def draw(s, k):
        c = (20 * k, 20 * k)
        pygame.draw.circle(s, PANEL_HI, c, 20 * k)
        if style == 'ps':
            col, lw = PS_COLORS[button], int(3.4 * k)
            if button == 'A':
                for dx in (-1, 1):
                    pygame.draw.line(s, col, (c[0] - 9 * k, c[1] - 9 * dx * k), (c[0] + 9 * k, c[1] + 9 * dx * k), lw)
            elif button == 'B':
                pygame.draw.circle(s, col, c, 10 * k, lw)
            elif button == 'X':
                pygame.draw.rect(s, col, (c[0] - 9 * k, c[1] - 9 * k, 18 * k, 18 * k), lw)
            else:
                # Centre the triangle's box (not its corners) on the badge.
                pts = [(c[0], c[1] - 9.5 * k), (c[0] + 10.5 * k, c[1] + 8.5 * k), (c[0] - 10.5 * k, c[1] + 8.5 * k)]
                pygame.draw.polygon(s, col, pts, lw)
        else:
            col = XBOX_COLORS[button] if style in ('xbox', 'steam') else TEXT
            blit_centered(s, font(24 * k, True).render(button, True, col), c)
    return _icon(('face', button, style), (40, 40), draw)


def button_badge(surf, x, cy, button, style):
    """Draws one button glyph for the current input style; returns its width."""
    if style == 'keyboard':
        label = KEY_LABELS[button]
        f = font(24, True)
        w = f.size(label)[0] + 24
        rrect(surf, PANEL_HI, pygame.Rect(x, cy - 20, w, 40), 8)
        blit_centered(surf, f.render(label, True, TEXT), (x + w // 2, cy))
        return w
    if button in ('A', 'B', 'X', 'Y'):
        surf.blit(_face_badge(button, style), (x, cy - 20))
        return 40
    if button == 'START':
        def draw(s, k):
            pygame.draw.rect(s, PANEL_HI, (0, 0, 52 * k, 40 * k), border_radius=20 * k)
            if style == 'switch':     # the + button
                pygame.draw.rect(s, TEXT, (20 * k, 18 * k, 12 * k, 4 * k))
                pygame.draw.rect(s, TEXT, (24 * k, 14 * k, 4 * k, 12 * k))
            else:                     # Options / Menu: three lines
                for dy in (-7, 0, 7):
                    pygame.draw.rect(s, TEXT, (16 * k, (19 + dy) * k, 20 * k, 3 * k), border_radius=k)
        surf.blit(_icon(('start', style), (52, 40), draw), (x, cy - 20))
        return 52
    label = SHOULDER_LABELS.get(style, {}).get(button, button)
    f = font(22, True)
    w = max(48, f.size(label)[0] + 22)
    rrect(surf, PANEL_HI, pygame.Rect(x, cy - 18, w, 36), 10)
    blit_centered(surf, f.render(label, True, TEXT), (x + w // 2, cy))
    return w


def draw_hints(surf, hints, style):
    """Footer with button hints, e.g. [('A', 'Select'), ('B', 'Back')]. A hint
    can list several buttons, e.g. (('LB', 'RB'), 'Move cursor')."""
    x = MARGIN
    cy = FOOTER_Y + 4
    for buttons, label in hints:
        buttons = (buttons,) if isinstance(buttons, str) else buttons
        if style == 'keyboard' and any(b not in KEY_LABELS for b in buttons):
            continue
        for b in buttons:
            x += button_badge(surf, x, cy, b, style) + 6
        x += 6
        x = draw_text(surf, label, (x, cy), 28, DIM, anchor='midleft').right + 40


# ---------------------------------------------------------------------------
# QR codes
# ---------------------------------------------------------------------------
def qr_surface(data, size):
    """A white QR code image for data, or None if the qrcode module is missing."""
    try:
        import qrcode
    except ImportError:
        return None
    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    cell = max(1, size // n)
    surf = pygame.Surface((cell * n, cell * n))
    surf.fill(WHITE)
    for y, row in enumerate(matrix):
        for x, on in enumerate(row):
            if on:
                surf.fill(BLACK, (x * cell, y * cell, cell, cell))
    return surf


def wifi_qr_text(ssid, password):
    def esc(s):
        for ch in '\\;,:"':
            s = s.replace(ch, '\\' + ch)
        return s
    return 'WIFI:T:WPA;S:%s;P:%s;;' % (esc(ssid), esc(password))


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------
class Item:
    def __init__(self, label, action=None, detail='', key=None, selectable=True,
                 color=None, detail_color=None, signal=None, locked=False, spinner=False):
        self.label = label
        self.action = action
        self.detail = detail
        self.key = key if key is not None else label
        self.selectable = selectable
        self.color = color
        self.detail_color = detail_color
        self.signal = signal
        self.locked = locked
        self.spinner = spinner


class ListView:
    def __init__(self, rect, item_h=86, gap=12):
        self.rect = pygame.Rect(rect)
        self.item_h = item_h
        self.gap = gap
        self.items = []
        self.index = 0
        self.scroll = 0
        self.rects = []

    def set_items(self, items):
        key = self.selected.key if self.selected else None
        self.items = items
        self.index = 0
        for i, it in enumerate(items):
            if it.key == key and it.selectable:
                self.index = i
                break
        else:
            self._first_selectable()
        self._clamp_scroll()

    def _first_selectable(self):
        for i, it in enumerate(self.items):
            if it.selectable:
                self.index = i
                return

    @property
    def selected(self):
        if 0 <= self.index < len(self.items) and self.items[self.index].selectable:
            return self.items[self.index]
        return None

    def move(self, d):
        i = self.index
        for _ in range(len(self.items)):
            i += d
            if i < 0 or i >= len(self.items):
                return False
            if self.items[i].selectable:
                self.index = i
                self._clamp_scroll()
                return True
        return False

    def visible_count(self):
        return max(1, (self.rect.height + self.gap) // (self.item_h + self.gap))

    def _clamp_scroll(self):
        n = self.visible_count()
        if self.index < self.scroll:
            self.scroll = self.index
        elif self.index >= self.scroll + n:
            self.scroll = self.index - n + 1
        self.scroll = max(0, min(self.scroll, max(0, len(self.items) - n)))

    def scroll_by(self, d):
        n = self.visible_count()
        self.scroll = max(0, min(self.scroll + d, max(0, len(self.items) - n)))

    def hit(self, pos):
        for i, r in self.rects:
            if r.collidepoint(pos) and self.items[i].selectable:
                return i
        return None

    def draw(self, surf):
        self.rects = []
        n = self.visible_count()
        y = self.rect.y
        for i in range(self.scroll, min(len(self.items), self.scroll + n)):
            it = self.items[i]
            r = pygame.Rect(self.rect.x, y, self.rect.width, self.item_h)
            self.rects.append((i, r))
            selected = i == self.index and it.selectable
            if selected:
                rrect(surf, ACCENT, r, 18)
            elif it.selectable:
                rrect(surf, PANEL, r, 18)
            label_color = WHITE if selected else (it.color or (TEXT if it.selectable else DIM))
            right = r.right - 28
            if it.signal is not None:
                signal_bars(surf, right - 44, r.centery, it.signal, WHITE if selected else TEXT)
                right -= 44 + 22
            if it.locked:
                lock_icon(surf, right - 22, r.centery, WHITE if selected else DIM)
                right -= 22 + 20
            if it.spinner:
                spinner(surf, (right - 24, r.centery), 18, WHITE if selected else ACCENT, 5)
                right -= 64
            if it.detail:
                dcolor = WHITE if selected else (it.detail_color or DIM)
                dr = draw_text(surf, it.detail, (right, r.centery), 30, dcolor, anchor='midright',
                               max_width=r.width // 2)
                right = dr.left - 24
            draw_text(surf, it.label, (r.x + 32, r.centery), 38, label_color,
                      bold=selected, anchor='midleft', max_width=right - r.x - 40)
            y += self.item_h + self.gap
        if self.scroll > 0:
            pygame.draw.polygon(surf, DIM, [(self.rect.centerx - 14, self.rect.y - 14),
                                            (self.rect.centerx + 14, self.rect.y - 14),
                                            (self.rect.centerx, self.rect.y - 30)])
        if self.scroll + n < len(self.items):
            b = self.rect.y + n * (self.item_h + self.gap) + 4
            pygame.draw.polygon(surf, DIM, [(self.rect.centerx - 14, b), (self.rect.centerx + 14, b),
                                            (self.rect.centerx, b + 16)])


# ---------------------------------------------------------------------------
# Screens, dialogs and the on-screen keyboard
# ---------------------------------------------------------------------------
class Screen:
    title = ''
    animating = False

    def __init__(self, app):
        self.app = app

    def enter(self):
        pass

    def leave(self):
        """Another screen covers this one, or it is being closed."""

    def close(self):
        """This screen has been removed for good."""

    def refresh(self):
        pass

    def handle(self, action):
        pass

    def hover(self, pos):
        pass

    def click(self, pos):
        pass

    def wheel(self, dy):
        pass

    def draw(self, surf):
        pass

    def hints(self):
        return [('A', 'Select'), ('B', 'Back')]


class ListScreen(Screen):
    """A screen with a menu list on the left and an info panel on the right."""
    list_rect = (MARGIN, CONTENT_Y, 1020, FOOTER_Y - CONTENT_Y - 60)
    panel_rect = (MARGIN + 1080, CONTENT_Y, W - MARGIN * 2 - 1080, FOOTER_Y - CONTENT_Y - 60)

    def __init__(self, app):
        super().__init__(app)
        self.list = ListView(self.list_rect)

    @property
    def animating(self):
        return any(it.spinner for it in self.list.items)

    def handle(self, action):
        if action == 'up':
            self.list.move(-1)
        elif action == 'down':
            self.list.move(1)
        elif action in ('select', 'enter'):
            it = self.list.selected
            if it and it.action:
                it.action()
        elif action in ('back', 'backspace', 'escape'):
            self.app.pop()

    def hover(self, pos):
        i = self.list.hit(pos)
        if i is not None:
            self.list.index = i

    def click(self, pos):
        i = self.list.hit(pos)
        if i is not None:
            self.list.index = i
            self.handle('select')

    def wheel(self, dy):
        self.list.scroll_by(-dy)

    def draw(self, surf):
        self.list.draw(surf)
        rrect(surf, PANEL, pygame.Rect(self.panel_rect), 22)
        self.draw_panel(surf, pygame.Rect(self.panel_rect).inflate(-64, -64))

    def draw_panel(self, surf, rect):
        pass


class Dialog:
    """A modal box with a message and a row of buttons."""

    def __init__(self, title, message, buttons=('OK',), on_close=None, danger=None, default=0):
        self.title = title
        self.message = message
        self.buttons = list(buttons)
        self.on_close = on_close
        self.danger = danger
        self.index = default
        self.rects = []

    def handle(self, action):
        if action == 'left':
            self.index = max(0, self.index - 1)
        elif action == 'right':
            self.index = min(len(self.buttons) - 1, self.index + 1)
        elif action in ('select', 'enter'):
            return self.index
        elif action in ('back', 'backspace', 'escape'):
            return -1
        return None

    def hover(self, pos):
        for i, r in self.rects:
            if r.collidepoint(pos):
                self.index = i

    def click(self, pos):
        for i, r in self.rects:
            if r.collidepoint(pos):
                self.index = i
                return i
        return None

    def draw(self, surf):
        shade(surf, 170)
        box = pygame.Rect(0, 0, 1100, 0)
        lines = wrap(font(34), self.message, box.width - 128)
        box.height = 220 + len(lines) * 46 + 110
        box.center = (W // 2, H // 2)
        rrect(surf, PANEL, box, 26)
        draw_text(surf, self.title, (box.x + 64, box.y + 56), 46, TEXT, bold=True)
        y = box.y + 140
        for line in lines:
            draw_text(surf, line, (box.x + 64, y), 34, DIM)
            y += 46
        self.rects = []
        gap = 28
        f = font(34, True)
        widths = [max(240, f.size(label)[0] + 80) for label in self.buttons]
        x = box.centerx - (sum(widths) + gap * (len(widths) - 1)) // 2
        for i, label in enumerate(self.buttons):
            bw = widths[i]
            r = pygame.Rect(x, box.bottom - 120, bw, 76)
            self.rects.append((i, r))
            if i == self.index:
                rrect(surf, BAD if i == self.danger else ACCENT, r, 18)
            else:
                rrect(surf, PANEL_HI, r, 18)
            draw_text(surf, label, r.center, 34, WHITE, bold=i == self.index, anchor='center')
            x += bw + gap


LETTERS = ['1234567890', 'qwertyuiop', "asdfghjkl'", 'zxcvbnm,.-']
SYMBOLS = ['1234567890', '!@#$%^&*()', '`~_=+[]{}\\|', ';:"/?<>\'.,-']
SPECIALS = [('shift', 'Shift', 2), ('page', '123', 2), ('space', 'Space', 4),
            ('del', '⌫', 2), ('show', 'Show', 2), ('done', 'Done', 3)]

# On-screen keyboard controls, copied from each console's own keyboard.
# Controller inputs (A, B, X, Y by position; lb/rb shoulders; lt/rt triggers;
# ls = left stick click; start) map to keyboard actions. Unknown controllers
# use the Xbox controls.
OSK_CONTROLS = {
    'ps': {'select': 'type', 'x': 'delete', 'y': 'space', 'lt': 'shift', 'rt': 'symbols',
           'start': 'done', 'back': 'close', 'lb': 'left', 'rb': 'right'},
    'xbox': {'select': 'type', 'x': 'delete', 'y': 'space', 'ls': 'shift', 'lt': 'symbols',
             'start': 'done', 'back': 'close', 'lb': 'left', 'rb': 'right'},
    'switch': {'select': 'type', 'back': 'delete', 'y': 'space', 'ls': 'shift', 'lt': 'symbols',
               'start': 'done', 'lb': 'left', 'rb': 'right'},
    'steam': {'select': 'type', 'x': 'delete', 'y': 'space', 'lt': 'shift', 'rt': 'symbols',
              'start': 'done', 'back': 'close', 'lb': 'left', 'rb': 'right'},
}
INPUT_BUTTON = {'select': 'A', 'back': 'B', 'x': 'X', 'y': 'Y', 'start': 'START',
                'lb': 'LB', 'rb': 'RB', 'lt': 'LT', 'rt': 'RT', 'ls': 'LS'}
HINT_LABELS = [('type', 'Type'), ('delete', 'Delete'), ('space', 'Space'), ('shift', 'Shift'),
               ('symbols', '123'), ('left', 'Move cursor'), ('done', 'Done'), ('close', 'Close')]


class KeyboardScreen(Screen):
    """Full-screen on-screen keyboard. Calls on_done(text) or on_done(None) if cancelled.

    With a real keyboard: type straight into the field (Backspace deletes), the
    arrow keys move around the on-screen keys, Enter presses the highlighted
    one (for Show/Hide and Done), and Esc goes back."""

    def __init__(self, app, title, prompt, on_done, secret=False, initial='', validate=None,
                 max_len=63):
        super().__init__(app)
        self.title = title
        self.prompt = prompt
        self.on_done = on_done
        self.secret = secret
        self.show = not secret
        self.text = initial
        self.pos = len(initial)          # cursor position in the text
        self.validate = validate
        self.max_len = max_len
        self.shift = False
        self.symbols = False
        self.error = ''
        self.row, self.col = 1, 0
        self.keys = []      # rows of (id, label, rect)
        self.blink = None
        self._layout()

    def tick(self):
        """Redraw only when the cursor blinks (or a real Shift key changes)."""
        state = (int(time.monotonic() * 2) % 2, self._held_shift())
        changed = state != self.blink
        self.blink = state
        return changed

    @staticmethod
    def _held_shift():
        """Shift held (or Caps Lock on) on a real keyboard."""
        return bool(pygame.key.get_mods() & (pygame.KMOD_SHIFT | pygame.KMOD_CAPS))

    def _upper(self):
        return self.shift or self._held_shift()

    def enter(self):
        pygame.key.start_text_input()

    def leave(self):
        pygame.key.stop_text_input()

    def controls(self):
        return OSK_CONTROLS.get(self.app.style, OSK_CONTROLS['xbox'])

    # -- layout --------------------------------------------------------------
    def _layout(self):
        rows = SYMBOLS if self.symbols else LETTERS
        unit, gap, key_h = 132, 12, 84
        self.keys = []
        y = 450
        for chars in rows:
            width = len(chars) * unit + (len(chars) - 1) * gap
            x = (W - width) // 2
            row = []
            for ch in chars:
                # The label is worked out when drawing, as Shift can change at any time.
                row.append((('char', ch), ch, pygame.Rect(x, y, unit, key_h)))
                x += unit + gap
            self.keys.append(row)
            y += key_h + gap
        specials = [s for s in SPECIALS if self.secret or s[0] != 'show']
        total = sum(span for _, _, span in SPECIALS)
        full = 10 * unit + 9 * gap
        widths = [int((full + gap) * span / total) - gap for _, _, span in specials]
        x = (W - (sum(widths) + gap * (len(widths) - 1))) // 2     # centred
        row = []
        for (kid, label, _), w in zip(specials, widths):
            if kid == 'page':
                label = 'abc' if self.symbols else '123'
            if kid == 'show':
                label = 'Hide' if self.show else 'Show'
            row.append(((kid,), label, pygame.Rect(x, y, w, key_h)))
            x += w + gap
        self.keys.append(row)
        self.row = min(self.row, len(self.keys) - 1)
        self.col = min(self.col, len(self.keys[self.row]) - 1)

    # -- editing ---------------------------------------------------------------
    def _insert(self, s):
        self.error = ''
        s = s[:self.max_len - len(self.text)]
        if s:
            self.text = self.text[:self.pos] + s + self.text[self.pos:]
            self.pos += len(s)

    def _delete(self):
        self.error = ''
        if self.pos > 0:
            self.text = self.text[:self.pos - 1] + self.text[self.pos:]
            self.pos -= 1

    def _press(self, kid):
        self.error = ''
        if kid[0] == 'char':
            self._insert(kid[1].upper() if self._upper() else kid[1])
            if self.shift:
                self.shift = False
        elif kid[0] == 'space':
            self._insert(' ')
        elif kid[0] == 'del':
            self._delete()
        elif kid[0] == 'shift':
            self.shift = not self.shift
        elif kid[0] == 'page':
            self.symbols = not self.symbols
            self._layout()
        elif kid[0] == 'show':
            self.show = not self.show
            self._layout()
        elif kid[0] == 'done':
            self._finish()

    def _finish(self):
        if self.validate:
            err = self.validate(self.text)
            if err:
                self.error = err
                return
        self.app.pop()
        self.on_done(self.text)

    def _cancel(self):
        self.app.pop()
        self.on_done(None)

    def _close(self):
        if not self.text:
            self._cancel()
        else:
            self.app.confirm('Discard what you typed?', 'Close the keyboard without using it?',
                             'Discard', self._cancel, danger=True, no='Keep typing')

    def _move_row(self, d):
        r = self.row + d
        if not 0 <= r < len(self.keys):
            return
        cx = self.keys[self.row][self.col][2].centerx
        self.row = r
        self.col = min(range(len(self.keys[r])), key=lambda i: abs(self.keys[r][i][2].centerx - cx))

    # -- input ---------------------------------------------------------------
    def handle(self, action):
        if action == 'up':
            self._move_row(-1)
        elif action == 'down':
            self._move_row(1)
        elif action == 'left':
            self.col = (self.col - 1) % len(self.keys[self.row])
        elif action == 'right':
            self.col = (self.col + 1) % len(self.keys[self.row])
        elif action == 'enter':                       # real keyboard: press the highlighted key
            self._press(self.keys[self.row][self.col][0])
        elif action == 'backspace':                   # real keyboard
            self._delete()
        elif action == 'escape':                      # real keyboard: straight back
            self._cancel()
        elif isinstance(action, tuple) and action[0] == 'char':
            self._insert(action[1])
        else:
            self._controller(self.controls().get(action))

    def _controller(self, op):
        if op == 'type':
            self._press(self.keys[self.row][self.col][0])
        elif op == 'delete':
            if self.text or 'close' in self.controls().values():
                self._delete()
            else:
                self._cancel()      # e.g. Switch: B on an empty field closes
        elif op == 'space':
            self._press(('space',))
        elif op == 'shift':
            self._press(('shift',))
        elif op == 'symbols':
            self._press(('page',))
        elif op == 'done':
            self._finish()
        elif op == 'close':
            self._close()
        elif op == 'left':
            self.pos = max(0, self.pos - 1)
        elif op == 'right':
            self.pos = min(len(self.text), self.pos + 1)

    def _key_at(self, pos):
        for r, row in enumerate(self.keys):
            for c, (_, _, rect) in enumerate(row):
                if rect.collidepoint(pos):
                    return r, c
        return None

    def hover(self, pos):
        hit = self._key_at(pos)
        if hit:
            self.row, self.col = hit

    def click(self, pos):
        hit = self._key_at(pos)
        if hit:
            self.row, self.col = hit
            self._press(self.keys[self.row][self.col][0])

    def hints(self):
        if self.app.style == 'keyboard':
            return [('B', 'Back')]      # typing needs no hints; Esc goes back
        controls = self.controls()
        hints = []
        for op, label in HINT_LABELS:
            inputs = [i for i, o in controls.items() if o == op]
            if op == 'left':
                inputs += [i for i, o in controls.items() if o == 'right']
            if inputs:
                hints.append((tuple(INPUT_BUTTON[i] for i in inputs), label))
        return hints

    # -- drawing -------------------------------------------------------------
    def draw(self, surf):
        draw_text(surf, self.prompt, (MARGIN, CONTENT_Y), 36, DIM)
        field = pygame.Rect(MARGIN, CONTENT_Y + 64, W - 2 * MARGIN, 110)
        rrect(surf, PANEL, field, 18)
        shown = self.text if self.show else '•' * len(self.text)
        f = font(48)
        # Scroll so the cursor stays inside the field.
        start = 0
        while start < self.pos and f.size(shown[start:self.pos])[0] > field.width - 96:
            start += 1
        visible = shown[start:]
        while visible and f.size(visible)[0] > field.width - 64:
            visible = visible[:-1]
        x0, cy = field.x + 32, field.centery
        draw_text(surf, visible, (x0, cy), 48, TEXT, anchor='midleft')
        if int(time.monotonic() * 2) % 2 == 0:
            cx = x0 + f.size(shown[start:self.pos])[0] + 1
            pygame.draw.line(surf, TEXT, (cx, cy - 26), (cx, cy + 26), 3)
        if self.error:
            draw_text(surf, self.error, (MARGIN, field.bottom + 24), 32, BAD)
        else:
            draw_text(surf, '%d characters' % len(self.text), (MARGIN, field.bottom + 24), 28, FAINT)
        upper = self._upper()
        for r, row in enumerate(self.keys):
            for c, (kid, label, rect) in enumerate(row):
                selected = (r, c) == (self.row, self.col)
                active = kid[0] == 'shift' and (self.shift or self._held_shift())
                if kid[0] == 'char' and upper:
                    label = label.upper()
                rrect(surf, ACCENT if selected else (ACCENT_SOFT if active else PANEL), rect, 14)
                size = 40 if kid[0] == 'char' else 32
                draw_text(surf, label, rect.center, size, WHITE if selected else TEXT,
                          bold=selected, anchor='center')
