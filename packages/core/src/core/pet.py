"""«Скрамик» — leveling, species, stats and mood math for the team pet.

Pure functions so the system can be unit-tested and reused without a DB.

Three independent layers:

- **level** — permanent progress from *lifetime* XP (closed issues). Only grows.
- **mood**  — current 0..100 state; rises with activity, falls with overdue work.
- **species + stats** — each user gets one of 10 collectible species (random by
  rarity, deterministic from user id). Stats (0..100) grow with level and reflect
  real board behaviour, modulated by the species' affinities (its "character").

Tuning is env-driven so the MVP can fast-forward levels for testing:

- ``PET_FAST_MODE=true``        — preset: ~1-2 closed issues per level.
- ``PET_XP_PER_RESOLVED``       — XP per closed issue (default 15, fast 100).
- ``PET_LEVEL_CURVE_BASE``      — level curve base   (default 50, fast 10).
- ``PET_LEVEL_CURVE_EXP``       — level curve exponent (default 1.5, fast 1.2).
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

# Backwards-compatible default; effective value comes from ``xp_per_resolved()``.
XP_PER_RESOLVED = 15

# Evolution tiers by level band (every 3 levels → next stage).
_TIER_NAMES = ["Яйцо", "Детёныш", "Подросток", "Взрослый", "Мастер", "Легенда"]

STAT_KEYS = ("velocity", "focus", "reliability", "stamina")


# ---------------------------------------------------------------------------
# Tuning (env-driven, read at call time so tests/deploys can override)
# ---------------------------------------------------------------------------


def _fast_mode() -> bool:
    # Default ON for the MVP — fast leveling/coins out of the box. Set false for prod.
    return os.getenv("PET_FAST_MODE", "true").strip().lower() in ("1", "true", "yes")


def _env_num(name: str, fast: float, normal: float) -> float:
    raw = os.getenv(name)
    if raw is not None and raw.strip():
        try:
            return float(raw)
        except ValueError:
            pass
    return fast if _fast_mode() else normal


def xp_per_resolved() -> int:
    return int(_env_num("PET_XP_PER_RESOLVED", fast=100, normal=XP_PER_RESOLVED))


def _curve() -> tuple[float, float]:
    base = _env_num("PET_LEVEL_CURVE_BASE", fast=10, normal=50)
    exp = _env_num("PET_LEVEL_CURVE_EXP", fast=1.2, normal=1.5)
    return base, exp


# ---------------------------------------------------------------------------
# Leveling
# ---------------------------------------------------------------------------


def xp_for_level(level: int) -> int:
    """Cumulative XP required to *reach* ``level`` (level 1 starts at 0)."""
    if level <= 1:
        return 0
    base, exp = _curve()
    return round(base * (level - 1) ** exp)


def level_for_xp(xp: int) -> dict[str, Any]:
    """Resolve total XP into level + progress toward the next level."""
    xp = max(0, int(xp))
    level = 1
    while xp_for_level(level + 1) <= xp:
        level += 1
    floor = xp_for_level(level)
    ceil = xp_for_level(level + 1)
    span = max(1, ceil - floor)
    into = xp - floor
    return {
        "level": level,
        "xp": xp,
        "xp_into_level": into,
        "xp_for_next": span,
        "progress": round(into / span, 3),
    }


def evolution_tier(level: int) -> tuple[int, str]:
    tier = max(0, (level - 1) // 3)
    name = _TIER_NAMES[min(tier, len(_TIER_NAMES) - 1)]
    return tier, name


def compute_mood(*, resolved: int, overdue: int, in_progress: int) -> int:
    """Current mood 0..100: closures feed Скрамик, overdue work upsets it."""
    resolved = int(resolved or 0)
    overdue = int(overdue or 0)
    in_progress = int(in_progress or 0)
    mood = 60 + min(resolved, 10) * 4 - overdue * 12 + (5 if in_progress > 0 else 0)
    return max(0, min(100, round(mood)))


# ---------------------------------------------------------------------------
# Species (collectible, rarity-weighted, deterministic per user)
# ---------------------------------------------------------------------------

# weight is per-mille (sum = 1000). asset_key matches the frontend sprite id.
# affinity multiplies the per-stat baseline (the species' "character").
SPECIES: list[dict[str, Any]] = [
    {
        "id": "standupik", "name": "Стендапик", "rarity": "common", "weight": 150,
        "desc": "Готов рассказать, что делал вчера — даже если вчера был выходной.",
        "affinity": {"focus": 1.25},
    },
    {
        "id": "dailyk", "name": "Дейлик", "rarity": "common", "weight": 150,
        "desc": "Работает на кофеине. Нет кофе — нет дейли.",
        "affinity": {"stamina": 1.25},
    },
    {
        "id": "backlogik", "name": "Бэклогик", "rarity": "common", "weight": 150,
        "desc": "Помнит все 247 задач из бэклога. Отдавать не торопится.",
        "affinity": {"reliability": 1.25},
    },
    {
        "id": "sprintik", "name": "Спринтик", "rarity": "uncommon", "weight": 130,
        "desc": "Бежит к дедлайну на максималках. Дедлайн, правда, быстрее.",
        "affinity": {"velocity": 1.4},
    },
    {
        "id": "retrik", "name": "Ретрик", "rarity": "uncommon", "weight": 130,
        "desc": "Знает все грабли команды наизусть. Расскажет в пятницу. Может быть.",
        "affinity": {"focus": 1.2, "reliability": 1.2},
    },
    {
        "id": "bagik", "name": "Багик", "rarity": "rare", "weight": 90,
        "desc": "Закрываешь одного — на ревью вылезает трое. Обаятельный.",
        "affinity": {"velocity": 1.35, "reliability": 0.8},
    },
    {
        "id": "pokerik", "name": "Покерик", "rarity": "rare", "weight": 90,
        "desc": "Всё оценивает в 8 стори-поинтов. На всякий случай.",
        "affinity": {"focus": 1.25},
    },
    {
        "id": "deployk", "name": "Деплоик", "rarity": "epic", "weight": 45,
        "desc": "Катит в прод по пятницам в 17:59. И не боится.",
        "affinity": {"velocity": 1.3, "stamina": 1.2},
    },
    {
        "id": "velocik", "name": "Велоцик", "rarity": "epic", "weight": 45,
        "desc": "Его burndown всегда вниз, а график велосити — вверх и вправо.",
        "affinity": {"velocity": 1.2, "focus": 1.2, "reliability": 1.2, "stamina": 1.2},
    },
    {
        "id": "unikornik", "name": "Юникорник", "rarity": "legendary", "weight": 20,
        "desc": "Закрывает задачи даже во время сна. Вживую видели только на демо.",
        "affinity": {"velocity": 1.3, "focus": 1.3, "reliability": 1.3, "stamina": 1.3},
    },
]

SPECIES_BY_ID: dict[str, dict[str, Any]] = {s["id"]: s for s in SPECIES}

RARITY_RANK = {"common": 0, "uncommon": 1, "rare": 2, "epic": 3, "legendary": 4}


def roll_species(seed: str) -> str:
    """Deterministically assign a species from ``seed`` (e.g. user id), weighted by rarity.

    Stable for a given seed → no re-rolls, reproducible, no DB roll needed.
    """
    digest = hashlib.sha256(str(seed).encode("utf-8")).digest()
    total = sum(s["weight"] for s in SPECIES)
    point = int.from_bytes(digest[:8], "big") % total
    cursor = 0
    for species in SPECIES:
        cursor += species["weight"]
        if point < cursor:
            return species["id"]
    return SPECIES[-1]["id"]


def species_info(species_id: str | None) -> dict[str, Any]:
    species = SPECIES_BY_ID.get(species_id or "", SPECIES[0])
    return {
        "id": species["id"],
        "name": species["name"],
        "rarity": species["rarity"],
        "rarity_rank": RARITY_RANK[species["rarity"]],
        "desc": species["desc"],
    }


# ---------------------------------------------------------------------------
# Stats (grow with level, reflect the board, shaped by species affinities)
# ---------------------------------------------------------------------------

_STAT_LABELS = {
    "velocity": "Скорость",
    "focus": "Фокус",
    "reliability": "Надёжность",
    "stamina": "Выносливость",
}


def compute_stats(
    *,
    level: int,
    resolved: int,
    overdue: int,
    in_progress: int,
    streak_days: int = 0,
    affinity: dict[str, float] | None = None,
) -> dict[str, int]:
    """Four 0..100 stats: a baseline from level, a real-board signal, x species affinity."""
    affinity = affinity or {}
    # Coerce None → 0/1 (a fresh PetState object has unset, None-valued columns before
    # its first DB flush, e.g. streak_days for a user's very first pet).
    level = int(level or 1)
    resolved = int(resolved or 0)
    overdue = int(overdue or 0)
    in_progress = int(in_progress or 0)
    streak_days = int(streak_days or 0)
    baseline = min(95, 28 + level * 5)  # grows with level, soft-capped

    signals = {
        "velocity": min(resolved, 12) * 2,
        "focus": 10 if in_progress <= 2 else -min(in_progress * 3, 18),
        "reliability": (10 if overdue == 0 else 0) - min(overdue * 8, 40),
        "stamina": min(streak_days, 14) * 2,
    }

    out: dict[str, int] = {}
    for key in STAT_KEYS:
        value = (baseline + signals[key]) * affinity.get(key, 1.0)
        out[key] = max(5, min(100, round(value)))
    return out


def stat_labels() -> dict[str, str]:
    return dict(_STAT_LABELS)


# ---------------------------------------------------------------------------
# Cosmetics + coin economy (buy items earned from closing tasks)
# ---------------------------------------------------------------------------

# Catalog metadata only; the pixel overlays live on the frontend (cosmetics.ts).
# slot — one item equipped per slot. price — in скрамкоины.
COSMETICS: list[dict[str, Any]] = [
    {"id": "cap_red", "name": "Кепка", "slot": "head", "rarity": "common", "price": 60},
    {"id": "tophat", "name": "Цилиндр", "slot": "head", "rarity": "rare", "price": 220},
    {"id": "crown", "name": "Корона", "slot": "head", "rarity": "legendary", "price": 900},
    {"id": "shades", "name": "Очки «deal-with-it»", "slot": "eyes", "rarity": "uncommon", "price": 120},  # noqa: E501
    {"id": "chain_gold", "name": "Золотая цепь", "slot": "neck", "rarity": "epic", "price": 400},
    {"id": "tie", "name": "Галстук", "slot": "neck", "rarity": "common", "price": 50},
    {"id": "coffee", "name": "Кружка кофе", "slot": "hand", "rarity": "common", "price": 70},
    {"id": "aura_spark", "name": "Аура искр", "slot": "aura", "rarity": "epic", "price": 500},
]

COSMETICS_BY_ID: dict[str, dict[str, Any]] = {c["id"]: c for c in COSMETICS}

COINS_PER_RESOLVED = 20  # default; effective value from coins_per_resolved()
COINS_PER_LEVEL = 50  # level-up bonus


def coins_per_resolved() -> int:
    return int(_env_num("PET_COINS_PER_RESOLVED", fast=100, normal=COINS_PER_RESOLVED))


def coins_earned(*, lifetime_resolved: int, level: int) -> int:
    """Total скрамкоины ever earned: from closed tasks + a level-up bonus."""
    return max(0, lifetime_resolved) * coins_per_resolved() + max(0, level - 1) * COINS_PER_LEVEL


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def snapshot_from_xp(
    *,
    xp: int,
    resolved: int,
    overdue: int,
    in_progress: int,
    streak_days: int = 0,
    species_id: str | None = None,
) -> dict[str, Any]:
    """Build a full pet snapshot from an explicit total ``xp``.

    ``xp`` drives level/tier (lifetime, monotonic, or dev-granted). ``resolved`` /
    ``overdue`` / ``in_progress`` are current board signals for mood + stats.
    """
    progress = level_for_xp(xp)
    tier, tier_name = evolution_tier(progress["level"])
    species = species_info(species_id)
    affinity = SPECIES_BY_ID.get(species["id"], {}).get("affinity", {})
    stats = compute_stats(
        level=progress["level"],
        resolved=resolved,
        overdue=overdue,
        in_progress=in_progress,
        streak_days=streak_days,
        affinity=affinity,
    )
    return {
        **progress,
        "mood": compute_mood(resolved=resolved, overdue=overdue, in_progress=in_progress),
        "tier": tier,
        "tier_name": tier_name,
        "species": species,
        "stats": stats,
    }


def compute_pet(
    *,
    resolved: int,
    overdue: int,
    in_progress: int,
    streak_days: int = 0,
    species_id: str | None = None,
) -> dict[str, Any]:
    """Snapshot where XP is derived from the *lifetime* ``resolved`` count (monotonic)."""
    return snapshot_from_xp(
        xp=max(0, resolved) * xp_per_resolved(),
        resolved=resolved,
        overdue=overdue,
        in_progress=in_progress,
        streak_days=streak_days,
        species_id=species_id,
    )
