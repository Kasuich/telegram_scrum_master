"""«Скрамик» — leveling/mood math for the team pet.

Pure functions so the leveling system can be unit-tested and reused without a DB.
Two independent scales (classic tamagotchi):

- **level** — permanent progress derived from accumulated XP; only grows.
- **mood**  — current 0..100 state; rises with activity, falls with overdue work.

XP is earned from real PM activity (closed issues), so the pet reflects the board.
"""

from __future__ import annotations

from typing import Any

XP_PER_RESOLVED = 15

# Evolution tiers by level band (every 3 levels → next stage).
_TIER_NAMES = ["Яйцо", "Детёныш", "Подросток", "Взрослый", "Мастер", "Легенда"]


def xp_for_level(level: int) -> int:
    """Cumulative XP required to *reach* ``level`` (level 1 starts at 0)."""
    if level <= 1:
        return 0
    return round(50 * (level - 1) ** 1.5)


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
    mood = 60 + min(resolved, 10) * 4 - overdue * 12 + (5 if in_progress > 0 else 0)
    return max(0, min(100, round(mood)))


def compute_pet(*, resolved: int, overdue: int, in_progress: int) -> dict[str, Any]:
    """Derive a full pet snapshot from current board activity counts."""
    xp = max(0, resolved) * XP_PER_RESOLVED
    progress = level_for_xp(xp)
    tier, tier_name = evolution_tier(progress["level"])
    return {
        **progress,
        "mood": compute_mood(resolved=resolved, overdue=overdue, in_progress=in_progress),
        "tier": tier,
        "tier_name": tier_name,
    }
