"""Tests for the «Битва скрамиков» engine + image render."""

from __future__ import annotations

import random

import pytest
from core import battle_image, pet_battle
from core.pet_battle import Combatant


def _c(
    name: str, *, level: int, species: str = "standupik", equipped=None, stats=None
) -> Combatant:
    if stats is None:
        stats = {"velocity": 50, "focus": 50, "reliability": 50, "stamina": 50}
    return Combatant(
        name=name,
        user_id=name,
        level=level,
        species_id=species,
        stats=stats,
        equipped=equipped or {},
    )


def test_power_grows_with_level():
    low = pet_battle.combatant_power(_c("a", level=1))
    high = pet_battle.combatant_power(_c("a", level=10))
    assert high > low


def test_accessories_add_power():
    plain = pet_battle.combatant_power(_c("a", level=5))
    crowned = pet_battle.combatant_power(_c("a", level=5, equipped={"head": "crown"}))
    assert crowned > plain


def test_royale_is_deterministic_under_fixed_seed():
    fighters = [_c(f"f{i}", level=i + 1) for i in range(5)]
    r1 = pet_battle.run_royale(fighters, rng=random.Random(42))
    r2 = pet_battle.run_royale(fighters, rng=random.Random(42))
    assert [r["name"] for r in r1["ranked"]] == [r["name"] for r in r2["ranked"]]
    assert r1["ranked"][0]["rank"] == 1
    assert r1["status_frames"]  # magical beats present


def test_royale_favours_stronger_on_average():
    """A high-level, crowned scrumik should win most of many randomized royales."""
    strong = _c("strong", level=12, equipped={"head": "crown", "neck": "chain_gold"})
    weak = _c("weak", level=1)
    wins = 0
    trials = 60
    for seed in range(trials):
        res = pet_battle.run_royale([strong, weak], rng=random.Random(seed))
        if res["ranked"][0]["name"] == "strong":
            wins += 1
    assert wins >= int(trials * 0.85)


def test_duel_returns_winner_and_log():
    a = _c("Ани", level=8, species="unikornik", equipped={"head": "crown"})
    b = _c("Маша", level=3, species="bagik")
    duel = pet_battle.run_duel(a, b, rng=random.Random(7))
    assert duel["winner"]["name"] in {"Ани", "Маша"}
    assert duel["winner"]["name"] != duel["loser"]["name"]
    assert len(duel["log"]) >= 2
    assert set(duel["hp"]) == {"Ани", "Маша"}


def test_combatant_from_state_falls_back():
    c = pet_battle.combatant_from_state(name="x", user_id="u1", state_json=None)
    assert c.level == 1
    assert c.stats and all(v > 0 for v in c.stats.values())


@pytest.mark.parametrize("species", ["standupik", "unikornik", "deployk"])
def test_leaderboard_png_is_nonempty(species):
    fighters = [_c(f"f{i}", level=i + 1, species=species) for i in range(4)]
    res = pet_battle.run_royale(fighters, rng=random.Random(1))
    png = battle_image.render_leaderboard_png("DARKHORSE", res["ranked"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic
    assert len(png) > 1000


def test_duel_png_is_nonempty():
    a = _c("Ани", level=8, species="unikornik", equipped={"head": "crown", "aura": "aura_spark"})
    b = _c("Маша", level=6, species="deployk", equipped={"eyes": "shades"})
    duel = pet_battle.run_duel(a, b, rng=random.Random(3))
    png = battle_image.render_duel_png(duel)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 1000
