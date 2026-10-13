"""Synthetic team generation.

Creates two teams with squad, formation, and playing style — all
deterministic given the RNG.
"""

from __future__ import annotations

import random as _random_mod

from simulator.models.match import (
    Formation,
    PlayingStyle,
    Team,
)
from simulator.generation.players import generate_players

# ---------------------------------------------------------------------------
# Fictional club data
# ---------------------------------------------------------------------------

_CLUB_POOL: list[dict[str, str]] = [
    {"name": "Northbridge City FC", "short": "NBC"},
    {"name": "Ironhaven United", "short": "IHU"},
    {"name": "Coastal Rangers", "short": "CRG"},
    {"name": "Stormgate Athletic", "short": "SGA"},
    {"name": "Heathrow Wanderers", "short": "HWD"},
    {"name": "Ashfield Town", "short": "AFT"},
    {"name": "Crestmont Rovers", "short": "CMR"},
    {"name": "Silverdale Borough", "short": "SDB"},
    {"name": "Thornwick Albion", "short": "TWA"},
    {"name": "Falconbridge City", "short": "FBC"},
]


def generate_team(
    rng: _random_mod.Random,
    *,
    team_index: int = 0,
    club_pool: list[dict[str, str]] | None = None,
) -> Team:
    """Create a single synthetic team.

    Parameters
    ----------
    rng : random.Random
        Seeded RNG for determinism.
    team_index : int
        Index used to pick the club from the pool (0 for home, 1 for away).
    club_pool : list, optional
        Override the default club pool.
    """
    pool = club_pool or _CLUB_POOL
    club = pool[team_index % len(pool)]

    formation = rng.choice(list(Formation))
    style = rng.choice(list(PlayingStyle))
    team_id = f"team_{team_index:02d}"

    players = generate_players(
        team_id=team_id,
        formation=formation.value,
        rng=rng,
    )

    return Team(
        team_id=team_id,
        name=club["name"],
        short_name=club["short"],
        formation=formation,
        playing_style=style,
        players=players,
    )


def generate_two_teams(
    rng: _random_mod.Random,
) -> tuple[Team, Team]:
    """Generate a home and away team."""
    home = generate_team(rng, team_index=0)
    away = generate_team(rng, team_index=1)
    return home, away
