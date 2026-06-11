#!/usr/bin/env python3
"""Демонстрация послойного рендера: скрамики в украшениях. Источник — sprites.js + cosmetics.js."""
import json
import os
import subprocess

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
subprocess.run(
    ["node", "-e",
     "const b=require('./sprites.js');const c=require('./cosmetics.js');"
     "require('fs').writeFileSync('dressed.json',JSON.stringify("
     "{base:b.SCRUMIK_BASE, species:b.SCRUMIKS, items:c.COSMETICS}));"],
    cwd=HERE, check=True,
)
data = json.load(open(os.path.join(HERE, "dressed.json")))
BASE = data["base"]
SP = {s["id"]: s for s in data["species"]}
IT = {i["id"]: i for i in data["items"]}
N = len(BASE)

# (вид, [надетые предметы], подпись)
COMBOS = [
    ("standupik", ["cap_red"], "Стендапик + кепка"),
    ("sprintik", ["shades"], "Спринтик + очки"),
    ("dailyk", ["coffee"], "Дейлик + кофе"),
    ("bagik", ["tophat"], "Багик + цилиндр"),
    ("deployk", ["chain_gold", "shades"], "Деплоик + цепь + очки"),
    ("unikornik", ["crown", "aura_spark"], "Юникорник + корона + аура"),
]

PX = 12
SPRITE = N * PX
PAD = 24
LABEL_H = 34
TILE_W = SPRITE + PAD * 2
TILE_H = SPRITE + PAD + LABEL_H
COLS = 3
ROWS = (len(COMBOS) + COLS - 1) // COLS
GAP = 18
TITLE_H = 60
MARGIN = 26
BG, TILE_BG, INK = (15, 17, 23), (26, 29, 39), (231, 235, 243)

W = MARGIN * 2 + COLS * TILE_W + (COLS - 1) * GAP
H = TITLE_H + MARGIN + ROWS * TILE_H + (ROWS - 1) * GAP + MARGIN
img = Image.new("RGB", (W, H), BG)
draw = ImageDraw.Draw(img)
f_title = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 28)
f_name = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 16)

SLOT_Z = ["background", "aura", "hand", "neck", "eyes", "head"]


def hx(c):
    c = c.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def render(sp, items, r, c):
    # верхняя косметика → фича вида → тело
    for item in reversed(items):
        ov = item["overlay"]
        if r < len(ov) and c < len(ov[r]) and ov[r][c] != ".":
            return hx(item["opal"].get(ov[r][c], "#ff00ff"))
    fov = sp.get("overlay")
    if fov and r < len(fov) and c < len(fov[r]) and fov[r][c] != ".":
        return hx(sp["opal"].get(fov[r][c], "#ff00ff"))
    bch = BASE[r][c]
    if bch == ".":
        return None
    if bch == "W":
        return (255, 255, 255)
    if bch == "P":
        return hx(sp["colors"]["o"])
    return hx(sp["colors"].get(bch, "#ff00ff"))


draw.text((MARGIN, 18), "Скрамики в украшениях — послойный рендер", font=f_title, fill=INK)

for i, (sid, item_ids, label) in enumerate(COMBOS):
    sp = SP[sid]
    items = [IT[x] for x in item_ids]
    ordered = sorted(items, key=lambda it: SLOT_Z.index(it["slot"]))
    col, row = i % COLS, i // COLS
    tx = MARGIN + col * (TILE_W + GAP)
    ty = TITLE_H + MARGIN + row * (TILE_H + GAP)
    draw.rounded_rectangle([tx, ty, tx + TILE_W, ty + TILE_H], radius=14, fill=TILE_BG)
    ox, oy = tx + PAD, ty + PAD
    for r in range(N):
        for c in range(N):
            col_rgb = render(sp, ordered, r, c)
            if col_rgb is None:
                continue
            draw.rectangle([ox + c * PX, oy + r * PX, ox + c * PX + PX - 1, oy + r * PX + PX - 1], fill=col_rgb)
    cx = tx + TILE_W // 2
    draw.text((cx - draw.textlength(label, font=f_name) / 2, oy + SPRITE + 8), label, font=f_name, fill=INK)

out = os.path.join(HERE, "scrumiks_dressed.png")
img.save(out)
print("saved:", out, img.size)
