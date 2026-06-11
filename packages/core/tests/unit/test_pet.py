from __future__ import annotations

from core.pet import (
    compute_mood,
    compute_pet,
    evolution_tier,
    level_for_xp,
    xp_for_level,
)


def test_xp_for_level_monotonic():
    assert xp_for_level(1) == 0
    assert xp_for_level(2) == 50
    assert xp_for_level(3) > xp_for_level(2)
    assert xp_for_level(5) > xp_for_level(4)


def test_level_for_xp_boundaries():
    assert level_for_xp(0)["level"] == 1
    assert level_for_xp(49)["level"] == 1
    assert level_for_xp(50)["level"] == 2
    info = level_for_xp(50)
    assert info["xp_into_level"] == 0
    assert 0.0 <= info["progress"] <= 1.0


def test_level_for_xp_progress_midway():
    floor = xp_for_level(2)
    ceil = xp_for_level(3)
    mid = floor + (ceil - floor) // 2
    info = level_for_xp(mid)
    assert info["level"] == 2
    assert 0.4 <= info["progress"] <= 0.6


def test_evolution_tier_bands():
    assert evolution_tier(1) == (0, "Яйцо")
    assert evolution_tier(3) == (0, "Яйцо")
    assert evolution_tier(4)[0] == 1
    assert evolution_tier(100)[1] == "Легенда"  # capped


def test_compute_mood_clamped():
    assert compute_mood(resolved=0, overdue=0, in_progress=0) == 60
    assert compute_mood(resolved=10, overdue=0, in_progress=1) == 100  # clamps at 100
    assert compute_mood(resolved=0, overdue=10, in_progress=0) == 0  # clamps at 0


def test_compute_pet_snapshot():
    pet = compute_pet(resolved=5, overdue=1, in_progress=2)
    assert pet["xp"] == 75  # 5 * 15
    assert pet["level"] == level_for_xp(75)["level"]
    assert "mood" in pet and "tier_name" in pet
    assert 0 <= pet["mood"] <= 100
