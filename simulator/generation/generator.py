"""High-level match generator.

Assembles teams, metadata, and delegates to the match engine.
"""

from __future__ import annotations

import random as _random_mod
import uuid
from datetime import datetime, timezone

from simulator.models.match import Match
from simulator.generation.teams import generate_two_teams

# ---------------------------------------------------------------------------
# Venue pool
# ---------------------------------------------------------------------------

_VENUES: list[str] = [
    "Northbridge Arena",
    "Ironhaven Stadium",
    "Coastal Park",
    "Stormgate Ground",
    "Heathrow Lane",
    "Ashfield Road",
    "Crestmont Park",
    "Silverdale Stadium",
    "Thornwick Lane",
    "Falconbridge Arena",
]


def generate_match(
    seed: int,
    *,
    duration_minutes: int = 90,
    competition_name: str = "Synthetic Premier League",
    matchday: int = 1,
) -> Match:
    """Create a fully populated :class:`Match` ready for simulation."""
    rng = _random_mod.Random(seed)

    home, away = generate_two_teams(rng)
    venue = rng.choice(_VENUES)
    match_id = str(uuid.UUID(int=rng.getrandbits(128), version=4))

    return Match(
        match_id=match_id,
        competition_name=competition_name,
        matchday=matchday,
        date=datetime(2025, 9, 14, 15, 0, tzinfo=timezone.utc),
        venue=venue,
        home_team=home,
        away_team=away,
        duration_minutes=duration_minutes,
        random_seed=seed,
    )
