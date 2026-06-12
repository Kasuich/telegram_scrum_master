"""Rasterize a «Скрамик» (species + equipped cosmetics) to a PIL image.

Layering mirrors the frontend ``PixelSprite`` and the docs prototype
``docs/scrumiks/render_dressed.py``: topmost cosmetic wins, then the species
feature overlay, then the shared body. Pure pixels, no text — used by
:mod:`core.battle_image` to compose Telegram battle pictures.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.scrumik_sprites import SCRUMIK_BASE, equipped_overlays, sprite_for

if TYPE_CHECKING:  # pragma: no cover - typing only
    from PIL import Image as _ImageType

_N = 16
_MAGENTA = (255, 0, 255)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _pixel_color(
    sprite: dict,
    overlays: list[dict],
    r: int,
    c: int,
) -> tuple[int, int, int] | None:
    """Resolve the RGB of pixel (r, c), or ``None`` if transparent.

    Cosmetics are checked top-of-stack first (``overlays`` is in back-to-front
    paint order, so we walk it reversed), then the species feature, then the body.
    """
    for item in reversed(overlays):
        grid = item["overlay"]
        if r < len(grid) and c < len(grid[r]):
            ch = grid[r][c]
            if ch != ".":
                return _hex_to_rgb(item["opal"].get(ch, "#ff00ff"))

    feature = sprite.get("overlay")
    if feature and r < len(feature) and c < len(feature[r]):
        ch = feature[r][c]
        if ch != ".":
            return _hex_to_rgb(sprite["opal"].get(ch, "#ff00ff"))

    base_ch = SCRUMIK_BASE[r][c]
    if base_ch == ".":
        return None
    if base_ch == "W":
        return (255, 255, 255)
    if base_ch == "P":
        return _hex_to_rgb(sprite["colors"]["o"])
    return _hex_to_rgb(sprite["colors"].get(base_ch, "#ff00ff"))


def render_scrumik(
    species_id: str | None,
    *,
    equipped: dict[str, str] | None = None,
    px: int = 8,
) -> "_ImageType.Image":
    """Render the scrumik as an RGBA image of size ``16*px`` (transparent bg)."""
    from PIL import Image, ImageDraw

    sprite = sprite_for(species_id)
    overlays = equipped_overlays(equipped)
    size = _N * px
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for r in range(_N):
        for c in range(_N):
            rgb = _pixel_color(sprite, overlays, r, c)
            if rgb is None:
                continue
            x0, y0 = c * px, r * px
            draw.rectangle([x0, y0, x0 + px - 1, y0 + px - 1], fill=(*rgb, 255))
    return img
