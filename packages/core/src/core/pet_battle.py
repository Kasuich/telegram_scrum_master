"""«Битва скрамиков» — a deterministic, seedable magic-battle engine.

Two modes:

- :func:`run_royale` — a whole-team tournament that ranks everyone by battle power
  (level + stats + accessory bonuses) nudged by a bounded random roll, and emits
  flavorful "magical" status frames for the chat animation.
- :func:`run_duel` — a 1-on-1, round-by-round spell fight with a battle log.

Design notes:

- **Level and accessories give a real edge.** Power is a documented function of level,
  the four pet stats, and per-cosmetic combat bonuses (:data:`ACCESSORY_BONUSES`). The
  random component is bounded (±15% in royale) so stronger scrumiks usually win, but
  upsets happen.
- **Pure + seedable.** All randomness flows through an injected ``random.Random`` so
  the engine is reproducible in tests. No DB, no I/O here.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from core import pet as pet_lib

# Combat attribute bonuses per equipped cosmetic. Multipliers are applied to the
# matching base attribute; ``crit`` adds flat crit chance, ``crit_dmg`` adds to the
# crit multiplier. Documented so the advantage of each item is explicit.
ACCESSORY_BONUSES: dict[str, dict[str, float]] = {
    "crown": {"atk_mult": 1.20, "all_mult": 1.05},  # legendary — raw power + prestige
    "tophat": {"def_mult": 1.25},  # rare — a wall of class
    "cap_red": {"hp_mult": 1.15},  # common — extra toughness
    "shades": {"crit": 0.15},  # uncommon — deal-with-it precision
    "chain_gold": {"atk_mult": 1.15},  # epic — heavy hitter
    "tie": {"all_mult": 1.05},  # common — modest all-round buff
    "coffee": {"spd_mult": 1.20},  # common — caffeinated initiative
    "aura_spark": {"crit_dmg": 0.50},  # epic — devastating crits
}

# Pools for the magical status frames (the "как при написании запросов" beats) and the
# duel round log. ``{name}`` / ``{n}`` are filled in by the engine.
_CAST_FLAVORS = [
    "🔮 {name} плетёт заклинание «Деплой-в-пятницу»…",
    "✨ {name} вызывает дух прошлого спринта…",
    "⚡ {name} контрит «Хотфиксом на проде»…",
    "🌀 {name} уводит таску в &laquo;In Progress&raquo; и пропадает…",
    "📜 {name} зачитывает древний тикет из бэклога…",
    "🧪 {name} варит зелье из стори-поинтов…",
    "🛡️ {name} ставит блок «Жду ревью»…",
    "🔥 {name} поджигает burndown-чарт…",
]
_HIT_FLAVORS = [
    "💥 критический рефактор! −{n}",
    "🗡️ {name} рубит легаси на −{n}",
    "⚔️ прилетает −{n} в незакрытый баг",
    "🌟 магия чистого кода: −{n}",
    "💢 −{n}, прод пошатнулся",
]
_FINALE_FLAVORS = [
    "🏆 Арбитр считает финальный рейтинг…",
    "📊 Сводим velocity к единому знаменателю…",
    "🎲 Кости спринта брошены…",
]


@dataclass(slots=True)
class Combatant:
    """A single scrumik on the arena."""

    name: str
    user_id: str | None
    level: int
    species_id: str | None
    stats: dict[str, int]
    equipped: dict[str, str] = field(default_factory=dict)

    @property
    def species_name(self) -> str:
        return pet_lib.species_info(self.species_id)["name"]


# ---------------------------------------------------------------------------
# Power / attributes
# ---------------------------------------------------------------------------


def combat_attributes(c: Combatant) -> dict[str, float]:
    """Derive HP/ATK/DEF/SPD/crit from level, stats and equipped accessories.

    Stat → attribute mapping: stamina→HP, focus→ATK, reliability→DEF, velocity→SPD.
    Level lifts every attribute (a flat baseline that always rewards progress).
    """
    s = c.stats or {}
    level = max(1, int(c.level or 1))
    hp = 60.0 + float(s.get("stamina", 30)) * 1.4 + level * 8
    atk = 12.0 + float(s.get("focus", 30)) * 0.55 + level * 1.5
    dfn = 8.0 + float(s.get("reliability", 30)) * 0.45 + level * 1.0
    spd = 10.0 + float(s.get("velocity", 30)) * 0.55 + level * 0.8
    crit = 0.05
    crit_dmg = 1.5

    # Accessory bonuses.
    all_mult = 1.0
    for slot_item in (c.equipped or {}).values():
        bonus = ACCESSORY_BONUSES.get(slot_item)
        if not bonus:
            continue
        hp *= bonus.get("hp_mult", 1.0)
        atk *= bonus.get("atk_mult", 1.0)
        dfn *= bonus.get("def_mult", 1.0)
        spd *= bonus.get("spd_mult", 1.0)
        crit += bonus.get("crit", 0.0)
        crit_dmg += bonus.get("crit_dmg", 0.0)
        all_mult *= bonus.get("all_mult", 1.0)

    hp *= all_mult
    atk *= all_mult
    dfn *= all_mult
    spd *= all_mult
    return {
        "hp": hp,
        "atk": atk,
        "def": dfn,
        "spd": spd,
        "crit": min(0.6, crit),
        "crit_dmg": crit_dmg,
    }


def combatant_power(c: Combatant) -> int:
    """A single headline number (⚡) summarizing battle strength."""
    a = combat_attributes(c)
    expected = a["atk"] * (1 + a["crit"] * (a["crit_dmg"] - 1))
    power = expected * 1.7 + a["def"] * 1.2 + a["hp"] * 0.45 + a["spd"] * 1.0
    return int(round(power))


# ---------------------------------------------------------------------------
# Royale (team tournament → ranking)
# ---------------------------------------------------------------------------


def run_royale(
    combatants: list[Combatant],
    *,
    rng: random.Random | None = None,
    jitter: float = 0.15,
) -> dict[str, Any]:
    """Rank all combatants. Score = power × (1 ± up-to-``jitter``)."""
    rng = rng or random.Random()
    rows: list[dict[str, Any]] = []
    for c in combatants:
        power = combatant_power(c)
        roll = 1.0 + rng.uniform(-jitter, jitter)
        rows.append(
            {
                "name": c.name,
                "user_id": c.user_id,
                "species_id": c.species_id,
                "species_name": c.species_name,
                "level": c.level,
                "equipped": dict(c.equipped or {}),
                "power": power,
                "score": int(round(power * roll)),
            }
        )
    rows.sort(key=lambda r: r["score"], reverse=True)
    for i, row in enumerate(rows):
        row["rank"] = i + 1
    return {
        "ranked": rows,
        "status_frames": _royale_status_frames(rows, rng),
        "winner": rows[0] if rows else None,
    }


def _sample_upto(items: list[str], n: int, rng: random.Random) -> list[str]:
    """Up to ``n`` distinct items in random order (handles lists shorter than n)."""
    return rng.sample(items, min(n, len(items)))


def _royale_status_frames(rows: list[dict[str, Any]], rng: random.Random) -> list[str]:
    """A short sequence of magical beats referencing real participants."""
    if not rows:
        return ["🌀 Арена пуста — некому сражаться…"]
    names = [r["name"] for r in rows]
    frames: list[str] = ["⚔️ Скрамики выходят на магическую арену…"]
    for name in _sample_upto(names, 4, rng):
        frames.append(rng.choice(_CAST_FLAVORS).format(name=name))
        frames.append(rng.choice(_HIT_FLAVORS).format(name=name, n=rng.randint(40, 220)))
    frames.append(rng.choice(_FINALE_FLAVORS))
    return frames


# ---------------------------------------------------------------------------
# Duel (1-on-1 → battle log)
# ---------------------------------------------------------------------------


def run_duel(
    a: Combatant,
    b: Combatant,
    *,
    rng: random.Random | None = None,
    max_rounds: int = 8,
) -> dict[str, Any]:
    """Round-by-round spell duel. Returns winner + a flavorful log."""
    rng = rng or random.Random()
    aa, ba = combat_attributes(a), combat_attributes(b)
    hp = {a.name: aa["hp"], b.name: ba["hp"]}
    attrs = {a.name: aa, b.name: ba}
    fighters = {a.name: a, b.name: b}
    log: list[str] = [f"⚔️ {a.name} ({a.species_name}) ⚡ против {b.name} ({b.species_name})!"]

    # Initiative: faster scrumik strikes first each round (with a small random tilt).
    order = (a.name, b.name) if aa["spd"] + rng.uniform(-5, 5) >= ba["spd"] else (b.name, a.name)

    rounds: list[dict[str, Any]] = []
    for rnd in range(1, max_rounds + 1):
        for atk_name in order:
            def_name = b.name if atk_name == a.name else a.name
            dmg, is_crit = _attack(attrs[atk_name], attrs[def_name], rng)
            hp[def_name] = max(0.0, hp[def_name] - dmg)
            cast = rng.choice(_CAST_FLAVORS).format(name=atk_name)
            hit = rng.choice(_HIT_FLAVORS).format(name=atk_name, n=int(dmg))
            crit_mark = " ✨КРИТ✨" if is_crit else ""
            log.append(f"Раунд {rnd}: {cast} {hit}{crit_mark} → {def_name}: {int(hp[def_name])} HP")
            rounds.append(
                {
                    "round": rnd,
                    "attacker": atk_name,
                    "defender": def_name,
                    "damage": int(dmg),
                    "crit": is_crit,
                    "defender_hp": int(hp[def_name]),
                }
            )
            if hp[def_name] <= 0:
                break
        if min(hp.values()) <= 0:
            break

    winner_name = max(hp, key=lambda n: hp[n])
    loser_name = a.name if winner_name == b.name else b.name
    log.append(f"🏆 Победил(а) {winner_name}! ({fighters[winner_name].species_name})")
    return {
        "winner": _fighter_brief(fighters[winner_name], int(hp[winner_name])),
        "loser": _fighter_brief(fighters[loser_name], int(hp[loser_name])),
        "rounds": rounds,
        "log": log,
        "hp": {name: int(value) for name, value in hp.items()},
        "status_frames": _duel_status_frames(a, b, rng),
    }


def _attack(atk: dict[str, float], dfn: dict[str, float], rng: random.Random) -> tuple[float, bool]:
    base = atk["atk"] * rng.uniform(0.85, 1.15)
    is_crit = rng.random() < atk["crit"]
    if is_crit:
        base *= atk["crit_dmg"]
    mitigated = base - dfn["def"] * 0.5
    return max(1.0, mitigated), is_crit


def _fighter_brief(c: Combatant, hp_left: int) -> dict[str, Any]:
    return {
        "name": c.name,
        "user_id": c.user_id,
        "species_id": c.species_id,
        "species_name": c.species_name,
        "level": c.level,
        "equipped": dict(c.equipped or {}),
        "power": combatant_power(c),
        "hp_left": hp_left,
    }


def _duel_status_frames(a: Combatant, b: Combatant, rng: random.Random) -> list[str]:
    frames = [f"⚔️ {a.name} и {b.name} скрещивают заклинания…"]
    frames.append(rng.choice(_CAST_FLAVORS).format(name=a.name))
    frames.append(rng.choice(_CAST_FLAVORS).format(name=b.name))
    frames.append(rng.choice(_FINALE_FLAVORS))
    return frames


# ---------------------------------------------------------------------------
# Building combatants from persisted pet state
# ---------------------------------------------------------------------------


def combatant_from_state(
    *,
    name: str,
    user_id: str | None,
    state_json: dict[str, Any] | None,
    level: int | None = None,
    species_id: str | None = None,
) -> Combatant:
    """Build a :class:`Combatant` from a ``PetState.state_json`` snapshot.

    Falls back to a level-1 baseline (rolled species, computed stats) when the snapshot
    is missing or partial — so a teammate who never opened their pet still fights.
    """
    sj = state_json or {}
    species = (
        species_id
        or sj.get("species", {}).get("id")
        or (pet_lib.roll_species(str(user_id)) if user_id else "standupik")
    )
    lvl = int(level or sj.get("level") or 1)
    stats = sj.get("stats")
    if not isinstance(stats, dict) or not stats:
        affinity = pet_lib.SPECIES_BY_ID.get(species, {}).get("affinity", {})
        stats = pet_lib.compute_stats(
            level=lvl, resolved=0, overdue=0, in_progress=0, affinity=affinity
        )
    equipped = dict((sj.get("_cosmetics", {}) or {}).get("equipped", {}) or {})
    return Combatant(
        name=name,
        user_id=str(user_id) if user_id is not None else None,
        level=lvl,
        species_id=species,
        stats={k: int(v) for k, v in stats.items()},
        equipped=equipped,
    )
