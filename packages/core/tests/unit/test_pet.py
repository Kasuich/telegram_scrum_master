from __future__ import annotations

import pytest
from core.pet import (
    COSMETICS,
    COSMETICS_BY_ID,
    SPECIES,
    SPECIES_BY_ID,
    coins_earned,
    compute_mood,
    compute_pet,
    compute_stats,
    evolution_tier,
    level_for_xp,
    roll_species,
    snapshot_from_xp,
    species_info,
    xp_for_level,
    xp_per_resolved,
)


@pytest.fixture(autouse=True)
def _prod_pet_mode(monkeypatch):
    """Default tests to the prod (non-fast) formula; fast-mode tests override locally."""
    monkeypatch.setenv("PET_FAST_MODE", "false")


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
    assert pet["species"]["id"] in SPECIES_BY_ID
    assert set(pet["stats"]) == {"velocity", "focus", "reliability", "stamina"}


def test_species_weights_sum_to_1000():
    assert sum(s["weight"] for s in SPECIES) == 1000
    assert len(SPECIES) == 10


def test_roll_species_deterministic_and_valid():
    seed = "11111111-2222-3333-4444-555555555555"
    first = roll_species(seed)
    assert first == roll_species(seed)  # stable
    assert first in SPECIES_BY_ID


def test_roll_species_covers_rarities():
    rolled = {roll_species(f"user-{i}") for i in range(2000)}
    # With 2000 seeds every species should appear, including the 2% legendary.
    assert rolled == set(SPECIES_BY_ID)


def test_compute_stats_bounds_and_affinity():
    base = compute_stats(level=5, resolved=4, overdue=0, in_progress=1)
    boosted = compute_stats(
        level=5, resolved=4, overdue=0, in_progress=1, affinity={"velocity": 1.4}
    )
    assert all(5 <= v <= 100 for v in base.values())
    assert boosted["velocity"] >= base["velocity"]


def test_snapshot_from_xp_uses_explicit_xp():
    snap = snapshot_from_xp(
        xp=5000, resolved=3, overdue=0, in_progress=1, species_id="unikornik"
    )
    assert snap["xp"] == 5000
    assert snap["level"] == level_for_xp(5000)["level"]
    assert snap["species"]["id"] == "unikornik"
    assert snap["species"]["rarity"] == "legendary"


def test_species_info_falls_back():
    assert species_info(None)["id"] in SPECIES_BY_ID
    assert species_info("nope")["id"] in SPECIES_BY_ID


def test_cosmetics_catalog_valid():
    assert len(COSMETICS) >= 8
    slots = {"head", "eyes", "neck", "hand", "aura", "background"}
    for c in COSMETICS:
        assert c["slot"] in slots
        assert c["price"] > 0
        assert COSMETICS_BY_ID[c["id"]] is c


def test_coins_earned_from_tasks_and_levels():
    # default: 20 coins per closed task + 50 per level above 1
    assert coins_earned(lifetime_resolved=0, level=1) == 0
    assert coins_earned(lifetime_resolved=10, level=1) == 200
    assert coins_earned(lifetime_resolved=10, level=3) == 200 + 100


def test_fast_mode_speeds_up_leveling(monkeypatch):
    monkeypatch.setenv("PET_FAST_MODE", "true")
    assert xp_per_resolved() == 100
    # base 10, exp 1.2 → reaching level 2 costs only 10 XP
    assert xp_for_level(2) == 10
    fast = compute_pet(resolved=2, overdue=0, in_progress=1)
    assert fast["level"] >= 5  # 2 closes → 200 XP → several levels in fast mode
