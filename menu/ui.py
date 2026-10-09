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


def draw_text(surf, s, pos, size=38, color=TEXT, bold=False, anchor='topleft', max_width=None):
    f = font(size, bold)
    if max_width:
        s = ellipsize(f, s, max_width)
    img = f.render(s, True, color)
    r = img.get_rect(**{anchor: pos})
    surf.blit(img, r)
    return r


def draw_wrapped(surf, s, pos, width, size=32, color=DIM, bold=False, line_gap=8):
    """Draws word-wrapped text and returns the y below it."""
    f = font(size, bold)
    x, y = pos
    for line in wrap(f, s, width):
        surf.blit(f.render(line, True, color), (x, y))
        y += f.get_linesize() + line_gap
    return y


def rrect(surf, color, rect, radius=18, width=0):
    pygame.draw.rect(surf, color, rect, width, border_radius=radius)


def dot(surf, color, center, r=10):
    pygame.draw.circle(surf, color, center, r)


def spinner(surf, center, r=26, color=ACCENT, width=6):
    start = (time.monotonic() * 5) % (2 * math.pi)
    rect = pygame.Rect(0, 0, r * 2, r * 2)
    rect.center = center
    pygame.draw.arc(surf, color, rect, start, start + 4.2, width)


def signal_bars(surf, x, cy, strength, color=TEXT):
    """Four Wi-Fi signal bars, filled according to strength (0-100)."""
    filled = 0 if strength <= 0 else 1 + min(3, strength // 26)
    for i in range(4):
        h = 10 + i * 8
        r = pygame.Rect(x + i * 12, cy + 16 - h, 8, h)
        pygame.draw.rect(surf, color if i < filled else FAINT, r, border_radius=2)
    return 4 * 12


def lock_icon(surf, x, cy, color=DIM):
    body = pygame.Rect(x, cy - 4, 22, 18)
    pygame.draw.rect(surf, color, body, border_radius=3)
    pygame.draw.arc(surf, color, pygame.Rect(x + 3, cy - 16, 16, 22), 0, math.pi, 3)
    return 26


# ---------------------------------------------------------------------------
# Button hints
# ---------------------------------------------------------------------------
XBOX_COLORS = {'A': (96, 180, 80), 'B': (220, 72, 60), 'X': (60, 122, 220), 'Y': (230, 190, 40)}
PS_GLYPHS = {'A': ('✕', (120, 155, 235)), 'B': ('○', (232, 92, 92)),
             'X': ('□', (225, 120, 190)), 'Y': ('△', (80, 200, 160))}
KEY_LABELS = {'A': 'Enter', 'B': 'Esc', 'X': 'Space', 'Y': 'Shift', 'START': 'Enter'}


def button_badge(surf, x, cy, button, style):
    """Draws one button glyph for the current input style; returns its width."""
    if style == 'keyboard':
        label = KEY_LABELS.get(button, button)
        f = font(24, True)
        w = f.size(label)[0] + 24
        rrect(surf, PANEL_HI, pygame.Rect(x, cy - 20, w, 40), 8)
        surf.blit(f.render(label, True, TEXT), f.render(label, True, TEXT).get_rect(center=(x + w // 2, cy)))
        return w
    if button == 'START':
        rrect(surf, PANEL_HI, pygame.Rect(x, cy - 20, 52, 40), 20)
        for i in (-8, 0, 8):
            pygame.draw.line(surf, TEXT, (x + 16, cy + i), (x + 36, cy + i), 3)
        return 52
    pygame.draw.circle(surf, PANEL_HI, (x + 20, cy), 20)
    if style == 'ps':
        glyph, color = PS_GLYPHS[button]
        img = font(26, True).render(glyph, True, color)
    else:
        color = XBOX_COLORS[button] if style == 'xbox' else TEXT
        img = font(24, True).render(button, True, color)
    surf.blit(img, img.get_rect(center=(x + 20, cy)))
    return 40


def draw_hints(surf, hints, style):
    """Footer with button hints, e.g. [('A', 'Select'), ('B', 'Back')]."""
    x = MARGIN
    cy = FOOTER_Y + 4
    for button, label in hints:
        if style == 'keyboard' and button not in KEY_LABELS:
            continue
        x += button_badge(surf, x, cy, button, style) + 12
        x = draw_text(surf, label, (x, cy), 28, DIM, anchor='midleft').right + 44


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
                right -= signal_bars(surf, right - 48, r.centery - 8, it.signal,
                                     WHITE if selected else TEXT) + 18
            if it.locked:
                right -= lock_icon(surf, right - 26, r.centery + 2, WHITE if selected else DIM) + 14
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
        shade = pygame.Surface((W, H), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 170))
        surf.blit(shade, (0, 0))
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
        bw, gap = 280, 28
        x = box.right - 64 - len(self.buttons) * bw - (len(self.buttons) - 1) * gap
        for i, label in enumerate(self.buttons):
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


class KeyboardScreen(Screen):
    """Full-screen on-screen keyboard. Calls on_done(text) or on_done(None) if cancelled."""

    def __init__(self, app, title, prompt, on_done, secret=False, initial='', validate=None,
                 max_len=63):
        super().__init__(app)
        self.title = title
        self.prompt = prompt
        self.on_done = on_done
        self.secret = secret
        self.show = not secret
        self.text = initial
        self.validate = validate
        self.max_len = max_len
        self.shift = False
        self.symbols = False
        self.error = ''
        self.row, self.col = 1, 0
        self.keys = []      # rows of (id, label, rect)
        self._layout()

    def enter(self):
        pygame.key.start_text_input()

    def leave(self):
        pygame.key.stop_text_input()

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
                label = ch.upper() if (self.shift and ch.isalpha()) else ch
                row.append((('char', label), label, pygame.Rect(x, y, unit, key_h)))
                x += unit + gap
            self.keys.append(row)
            y += key_h + gap
        total = sum(span for _, _, span in SPECIALS)
        width = 10 * unit + 9 * gap
        x = (W - width) // 2
        row = []
        for kid, label, span in SPECIALS:
            if kid == 'page':
                label = 'abc' if self.symbols else '123'
            if kid == 'show':
                label = 'Hide' if self.show else 'Show'
                if not self.secret:
                    continue
            w = int(width * span / total) - gap
            row.append(((kid,), label, pygame.Rect(x, y, w, key_h)))
            x += w + gap
        self.keys.append(row)
        self.row = min(self.row, len(self.keys) - 1)
        self.col = min(self.col, len(self.keys[self.row]) - 1)

    def _press(self, kid):
        self.error = ''
        if kid[0] == 'char':
            if len(self.text) < self.max_len:
                self.text += kid[1]
                if self.shift:
                    self.shift = False
                    self._layout()
        elif kid[0] == 'space':
            if len(self.text) < self.max_len:
                self.text += ' '
        elif kid[0] == 'del':
            self.text = self.text[:-1]
        elif kid[0] == 'shift':
            self.shift = not self.shift
            self._layout()
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

    def _move_row(self, d):
        r = self.row + d
        if not 0 <= r < len(self.keys):
            return
        cx = self.keys[self.row][self.col][2].centerx
        self.row = r
        self.col = min(range(len(self.keys[r])), key=lambda i: abs(self.keys[r][i][2].centerx - cx))

    def handle(self, action):
        if action == 'up':
            self._move_row(-1)
        elif action == 'down':
            self._move_row(1)
        elif action == 'left':
            self.col = (self.col - 1) % len(self.keys[self.row])
        elif action == 'right':
            self.col = (self.col + 1) % len(self.keys[self.row])
        elif action == 'select':
            self._press(self.keys[self.row][self.col][0])
        elif action == 'back':
            if self.text:
                self.text = self.text[:-1]
            else:
                self._cancel()
        elif action == 'backspace':
            self.text = self.text[:-1]
        elif action == 'escape':
            self._cancel()
        elif action == 'x':
            self._press(('space',))
        elif action == 'y':
            self._press(('shift',))
        elif action in ('start', 'enter'):
            self._finish()
        elif isinstance(action, tuple) and action[0] == 'char':
            self.error = ''
            if len(self.text) < self.max_len:
                self.text += action[1]

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
        return [('A', 'Type'), ('B', 'Delete'), ('X', 'Space'), ('Y', 'Shift'), ('START', 'Done')]

    def draw(self, surf):
        draw_text(surf, self.prompt, (MARGIN, CONTENT_Y), 36, DIM)
        field = pygame.Rect(MARGIN, CONTENT_Y + 64, W - 2 * MARGIN, 110)
        rrect(surf, PANEL, field, 18)
        shown = self.text if self.show else '•' * len(self.text)
        caret = '|' if int(time.monotonic() * 2) % 2 == 0 else ' '
        f = font(48)
        s = shown
        while s and f.size(s + caret)[0] > field.width - 64:
            s = s[1:]
        draw_text(surf, s + caret, (field.x + 32, field.centery), 48, TEXT, anchor='midleft')
        if self.error:
            draw_text(surf, self.error, (MARGIN, field.bottom + 24), 32, BAD)
        else:
            draw_text(surf, '%d characters' % len(self.text), (MARGIN, field.bottom + 24), 28, FAINT)
        for r, row in enumerate(self.keys):
            for c, (kid, label, rect) in enumerate(row):
                selected = (r, c) == (self.row, self.col)
                active = (kid[0] == 'shift' and self.shift)
                rrect(surf, ACCENT if selected else (ACCENT_SOFT if active else PANEL), rect, 14)
                size = 40 if kid[0] == 'char' else 32
                draw_text(surf, label, rect.center, size, WHITE if selected else TEXT,
                          bold=selected, anchor='center')

    animating = True
