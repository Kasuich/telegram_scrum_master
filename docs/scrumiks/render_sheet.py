#!/usr/bin/env python3
"""Рендер композита всех скрамиков в один PNG с подписями. Источник — sprites.json."""
import json
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
data = json.load(open(os.path.join(HERE, "sprites.json")))
BASE, SPECIES, RARITY = data["base"], data["species"], data["rarity"]
N = len(BASE)  # 16

PX = 11                      # размер пикселя спрайта
SPRITE = N * PX              # 176
PAD = 26                     # отступ внутри плитки
LABEL_H = 56                 # высота подписи
TILE_W = SPRITE + PAD * 2    # 228
TILE_H = SPRITE + PAD + LABEL_H
COLS = 5
ROWS = (len(SPECIES) + COLS - 1) // COLS
GAP = 18
TITLE_H = 76
MARGIN = 28

BG = (15, 17, 23)
TILE_BG = (26, 29, 39)
INK = (231, 235, 243)
MUTED = (154, 163, 181)

W = MARGIN * 2 + COLS * TILE_W + (COLS - 1) * GAP
H = TITLE_H + MARGIN + ROWS * TILE_H + (ROWS - 1) * GAP + MARGIN

img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)

ARIAL = "/System/Library/Fonts/Supplemental/Arial.ttf"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.truetype(ARIAL, size)
f_title = font(BOLD, 34)
f_name = font(BOLD, 20)
f_meta = font(ARIAL, 14)

def hx(c):
    c = c.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))

def cell_color(sp, r, c):
    ov = sp.get("overlay")
    if ov and r < len(ov) and c < len(ov[r]):
        ch = ov[r][c]
        if ch != ".":
            return hx(sp["opal"].get(ch, "#ff00ff"))
    bch = BASE[r][c]
    if bch == ".":
        return None
    if bch == "W":
        return (255, 255, 255)
    if bch == "P":
        return hx(sp["colors"]["o"])
    col = sp["colors"].get(bch)
    return hx(col) if col else (255, 0, 255)

# Заголовок
d.text((MARGIN, 24), "Скрамики — 10 видов", font=f_title, fill=INK)
d.text((MARGIN + 360, 38), "виртуальные питомцы команды", font=f_meta, fill=MUTED)

for i, sp in enumerate(SPECIES):
    col, row = i % COLS, i // COLS
    tx = MARGIN + col * (TILE_W + GAP)
    ty = TITLE_H + MARGIN + row * (TILE_H + GAP)
    rar = RARITY[sp["rarity"]]
    rcol = hx(rar["color"])

    # плитка + рамка редкости
    d.rounded_rectangle([tx, ty, tx + TILE_W, ty + TILE_H], radius=14, fill=TILE_BG,
                        outline=rcol, width=2)

    # спрайт
    ox, oy = tx + PAD, ty + PAD
    for r in range(N):
        for c in range(N):
            col_rgb = cell_color(sp, r, c)
            if col_rgb is None:
                continue
            x0, y0 = ox + c * PX, oy + r * PX
            d.rectangle([x0, y0, x0 + PX - 1, y0 + PX - 1], fill=col_rgb)

    # подпись
    cx = tx + TILE_W // 2
    ny = oy + SPRITE + 8
    name = sp["name"]
    wname = d.textlength(name, font=f_name)
    d.text((cx - wname / 2, ny), name, font=f_name, fill=INK)
    meta = f'{rar["label"]} · {sp["chance"]}'
    wmeta = d.textlength(meta, font=f_meta)
    d.text((cx - wmeta / 2, ny + 26), meta, font=f_meta, fill=rcol)

out = os.path.join(HERE, "scrumiks_all.png")
img.save(out)
print("saved:", out, img.size)
