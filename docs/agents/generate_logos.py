#!/usr/bin/env python3
"""
Логотипы агентов — 32×32 пиксель-арт эмблемы.

Дизайн-язык: каждый агент — иконографическая эмблема в тёмном бейдже со
скруглёнными углами (как премиальная app-иконка), мотив с 1px контуром и
двух-трёхтоновой растушёвкой. Серьёзнее скрамиков: без блоб-мордашек.

Рендер: docs/agents/png/<id>.png (32×32) + <id>@12x.png (превью) + contact.png.
"""
from __future__ import annotations
import os
from PIL import Image

SIZE = 32
OUT = os.path.join(os.path.dirname(__file__), "png")
os.makedirs(OUT, exist_ok=True)

# ----------------------------------------------------------------- утилиты цвета
def hx(s):
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), 255)

def mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(4))

OUTLINE = hx("#0e1018")

# --------------------------------------------------------------------- холст/слой
class Layer:
    """32×32 сетка RGBA-пикселей или None (прозрачно)."""
    def __init__(self):
        self.px = [[None] * SIZE for _ in range(SIZE)]

    def set(self, x, y, c):
        if 0 <= x < SIZE and 0 <= y < SIZE and c is not None:
            self.px[y][x] = c

    def get(self, x, y):
        if 0 <= x < SIZE and 0 <= y < SIZE:
            return self.px[y][x]
        return None

    def rect(self, x0, y0, x1, y1, c):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                self.set(x, y, c)

    def hline(self, x0, x1, y, c):
        for x in range(x0, x1 + 1):
            self.set(x, y, c)

    def vline(self, x, y0, y1, c):
        for y in range(y0, y1 + 1):
            self.set(x, y, c)

    def disc(self, cx, cy, r, c):
        for y in range(int(cy - r) - 1, int(cy + r) + 2):
            for x in range(int(cx - r) - 1, int(cx + r) + 2):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r + r * 0.35:
                    self.set(x, y, c)

    def ring(self, cx, cy, r, c, w=1.0):
        for y in range(int(cy - r) - 1, int(cy + r) + 2):
            for x in range(int(cx - r) - 1, int(cx + r) + 2):
                d = (x - cx) ** 2 + (y - cy) ** 2
                if (r - w) ** 2 <= d <= r * r + r * 0.35:
                    self.set(x, y, c)

    def tri_down(self, cx, ytip, ybase, halfw_base, c):
        """Симметричный треугольник вершиной вверх (шляпа)."""
        h = ybase - ytip
        for y in range(ytip, ybase + 1):
            t = (y - ytip) / max(1, h)
            hw = halfw_base * t
            for x in range(round(cx - hw), round(cx + hw) + 1):
                self.set(x, y, c)

    def poly(self, pts, c):
        ys = [p[1] for p in pts]
        for y in range(min(ys), max(ys) + 1):
            xs = []
            n = len(pts)
            for i in range(n):
                x0, y0 = pts[i]
                x1, y1 = pts[(i + 1) % n]
                if (y0 <= y < y1) or (y1 <= y < y0):
                    xs.append(x0 + (x1 - x0) * (y - y0) / (y1 - y0))
            xs.sort()
            for i in range(0, len(xs) - 1, 2):
                for x in range(round(xs[i]), round(xs[i + 1]) + 1):
                    self.set(x, y, c)

    def outline(self, c=OUTLINE, diag=True):
        """1px контур: пустые пиксели, соседние с заполненными, красим в c."""
        nb = [(-1, 0), (1, 0), (0, -1), (0, 1)]
        if diag:
            nb += [(-1, -1), (1, -1), (-1, 1), (1, 1)]
        add = []
        for y in range(SIZE):
            for x in range(SIZE):
                if self.px[y][x] is not None:
                    continue
                for dx, dy in nb:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < SIZE and 0 <= ny < SIZE and self.px[ny][nx] is not None:
                        add.append((x, y))
                        break
        for x, y in add:
            self.px[y][x] = c
        return self

    def composite_over(self, base):
        for y in range(SIZE):
            for x in range(SIZE):
                if self.px[y][x] is not None:
                    base.px[y][x] = self.px[y][x]


# -------------------------------------------------------------- бейдж-подложка
def make_badge(top, bot, border, accent=None):
    """Скруглённый тёмный бейдж с вертикальным градиентом и рамкой."""
    L = Layer()
    corner = {(0, 0), (1, 0), (0, 1), (SIZE - 1, 0), (SIZE - 2, 0), (SIZE - 1, 1),
              (0, SIZE - 1), (1, SIZE - 1), (0, SIZE - 2),
              (SIZE - 1, SIZE - 1), (SIZE - 2, SIZE - 1), (SIZE - 1, SIZE - 2)}
    for y in range(SIZE):
        t = y / (SIZE - 1)
        col = mix(top, bot, t)
        for x in range(SIZE):
            if (x, y) in corner:
                continue
            L.set(x, y, col)
    # рамка
    for y in range(SIZE):
        for x in range(SIZE):
            if L.px[y][x] is None:
                continue
            edge = False
            for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                nx, ny = x + dx, y + dy
                if not (0 <= nx < SIZE and 0 <= ny < SIZE) or L.px[ny][nx] is None:
                    edge = True
            if edge:
                L.px[y][x] = border
    # верхний внутренний блик
    for x in range(2, SIZE - 2):
        if L.px[1][x] is not None:
            L.px[1][x] = mix(L.px[1][x], (255, 255, 255, 255), 0.12)
    if accent:  # тонкая нижняя акцентная линия
        for x in range(3, SIZE - 3):
            if L.px[SIZE - 2][x] is not None:
                L.px[SIZE - 2][x] = mix(L.px[SIZE - 2][x], accent, 0.5)
    return L


# =============================================================== АГЕНТЫ
def agent_pm():
    """pm_agent — Гэндальф с палантиром. Серый маг, белая борода, фиолетовый орб."""
    badge = make_badge(hx("#2a2f44"), hx("#15182a"), hx("#0c0e1a"), hx("#8a6bff"))
    L = Layer()
    g = hx("#9aa1b4"); gd = hx("#6a7088"); gh = hx("#c4cad8")   # серая ткань
    skin = hx("#e8bd92"); skd = hx("#c8966a")
    bd = hx("#eef1f8"); bds = hx("#cdd3e2")                      # борода
    star = hx("#ffd368")

    # шляпа — высокий конус с лёгким изгибом
    L.tri_down(16, 1, 12, 7, g)
    # тень/блик на шляпе
    for y in range(2, 12):
        row = [x for x in range(SIZE) if L.get(x, y) == g]
        if row:
            L.set(min(row), y, gh)
            L.set(max(row), y, gd)
            L.set(max(row) - 1, y, gd)
    # кончик заломлен
    L.set(15, 1, g); L.set(16, 1, g); L.set(17, 2, g)
    # лента + звезда-самоцвет
    L.hline(9, 23, 11, hx("#3a3550"))
    L.hline(9, 23, 12, hx("#2c2842"))
    L.set(16, 11, star); L.set(16, 12, hx("#b8862f")); L.set(15, 12, star); L.set(17, 12, star)
    # поля шляпы
    L.hline(7, 25, 13, g)
    L.set(7, 13, gd); L.set(8, 13, gd); L.set(24, 13, gd); L.set(25, 13, gd)
    L.hline(7, 25, 14, gd)
    # лицо
    L.rect(10, 15, 22, 18, skin)
    L.set(10, 15, skd); L.set(22, 15, skd)
    # глаза (серьёзный взгляд из-под полей)
    L.set(13, 16, OUTLINE); L.set(14, 16, OUTLINE)
    L.set(18, 16, OUTLINE); L.set(19, 16, OUTLINE)
    # борода — окладистая, до низа
    L.poly([(10, 18), (22, 18), (24, 21), (22, 27), (16, 30), (10, 27), (8, 21)], bd)
    # усы/тени бороды
    for y in range(19, 30):
        row = [x for x in range(SIZE) if L.get(x, y) == bd]
        if row:
            L.set(max(row), y, bds); L.set(max(row) - 1, y, bds)
    L.hline(12, 20, 19, bds)   # линия усов
    L.set(16, 18, skin); L.set(15, 18, skin); L.set(17, 18, skin)  # рот скрыт

    # палантир — светящийся орб в правой нижней руке
    ox, oy, orr = 24, 24, 4
    L.disc(ox, oy, orr + 1, hx("#241a3a"))           # тёмное стекло
    L.disc(ox, oy, orr, hx("#6a3fd0"))               # ядро
    L.disc(ox - 1, oy - 1, orr - 1.5, hx("#a981ef"))  # свечение
    L.set(ox - 1, oy - 2, hx("#efe2ff")); L.set(ox - 2, oy - 1, hx("#efe2ff"))  # блик
    L.disc(ox, oy + 1, orr * 0.5, hx("#c7a6ff"))      # внутренний огонёк
    # подставка под орб
    L.hline(ox - 3, ox + 3, oy + orr + 1, hx("#4a4258"))

    L.outline()
    L.composite_over(badge)
    return badge


def agent_meeting():
    """meeting_summarizer — звуковая волна, сходящаяся в документ-саммари."""
    badge = make_badge(hx("#11332f"), hx("#08201d"), hx("#062018"), hx("#2fd6b0"))
    L = Layer()
    paper = hx("#f2efe4"); psh = hx("#d6d0bd"); line = hx("#5b6576")
    teal = hx("#27c2a3"); td = hx("#1c8f78")
    # документ (слегка повёрнут — чуть приподнят правый верх)
    L.rect(9, 9, 23, 27, paper)
    # тень страницы
    L.vline(23, 9, 27, psh); L.hline(9, 23, 27, psh)
    # загнутый уголок
    L.set(23, 9, None); L.set(22, 9, None); L.set(23, 10, None)
    L.set(21, 9, hx("#cfc8b3")); L.set(22, 10, hx("#cfc8b3")); L.set(23, 11, hx("#cfc8b3"))
    # шапка-заголовок (бирюзовая плашка)
    L.rect(11, 11, 20, 13, teal)
    L.hline(11, 20, 13, td)
    # строки саммари с буллетами
    for i, y in enumerate(range(16, 26, 2)):
        L.set(11, y, teal)                       # буллет
        L.hline(13, 20 - (i % 2) * 3, y, line)   # текст
    # галочка-«согласовано» внизу
    L.set(12, 24, td); L.set(13, 25, td); L.set(14, 24, td)
    L.set(15, 23, td); L.set(16, 22, td)

    # эквалайзер слева — «голоса встречи» сходятся в саммари (бары с зазором)
    wave = hx("#2fd6b0")
    bars = [(4, 18, 22), (6, 14, 24), (8, 17, 23)]
    for x, y0, y1 in bars:
        L.vline(x, y0, y1, wave)
        L.set(x, y0, hx("#7defcf")); L.set(x, y1, td)

    L.outline()
    L.composite_over(badge)
    return badge


def agent_audit():
    """board_audit_agent — kanban-доска под лупой, аудит."""
    badge = make_badge(hx("#3a2a12") , hx("#241606"), hx("#1c1204"), hx("#ffb02e"))
    L = Layer()
    board = hx("#e8edf4"); bsh = hx("#c3cad6")
    c_red = hx("#e8554e"); c_amb = hx("#f2a73c"); c_grn = hx("#46c06a"); c_blu = hx("#4a90e2")
    # доска
    L.rect(6, 7, 26, 25, board)
    L.hline(6, 26, 25, bsh); L.vline(26, 7, 25, bsh)
    # три колонки карточек
    cols = [8, 14, 20]
    cards = [[c_red, c_amb], [c_grn, c_blu, c_amb], [c_grn]]
    for cx, stack in zip(cols, cards):
        L.vline(cx + 2, 9, 23, hx("#cdd5e0"))  # разделитель-намёк
        for i, col in enumerate(stack):
            y = 10 + i * 4
            L.rect(cx, y, cx + 3, y + 2, col)
            L.hline(cx, cx + 3, y + 2, mix(col, OUTLINE, 0.35))

    # лупа поверх — аудит
    cx, cy, r = 21, 19, 5
    L.disc(cx, cy, r, hx("#bfe6ff"))                 # стекло
    L.disc(cx - 1, cy - 1, r - 2, hx("#e8f6ff"))      # блик стекла
    L.ring(cx, cy, r, hx("#cfd4dd"), 1.6)             # металл оправы
    L.ring(cx, cy, r, hx("#9aa0ac"), 1.0)
    # галочка качества внутри лупы
    L.set(cx - 2, cy, hx("#1f9d4d")); L.set(cx - 1, cy + 1, hx("#1f9d4d"))
    L.set(cx, cy, hx("#1f9d4d")); L.set(cx + 1, cy - 1, hx("#1f9d4d")); L.set(cx + 2, cy - 2, hx("#1f9d4d"))
    # ручка лупы
    for i in range(4):
        L.set(cx + 4 + i, cy + 4 + i, hx("#7a5a2e"))
        L.set(cx + 5 + i, cy + 4 + i, hx("#5c4220"))

    L.outline()
    L.composite_over(badge)
    return badge


def agent_shturm():
    """Штурм — проверка качества. Боевой щит-крест с галочкой и молнией."""
    badge = make_badge(hx("#3a1416"), hx("#220a0c"), hx("#1a0708"), hx("#ff5a4d"))
    L = Layer()
    red = hx("#df4138"); rhi = hx("#f4766b"); rsh = hx("#a32a26")
    steel = hx("#c9d0dc"); stsh = hx("#8b93a3")
    gold = hx("#ffd23f")
    # щит-герб
    shield = [(16, 5), (25, 8), (25, 17), (16, 28), (7, 17), (7, 8)]
    L.poly(shield, red)
    # объём щита: блик слева, тень справа
    for y in range(6, 28):
        row = [x for x in range(SIZE) if L.get(x, y) == red]
        if row:
            L.set(min(row), y, rhi); L.set(min(row) + 1, y, rhi)
            L.set(max(row), y, rsh); L.set(max(row) - 1, y, rsh)
    # стальная кайма по верху
    L.hline(8, 24, 7, steel); L.set(8, 7, stsh); L.set(24, 7, stsh)
    L.hline(8, 24, 8, stsh)

    # большая галочка качества по центру (стальная с золотым контуром)
    chk = [(11, 17), (13, 17), (15, 20), (15, 20), (20, 11), (22, 11), (16, 23), (13, 23)]
    # рисуем галочку толстой ломаной
    def thick_line(x0, y0, x1, y1, c, w=2):
        steps = max(abs(x1 - x0), abs(y1 - y0))
        for s in range(steps + 1):
            t = s / max(1, steps)
            x = round(x0 + (x1 - x0) * t); y = round(y0 + (y1 - y0) * t)
            for dx in range(w):
                for dy in range(w):
                    L.set(x + dx, y + dy, c)
    thick_line(11, 18, 15, 22, gold, 3)
    thick_line(15, 22, 22, 12, gold, 3)
    # внутренний светлый штрих галочки
    thick_line(12, 18, 15, 21, hx("#fff0a8"), 1)
    thick_line(15, 21, 21, 13, hx("#fff0a8"), 1)

    # молния-штурм в правом верхнем углу — чёткий зигзаг
    bolt = {
        3: [25], 4: [24, 25], 5: [23, 24], 6: [22, 23, 24, 25],
        7: [24, 25], 8: [23, 24], 9: [22, 23, 24], 10: [23],
    }
    for y, xs in bolt.items():
        for x in xs:
            L.set(x, y, gold)
    # светлая жилка молнии
    for x, y in [(25, 4), (24, 5), (24, 7), (23, 8), (23, 9)]:
        L.set(x, y, hx("#fff0a8"))

    L.outline()
    L.composite_over(badge)
    return badge


AGENTS = {
    "pm_agent": ("PM · Гэндальф", agent_pm),
    "meeting_summarizer": ("Суммаризатор", agent_meeting),
    "board_audit_agent": ("Аудит доски", agent_audit),
    "shturm": ("Штурм · QA", agent_shturm),
}


def to_image(layer):
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    px = img.load()
    for y in range(SIZE):
        for x in range(SIZE):
            c = layer.px[y][x]
            if c is not None:
                px[x, y] = c
    return img


def main():
    imgs = {}
    for aid, (label, fn) in AGENTS.items():
        layer = fn()
        img = to_image(layer)
        img.save(os.path.join(OUT, f"{aid}.png"))
        big = img.resize((SIZE * 12, SIZE * 12), Image.NEAREST)
        big.save(os.path.join(OUT, f"{aid}@12x.png"))
        imgs[aid] = img
        print("saved", aid)

    # контактный лист: 4 в ряд, 8×, тёмный фон + подписи область
    scale = 8
    pad = 10
    cellw = SIZE * scale + pad
    sheet = Image.new("RGBA", (cellw * len(imgs) + pad, SIZE * scale + pad * 2),
                      (24, 26, 34, 255))
    x = pad
    for aid in AGENTS:
        big = imgs[aid].resize((SIZE * scale, SIZE * scale), Image.NEAREST)
        sheet.paste(big, (x, pad), big)
        x += cellw
    sheet.save(os.path.join(OUT, "contact.png"))
    print("saved contact.png")


if __name__ == "__main__":
    main()
