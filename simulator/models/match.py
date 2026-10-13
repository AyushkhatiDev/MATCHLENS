"""Match and Team data models for MatchLens simulator.

Defines Match, Team, PlayingStyle, Formation, and the standardised
pitch coordinate system (105 × 68 m).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from simulator.models.player import Player


# ---------------------------------------------------------------------------
# Pitch constants — standardised coordinate system
# ---------------------------------------------------------------------------

PITCH_LENGTH_M: float = 105.0
"""Pitch length in metres (x-axis, 0 = home goal-line, 105 = away goal-line)."""

PITCH_WIDTH_M: float = 68.0
"""Pitch width in metres (y-axis, 0 = left touchline, 68 = right touchline)."""


# ---------------------------------------------------------------------------
# Zone helpers
# ---------------------------------------------------------------------------

class PitchThird(str, Enum):
    """Longitudinal pitch thirds (relative to attacking direction)."""

    DEFENSIVE = "defensive"
    MIDDLE = "middle"
    ATTACKING = "attacking"


class PitchLane(str, Enum):
    """Lateral pitch lanes."""

    LEFT = "left"
    CENTRAL = "central"
    RIGHT = "right"


def classify_third(x: float, *, attacking_right: bool = True) -> PitchThird:
    """Return the pitch third for an x-coordinate.

    Parameters
    ----------
    x : float
        x-coordinate on the pitch (0–105).
    attacking_right : bool
        If *True* the team attacks toward x = 105 (default for the home
        team in the first half).  If *False* the team attacks toward x = 0.
    """
    if attacking_right:
        if x < PITCH_LENGTH_M / 3:
            return PitchThird.DEFENSIVE
        elif x < 2 * PITCH_LENGTH_M / 3:
            return PitchThird.MIDDLE
        else:
            return PitchThird.ATTACKING
    else:
        if x > 2 * PITCH_LENGTH_M / 3:
            return PitchThird.DEFENSIVE
        elif x > PITCH_LENGTH_M / 3:
            return PitchThird.MIDDLE
        else:
            return PitchThird.ATTACKING


def classify_lane(y: float) -> PitchLane:
    """Return the pitch lane for a y-coordinate."""
    if y < PITCH_WIDTH_M / 3:
        return PitchLane.LEFT
    elif y < 2 * PITCH_WIDTH_M / 3:
        return PitchLane.CENTRAL
    else:
        return PitchLane.RIGHT


# ---------------------------------------------------------------------------
# Playing style
# ---------------------------------------------------------------------------

class PlayingStyle(str, Enum):
    """Team tactical identity — influences event generation weights."""

    POSSESSION = "possession"
    BALANCED = "balanced"
    HIGH_PRESS = "high_press"
    COUNTER_ATTACK = "counter_attack"
    DEFENSIVE = "defensive"
    DIRECT = "direct"


# ---------------------------------------------------------------------------
# Formation
# ---------------------------------------------------------------------------

class Formation(str, Enum):
    """Common football formations."""

    F_4_3_3 = "4-3-3"
    F_4_4_2 = "4-4-2"
    F_3_5_2 = "3-5-2"
    F_4_2_3_1 = "4-2-3-1"
    F_3_4_3 = "3-4-3"
    F_5_3_2 = "5-3-2"
    F_4_1_4_1 = "4-1-4-1"


# ---------------------------------------------------------------------------
# Team
# ---------------------------------------------------------------------------

class Team(BaseModel):
    """A synthetic football team."""

    team_id: str
    name: str
    short_name: str = Field(max_length=5)
    formation: Formation
    playing_style: PlayingStyle
    players: list[Player] = Field(default_factory=list)

    def outfield_players(self) -> list[Player]:
        """Return all non-goalkeeper players."""
        return [p for p in self.players if not p.is_goalkeeper]

    def goalkeeper(self) -> Optional[Player]:
        """Return the goalkeeper, if any."""
        for p in self.players:
            if p.is_goalkeeper:
                return p
        return None

    def players_by_position(self, *positions: str) -> list[Player]:
        """Return players matching any of the given position values."""
        pos_set = set(positions)
        return [p for p in self.players if p.position.value in pos_set]


# ---------------------------------------------------------------------------
# Match
# ---------------------------------------------------------------------------

class Match(BaseModel):
    """Top-level match container."""

    match_id: str
    competition_name: str = "Synthetic Premier League"
    season: str = "2025-26"
    matchday: int = Field(ge=1)
    date: datetime
    venue: str
    home_team: Team
    away_team: Team
    duration_minutes: int = Field(default=90, ge=1)
    random_seed: int
    synthetic_data: bool = Field(
        default=True,
        description="Flag indicating that this data is synthetically generated.",
    )
