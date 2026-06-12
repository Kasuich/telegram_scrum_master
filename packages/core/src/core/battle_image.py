"""Render «Битва скрамиков» results to PNG bytes for Telegram ``sendPhoto``.

Two pictures:

- :func:`render_leaderboard_png` — the royale ranking (medal, mini-scrumik, name,
  level, ⚡power bar).
- :func:`render_duel_png` — a 1-on-1 result (two scrumiks, HP bars, winner banner).

Fonts: we use Pillow's bundled DejaVu Sans (``load_default(size=…)``, Pillow ≥10.1)
which has Cyrillic — the system fonts referenced by the docs prototype don't exist in
the slim Docker images. Emoji are kept out of the rasterized text (DejaVu has no color
emoji); they live in the Telegram caption instead.
"""

from __future__ import annotations

import io
import os
from functools import lru_cache
from typing import Any

from core.scrumik_render import render_scrumik

# Vendored Cyrillic-capable font (Pillow's bundled default has no Cyrillic, and slim
# Docker images carry no system fonts). DejaVu Sans ships under a permissive license.
_FONT_PATH = os.path.join(os.path.dirname(__file__), "assets", "DejaVuSans.ttf")

# Palette (matches the web-ui dark theme).
_BG = (15, 17, 23)
_PANEL = (26, 29, 39)
_INK = (231, 235, 243)
_MUTE = (148, 158, 173)
_ACCENT = (124, 92, 255)
_HP = (86, 204, 130)
_HP_BG = (52, 56, 70)
_MEDALS = [(245, 179, 1), (205, 211, 218), (205, 127, 50)]  # gold / silver / bronze


@lru_cache(maxsize=16)
def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype(_FONT_PATH, size)
    except Exception:  # noqa: BLE001 — last-resort fallback (Latin-only)
        return ImageFont.load_default()


def _text_w(draw, text: str, font) -> float:
    return draw.textlength(text, font=font)


def _to_png(img) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _ellipsize(draw, text: str, font, max_w: float) -> str:
    if _text_w(draw, text, font) <= max_w:
        return text
    while text and _text_w(draw, text + "…", font) > max_w:
        text = text[:-1]
    return text + "…"


def render_leaderboard_png(team_name: str, ranked: list[dict[str, Any]], *, top: int = 10) -> bytes:
    from PIL import Image, ImageDraw

    rows = ranked[:top]
    W = 760
    MARGIN = 28
    HEADER_H = 96
    ROW_H = 70
    ROW_GAP = 12
    H = HEADER_H + MARGIN + len(rows) * (ROW_H + ROW_GAP) + MARGIN

    img = Image.new("RGB", (W, max(H, HEADER_H + 80)), _BG)
    draw = ImageDraw.Draw(img)
    f_title = _font(34)
    f_sub = _font(18)
    f_name = _font(24)
    f_meta = _font(17)
    f_rank = _font(26)

    draw.text((MARGIN, 24), "АРЕНА СКРАМИКОВ", font=f_title, fill=_INK)
    draw.text((MARGIN, 64), f"Турнир команды · {team_name}", font=f_sub, fill=_MUTE)

    max_power = max((int(r.get("power", 0)) for r in rows), default=1) or 1
    y = HEADER_H + MARGIN
    for r in rows:
        rank = int(r.get("rank", 0))
        draw.rounded_rectangle([MARGIN, y, W - MARGIN, y + ROW_H], radius=14, fill=_PANEL)

        # Rank badge.
        badge_c = _MEDALS[rank - 1] if 1 <= rank <= 3 else (60, 66, 82)
        bx = MARGIN + 18
        draw.ellipse([bx, y + 17, bx + 36, y + 53], fill=badge_c)
        num = str(rank)
        draw.text(
            (bx + 18 - _text_w(draw, num, f_rank) / 2, y + 18),
            num,
            font=f_rank,
            fill=(20, 22, 30),
        )

        # Mini scrumik.
        sprite = render_scrumik(r.get("species_id"), equipped=r.get("equipped") or {}, px=3)
        img.paste(sprite, (bx + 52, y + (ROW_H - sprite.height) // 2), sprite)

        # Name + species/level.
        tx = bx + 52 + sprite.width + 16
        name = _ellipsize(draw, str(r.get("name", "—")), f_name, 300)
        draw.text((tx, y + 12), name, font=f_name, fill=_INK)
        meta = f"{r.get('species_name', '')} · ур. {r.get('level', 1)}"
        draw.text((tx, y + 42), meta, font=f_meta, fill=_MUTE)

        # Power bar (right side).
        power = int(r.get("power", 0))
        bar_x0, bar_x1 = W - MARGIN - 200, W - MARGIN - 20
        bar_y = y + 44
        draw.rounded_rectangle([bar_x0, bar_y, bar_x1, bar_y + 12], radius=6, fill=_HP_BG)
        fill_w = int((bar_x1 - bar_x0) * power / max_power)
        if fill_w > 0:
            draw.rounded_rectangle(
                [bar_x0, bar_y, bar_x0 + fill_w, bar_y + 12], radius=6, fill=_ACCENT
            )
        ptext = f"{power}"
        draw.text((bar_x1 - _text_w(draw, ptext, f_meta), y + 12), ptext, font=f_meta, fill=_INK)
        draw.text((bar_x0, y + 12), "сила", font=f_meta, fill=_MUTE)

        y += ROW_H + ROW_GAP

    return _to_png(img)


def render_duel_png(duel: dict[str, Any]) -> bytes:
    from PIL import Image, ImageDraw

    winner = duel.get("winner") or {}
    loser = duel.get("loser") or {}
    hp = duel.get("hp") or {}

    W, H = 760, 420
    img = Image.new("RGB", (W, H), _BG)
    draw = ImageDraw.Draw(img)
    f_title = _font(34)
    f_name = _font(26)
    f_meta = _font(18)
    f_vs = _font(40)
    f_banner = _font(24)

    draw.text((28, 22), "ДУЭЛЬ СКРАМИКОВ", font=f_title, fill=_INK)

    def panel(side_x: int, fighter: dict[str, Any], is_winner: bool) -> None:
        px0 = side_x
        draw.rounded_rectangle([px0, 90, px0 + 300, 360], radius=18, fill=_PANEL)
        sprite = render_scrumik(
            fighter.get("species_id"), equipped=fighter.get("equipped") or {}, px=8
        )
        img.paste(sprite, (px0 + 150 - sprite.width // 2, 110), sprite)
        name = _ellipsize(draw, str(fighter.get("name", "—")), f_name, 280)
        draw.text((px0 + 150 - _text_w(draw, name, f_name) / 2, 250), name, font=f_name, fill=_INK)
        meta = f"{fighter.get('species_name', '')} · ур. {fighter.get('level', 1)}"
        draw.text((px0 + 150 - _text_w(draw, meta, f_meta) / 2, 282), meta, font=f_meta, fill=_MUTE)

        # HP bar.
        hp_val = int(hp.get(fighter.get("name"), fighter.get("hp_left", 0)))
        bar_x0, bar_x1 = px0 + 30, px0 + 270
        draw.rounded_rectangle([bar_x0, 318, bar_x1, 332], radius=7, fill=_HP_BG)
        ratio = max(0.0, min(1.0, hp_val / 200.0))
        if ratio > 0:
            draw.rounded_rectangle(
                [bar_x0, 318, bar_x0 + int((bar_x1 - bar_x0) * ratio), 332], radius=7, fill=_HP
            )
        draw.text((bar_x0, 336), f"HP {hp_val}", font=f_meta, fill=_MUTE)

        if is_winner:
            draw.text(
                (px0 + 150 - _text_w(draw, "ПОБЕДА", f_banner) / 2, 62),
                "ПОБЕДА",
                font=f_banner,
                fill=(245, 179, 1),
            )

    panel(28, winner, True)
    panel(W - 28 - 300, loser, False)
    draw.text((W / 2 - _text_w(draw, "VS", f_vs) / 2, 200), "VS", font=f_vs, fill=_ACCENT)

    return _to_png(img)
