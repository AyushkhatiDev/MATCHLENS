"""Synthetic player generation.

Generates fictional footballers with position-appropriate attributes.
All randomness flows through the provided ``random.Random`` instance so
that generation is fully deterministic given a seed.
"""

from __future__ import annotations

import random as _random_mod
from typing import Sequence

from simulator.models.player import (
    Player,
    PlayerAttributes,
    Position,
    PreferredFoot,
)

# ---------------------------------------------------------------------------
# Fictional name pools
# ---------------------------------------------------------------------------

FIRST_NAMES: list[str] = [
    "Aiden", "Bruno", "Carlos", "Diego", "Emil", "Fabio", "Gael",
    "Hugo", "Ivan", "Javier", "Kai", "Leon", "Marco", "Nico",
    "Oscar", "Paulo", "Rafael", "Sami", "Tomas", "Victor",
    "William", "Xavi", "Youssef", "Zain", "Andre", "Bastian",
    "Cristian", "Daan", "Edson", "Felix", "Giovanni", "Henrik",
    "Iker", "Johan", "Kylian", "Lucas", "Mateo", "Neylan",
    "Ollie", "Pietro", "Quinn", "Rui", "Stefan", "Theo",
]

LAST_NAMES: list[str] = [
    "Almeida", "Berg", "Costa", "Diaz", "Evans", "Ferreira",
    "Garcia", "Hansen", "Ibanez", "Jensen", "Kim", "Larsson",
    "Moreno", "Nilsen", "Oliveira", "Petrov", "Quinn", "Reyes",
    "Santos", "Torres", "Uribe", "Vidal", "Walker", "Xhaka",
    "Yamamoto", "Zimmerman", "Andersen", "Bakker", "Campos",
    "Dupont", "Eriksen", "Fischer", "Gonzales", "Hernandez",
    "Ivanov", "Johansson", "Klein", "Lopez", "Martinez",
    "Nakamura", "Osei", "Patel", "Rossi", "Silva",
]

# ---------------------------------------------------------------------------
# Attribute generation profiles per position group
# ---------------------------------------------------------------------------

# (mean, spread) — the generator picks mean ± spread/2 then clamps 30–95.
_ATTR_PROFILES: dict[str, dict[str, tuple[int, int]]] = {
    "GK": {
        "passing": (50, 20), "finishing": (20, 10), "tackling": (30, 15),
        "pace": (45, 15), "dribbling": (30, 15), "vision": (50, 15),
        "positioning": (75, 15), "stamina": (55, 15), "pressing": (25, 10),
        "ball_control": (40, 15),
    },
    "DEF": {
        "passing": (60, 20), "finishing": (30, 15), "tackling": (75, 15),
        "pace": (60, 20), "dribbling": (45, 20), "vision": (55, 15),
        "positioning": (72, 15), "stamina": (70, 15), "pressing": (65, 15),
        "ball_control": (55, 15),
    },
    "MID": {
        "passing": (72, 18), "finishing": (50, 20), "tackling": (55, 20),
        "pace": (62, 18), "dribbling": (65, 18), "vision": (70, 15),
        "positioning": (65, 15), "stamina": (72, 15), "pressing": (65, 15),
        "ball_control": (70, 15),
    },
    "ATT": {
        "passing": (60, 18), "finishing": (76, 15), "tackling": (30, 15),
        "pace": (75, 18), "dribbling": (75, 15), "vision": (62, 15),
        "positioning": (72, 15), "stamina": (65, 15), "pressing": (55, 15),
        "ball_control": (72, 15),
    },
}

_POS_GROUP: dict[Position, str] = {
    Position.GK: "GK",
    Position.CB: "DEF", Position.LB: "DEF", Position.RB: "DEF",
    Position.CDM: "MID", Position.CM: "MID", Position.CAM: "MID",
    Position.LM: "MID", Position.RM: "MID",
    Position.LW: "ATT", Position.RW: "ATT", Position.ST: "ATT",
}

_ROLE_MAP: dict[Position, str] = {
    Position.GK: "Goalkeeper",
    Position.CB: "Centre-Back",
    Position.LB: "Left-Back",
    Position.RB: "Right-Back",
    Position.CDM: "Defensive Midfielder",
    Position.CM: "Central Midfielder",
    Position.CAM: "Attacking Midfielder",
    Position.LM: "Left Midfielder",
    Position.RM: "Right Midfielder",
    Position.LW: "Left Winger",
    Position.RW: "Right Winger",
    Position.ST: "Striker",
}

# ---------------------------------------------------------------------------
# Formation → position lists
# ---------------------------------------------------------------------------

FORMATION_POSITIONS: dict[str, list[Position]] = {
    "4-3-3": [
        Position.GK,
        Position.RB, Position.CB, Position.CB, Position.LB,
        Position.CM, Position.CM, Position.CM,
        Position.RW, Position.ST, Position.LW,
    ],
    "4-4-2": [
        Position.GK,
        Position.RB, Position.CB, Position.CB, Position.LB,
        Position.RM, Position.CM, Position.CM, Position.LM,
        Position.ST, Position.ST,
    ],
    "3-5-2": [
        Position.GK,
        Position.CB, Position.CB, Position.CB,
        Position.RM, Position.CDM, Position.CM, Position.CAM, Position.LM,
        Position.ST, Position.ST,
    ],
    "4-2-3-1": [
        Position.GK,
        Position.RB, Position.CB, Position.CB, Position.LB,
        Position.CDM, Position.CDM,
        Position.RW, Position.CAM, Position.LW,
        Position.ST,
    ],
    "3-4-3": [
        Position.GK,
        Position.CB, Position.CB, Position.CB,
        Position.RM, Position.CM, Position.CM, Position.LM,
        Position.RW, Position.ST, Position.LW,
    ],
    "5-3-2": [
        Position.GK,
        Position.RB, Position.CB, Position.CB, Position.CB, Position.LB,
        Position.CM, Position.CM, Position.CM,
        Position.ST, Position.ST,
    ],
    "4-1-4-1": [
        Position.GK,
        Position.RB, Position.CB, Position.CB, Position.LB,
        Position.CDM,
        Position.RM, Position.CM, Position.CM, Position.LM,
        Position.ST,
    ],
}


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def _clamp(val: int, lo: int = 30, hi: int = 95) -> int:
    return max(lo, min(hi, val))


def _gen_attr(rng: _random_mod.Random, mean: int, spread: int) -> int:
    return _clamp(rng.randint(mean - spread // 2, mean + spread // 2))


def generate_attributes(position: Position, rng: _random_mod.Random) -> PlayerAttributes:
    """Generate realistic attributes for a position."""
    group = _POS_GROUP[position]
    profile = _ATTR_PROFILES[group]
    return PlayerAttributes(
        **{attr: _gen_attr(rng, m, s) for attr, (m, s) in profile.items()}
    )


def generate_players(
    team_id: str,
    formation: str,
    rng: _random_mod.Random,
    *,
    first_names: Sequence[str] = FIRST_NAMES,
    last_names: Sequence[str] = LAST_NAMES,
) -> list[Player]:
    """Generate 11 players for *team_id* matching *formation*.

    Each invocation draws from the RNG so results are deterministic.
    """
    positions = FORMATION_POSITIONS.get(formation)
    if positions is None:
        raise ValueError(f"Unknown formation: {formation}")

    used_numbers: set[int] = set()
    players: list[Player] = []

    for idx, pos in enumerate(positions):
        first = rng.choice(list(first_names))
        last = rng.choice(list(last_names))
        name = f"{first} {last}"

        # Goalkeepers traditionally get 1; outfield 2-99.
        if pos == Position.GK:
            number = 1
        else:
            number = rng.randint(2, 40)
            while number in used_numbers:
                number = rng.randint(2, 40)
        used_numbers.add(number)

        foot_roll = rng.random()
        if foot_roll < 0.65:
            foot = PreferredFoot.RIGHT
        elif foot_roll < 0.90:
            foot = PreferredFoot.LEFT
        else:
            foot = PreferredFoot.BOTH

        players.append(
            Player(
                player_id=f"{team_id}_p{idx:02d}",
                name=name,
                shirt_number=number,
                position=pos,
                team_id=team_id,
                preferred_foot=foot,
                role=_ROLE_MAP[pos],
                attributes=generate_attributes(pos, rng),
            )
        )

    return players
