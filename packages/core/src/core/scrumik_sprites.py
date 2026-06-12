"""Pixel-art data for «Скрамики», ported 1:1 from the frontend catalogs.

Source of truth on the frontend: ``apps/web-ui/src/lib/scrumiks/sprites.ts`` and
``cosmetics.ts``. This module mirrors the pixel grids + palettes so the backend can
rasterize a scrumik (via :mod:`core.scrumik_render`) for Telegram battle images,
without a browser. Keep in sync with the TS files if either changes.

Grid: 16×16. Base keys — ``o`` outline, ``g`` body, ``d`` shadow, ``h`` highlight,
``W`` white (sclera), ``P`` pupil (= outline color). Overlays declare their own
single-char keys in ``opal``; ``.`` is transparent.
"""

from __future__ import annotations

from typing import Any

_DOTS = "." * 16


def _rows(*parts: Any) -> list[str]:
    """Expand a mix of literal rows and ``("dots", n)`` markers into 16 rows."""
    out: list[str] = []
    for part in parts:
        if isinstance(part, tuple) and part and part[0] == "dots":
            out.extend([_DOTS] * int(part[1]))
        else:
            out.append(str(part))
    assert len(out) == 16, f"overlay must be 16 rows, got {len(out)}"
    return out


def _dots(n: int) -> tuple[str, int]:
    return ("dots", n)


# Shared silhouette — every row is exactly 16 chars.
SCRUMIK_BASE: list[str] = [
    "................",
    "................",
    "......oooo......",
    ".....oggggo.....",
    "....ohggggo.....",
    "...ohgggggggo...",
    "...oggggggggo...",
    "..oggggggggggo..",
    "..oggWPggWPggo..",
    "..oggPPggPPggo..",
    "..oggggggggggo..",
    "...oggggggggo...",
    "...oggggggddo...",
    "....oggggddo....",
    ".....oooooo.....",
    "................",
]

RARITY: dict[str, dict[str, str]] = {
    "common": {"label": "Common", "color": "#9ca3af"},
    "uncommon": {"label": "Uncommon", "color": "#22c55e"},
    "rare": {"label": "Rare", "color": "#3b82f6"},
    "epic": {"label": "Epic", "color": "#a855f7"},
    "legendary": {"label": "Legendary", "color": "#f59e0b"},
}


# Per-species feature overlays + palettes (mirror of sprites.ts LIST).
SCRUMIKS: dict[str, dict[str, Any]] = {
    "standupik": {
        "colors": {"o": "#173a2a", "g": "#7fdca6", "d": "#4faf7e", "h": "#b6f0cd"},
        "opal": {"b": "#ffd166", "A": "#2f8f63"},
        "overlay": _rows("........b.......", "........A.......", _dots(14)),
    },
    "dailyk": {
        "colors": {"o": "#3a2415", "g": "#a06f45", "d": "#7d5331", "h": "#c4905c"},
        "opal": {"S": "#e3eaf0", "F": "#f3e0c2"},
        "overlay": _rows(
            "......S..S......",
            ".......SS.......",
            "................",
            "......FFFF......",
            _dots(12),
        ),
    },
    "backlogik": {
        "colors": {"o": "#4a4030", "g": "#e8dcc0", "d": "#c5b591", "h": "#f5eed8"},
        "opal": {"y": "#ffe08a", "k": "#ffb3c6", "c": "#9be0e8"},
        "overlay": _rows(_dots(11), ".....yy..kk.....", ".......cc.......", _dots(3)),
    },
    "sprintik": {
        "colors": {"o": "#3a2410", "g": "#f59a4b", "d": "#cf7426", "h": "#ffc187"},
        "opal": {"R": "#e23b3b", "L": "#f4a94b"},
        "overlay": _rows(
            _dots(6),
            "....RRRRRRRR....",
            "...R............",
            "LL..............",
            "LL..............",
            _dots(6),
        ),
    },
    "retrik": {
        "colors": {"o": "#16302d", "g": "#3fb8ad", "d": "#2a8f86", "h": "#7fd8cf"},
        "opal": {"C": "#e6f4f2"},
        "overlay": _rows(
            "............CCC.",
            "...........CCCC.",
            "............CC..",
            "...........C....",
            "..........C.....",
            _dots(11),
        ),
    },
    "bagik": {
        "colors": {"o": "#2e1640", "g": "#a45fd0", "d": "#7e3fae", "h": "#c89bea"},
        "opal": {"A": "#7a3fa0", "b": "#c08ad8"},
        "overlay": _rows(".....b....b.....", ".....A....A.....", _dots(13), "....A.A..A.A...."),
    },
    "pokerik": {
        "colors": {"o": "#172547", "g": "#4a78d8", "d": "#2f55ad", "h": "#8fb0f0"},
        "opal": {"W": "#f4f1e8", "p": "#3a2a55"},
        "overlay": _rows(
            _dots(11), ".....W.W.W......", ".....WWWWW......", ".....p.p.p......", _dots(2)
        ),
    },
    "deployk": {
        "colors": {"o": "#34140f", "g": "#d24a3a", "d": "#a32f24", "h": "#f08070"},
        "opal": {"M": "#aab4c0", "N": "#cf4b2a", "f": "#ffb13b", "F": "#ffe26b"},
        "overlay": _rows(
            "................",
            "................",
            "......MMMM......",
            _dots(6),
            ".N............N.",
            _dots(4),
            ".......ff.......",
            "......fFFf......",
        ),
    },
    "velocik": {
        "colors": {"o": "#173a16", "g": "#54b94a", "d": "#379030", "h": "#8fe07f"},
        "opal": {"K": "#2a7a3a", "G": "#7fd06a"},
        "overlay": _rows(
            "................",
            ".....K.K.K......",
            _dots(6),
            ".............GG.",
            ".............G..",
            _dots(6),
        ),
    },
    "unikornik": {
        "colors": {"o": "#3a3550", "g": "#f3f0fb", "d": "#d6d0ea", "h": "#ffffff"},
        "opal": {
            "H": "#ffd24d",
            "q": "#ffe9a3",
            "*": "#fff7c2",
            "1": "#ff5d5d",
            "2": "#ffa84d",
            "3": "#ffe24d",
            "4": "#5dd97a",
            "5": "#5db8ff",
            "6": "#b08aff",
        },
        "overlay": _rows(
            "........H.......",
            ".......qH.......",
            "................",
            "............*...",
            ".............*..",
            ".1..............",
            ".2..............",
            "1...............",
            "3...............",
            "4...............",
            ".5.........*....",
            ".6..............",
            _dots(4),
        ),
    },
}


# Cosmetic overlays (mirror of cosmetics.ts LIST). Slot → one item equipped.
COSMETICS: dict[str, dict[str, Any]] = {
    "cap_red": {
        "slot": "head",
        "opal": {"C": "#e23b3b", "k": "#a31f1f"},
        "overlay": _rows(
            "................",
            "................",
            ".....CCCCCC.....",
            "....CkkkkkkC....",
            "...........CCC..",
            _dots(11),
        ),
    },
    "tophat": {
        "slot": "head",
        "opal": {"K": "#15171f", "r": "#e23b3b"},
        "overlay": _rows(".....KKKKKK.....", ".....KrrrrK.....", "....KKKKKKKK....", _dots(13)),
    },
    "crown": {
        "slot": "head",
        "opal": {"G": "#ffd24d", "o": "#b8860b"},
        "overlay": _rows("................", "....G.G.G.G.....", "....GGGGGGG.....", _dots(13)),
    },
    "shades": {
        "slot": "eyes",
        "opal": {"K": "#15171f", "w": "#ffffff"},
        "overlay": _rows(_dots(8), ".....KKKKKK.....", _dots(7)),
    },
    "chain_gold": {
        "slot": "neck",
        "opal": {"G": "#ffd24d"},
        "overlay": _rows(
            _dots(11), "....G......G....", ".....G....G.....", "......GGGG......", _dots(2)
        ),
    },
    "tie": {
        "slot": "neck",
        "opal": {"T": "#c62f3a"},
        "overlay": _rows(
            _dots(10), ".......TT.......", ".......TT.......", ".......TT.......", _dots(3)
        ),
    },
    "coffee": {
        "slot": "hand",
        "opal": {"M": "#e6e2d6", "c": "#6f4a2f"},
        "overlay": _rows(
            _dots(9), "...........MMM..", "...........McM..", "...........MMM..", _dots(4)
        ),
    },
    "aura_spark": {
        "slot": "aura",
        "opal": {"*": "#fff7c2"},
        "overlay": _rows(
            "................",
            "..*..........*..",
            _dots(4),
            "*..............*",
            _dots(4),
            "..*..........*..",
            _dots(4),
        ),
    },
}

# Painted back-to-front: background/aura → body → clothes → face/head.
SLOT_Z: list[str] = ["background", "aura", "hand", "neck", "eyes", "head"]


def sprite_for(species_id: str | None) -> dict[str, Any]:
    if species_id and species_id in SCRUMIKS:
        return SCRUMIKS[species_id]
    return SCRUMIKS["standupik"]


def equipped_overlays(equipped: dict[str, str] | None) -> list[dict[str, Any]]:
    """Resolve equipped ``{slot: itemId}`` into an ordered list of cosmetics to layer."""
    if not equipped:
        return []
    out: list[dict[str, Any]] = []
    for slot in SLOT_Z:
        item_id = equipped.get(slot)
        if item_id and item_id in COSMETICS:
            out.append(COSMETICS[item_id])
    return out
