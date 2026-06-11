#!/usr/bin/env python3
"""Рендер витрины украшений: демо-блоб с каждым предметом + подпись. Источник — JSON из node."""
import json
import os
import subprocess

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))

# Выгружаем данные из JS через node (единый источник правды).
subprocess.run(
    ["node", "-e",
     "const b=require('./sprites.js');const c=require('./cosmetics.js');"
     "require('fs').writeFileSync('cosmetics.json',JSON.stringify("
     "{base:b.SCRUMIK_BASE, items:c.COSMETICS, demo:c.DEMO_COLORS, rarity:c.RARITY}));"],
    cwd=HERE, check=True,
)
data = json.load(open(os.path.join(HERE, "cosmetics.json")))
BASE, ITEMS, DEMO, RARITY = data["base"], data["items"], data["demo"], data["rarity"]
N = len(BASE)

PX = 11
SPRITE = N * PX
PAD = 24
LABEL_H = 58
TILE_W = SPRITE + PAD * 2
TILE_H = SPRITE + PAD + LABEL_H
COLS = 4
ROWS = (len(ITEMS) + COLS - 1) // COLS
GAP = 18
TITLE_H = 70
MARGIN = 26

BG = (15, 17, 23)
TILE_BG = (26, 29, 39)
INK = (231, 235, 243)
MUTED = (154, 163, 181)

W = MARGIN * 2 + COLS * TILE_W + (COLS - 1) * GAP
H = TITLE_H + MARGIN + ROWS * TILE_H + (ROWS - 1) * GAP + MARGIN
img = Image.new("RGB", (W, H), BG)
draw = ImageDraw.Draw(img)

ARIAL = "/System/Library/Fonts/Supplemental/Arial.ttf"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
f_title = ImageFont.truetype(BOLD, 30)
f_name = ImageFont.truetype(BOLD, 17)
f_meta = ImageFont.truetype(ARIAL, 13)


def hx(c):
    c = c.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def cell(item, r, c):
    ov = item["overlay"]
    if r < len(ov) and c < len(ov[r]) and ov[r][c] != ".":
        return hx(item["opal"].get(ov[r][c], "#ff00ff"))
    bch = BASE[r][c]
    if bch == ".":
        return None
    if bch == "W":
        return (255, 255, 255)
    if bch == "P":
        return hx(DEMO["o"])
    return hx(DEMO.get(bch, "#ff00ff"))


draw.text((MARGIN, 22), "Скрамики — украшения", font=f_title, fill=INK)
draw.text((MARGIN + 330, 33), "на нейтральном демо-скрамике", font=f_meta, fill=MUTED)

for i, item in enumerate(ITEMS):
    col, row = i % COLS, i // COLS
    tx = MARGIN + col * (TILE_W + GAP)
    ty = TITLE_H + MARGIN + row * (TILE_H + GAP)
    rar = RARITY[item["rarity"]]
    rcol = hx(rar["color"])
    draw.rounded_rectangle([tx, ty, tx + TILE_W, ty + TILE_H], radius=14, fill=TILE_BG,
                           outline=rcol, width=2)
    ox, oy = tx + PAD, ty + PAD
    for r in range(N):
        for c in range(N):
            col_rgb = cell(item, r, c)
            if col_rgb is None:
                continue
            x0, y0 = ox + c * PX, oy + r * PX
            draw.rectangle([x0, y0, x0 + PX - 1, y0 + PX - 1], fill=col_rgb)
    cx = tx + TILE_W // 2
    ny = oy + SPRITE + 8
    name = item["name"]
    draw.text((cx - draw.textlength(name, font=f_name) / 2, ny), name, font=f_name, fill=INK)
    meta = f'{item["slot"]} · {rar["label"]} · {item["price"]}🪙'
    draw.text((cx - draw.textlength(meta, font=f_meta) / 2, ny + 24), meta, font=f_meta, fill=rcol)

out = os.path.join(HERE, "cosmetics_all.png")
img.save(out)
print("saved:", out, img.size)
