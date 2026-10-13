"""Tactical scenario modifiers.

A scenario influences the *weights* used during event generation without
overriding the engine.  Scenarios can be explicitly set or can emerge
from match state (e.g. a trailing team late in the game triggers
``DESPERATE_CHASE``).
"""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple


class Scenario(str, Enum):
    """Match scenario labels."""

    NORMAL = "NORMAL"
    HIGH_PRESSURE = "HIGH_PRESSURE"
    COUNTER_ATTACK = "COUNTER_ATTACK"
    DOMINANT_POSSESSION = "DOMINANT_POSSESSION"
    DESPERATE_CHASE = "DESPERATE_CHASE"
    LATE_GAME_CHAOS = "LATE_GAME_CHAOS"


class ScenarioModifiers(NamedTuple):
    """Multiplicative modifiers applied to base event weights.

    A value of 1.0 means no change.  >1 increases probability, <1 decreases.
    """

    pass_weight: float = 1.0
    shot_weight: float = 1.0
    pressure_weight: float = 1.0
    carry_weight: float = 1.0
    cross_weight: float = 1.0
    tackle_weight: float = 1.0
    interception_weight: float = 1.0
    foul_weight: float = 1.0
    turnover_chance: float = 1.0  # multiplier on possession-change probability


SCENARIO_MODS: dict[Scenario, ScenarioModifiers] = {
    Scenario.NORMAL: ScenarioModifiers(),
    Scenario.HIGH_PRESSURE: ScenarioModifiers(
        pressure_weight=1.8,
        tackle_weight=1.4,
        interception_weight=1.3,
        turnover_chance=1.3,
        foul_weight=1.2,
    ),
    Scenario.COUNTER_ATTACK: ScenarioModifiers(
        carry_weight=1.5,
        pass_weight=0.8,
        shot_weight=1.3,
        pressure_weight=0.7,
    ),
    Scenario.DOMINANT_POSSESSION: ScenarioModifiers(
        pass_weight=1.4,
        carry_weight=1.2,
        pressure_weight=0.7,
        turnover_chance=0.6,
    ),
    Scenario.DESPERATE_CHASE: ScenarioModifiers(
        shot_weight=1.5,
        cross_weight=1.5,
        pressure_weight=1.3,
        turnover_chance=1.4,
        foul_weight=1.3,
    ),
    Scenario.LATE_GAME_CHAOS: ScenarioModifiers(
        shot_weight=1.3,
        pressure_weight=1.2,
        turnover_chance=1.3,
        foul_weight=1.4,
    ),
}


def detect_scenario(
    *,
    match_minute: int,
    duration_minutes: int,
    home_score: int,
    away_score: int,
    possession_team_is_home: bool,
    possession_pct_home: float,
) -> Scenario:
    """Infer a scenario from the current match state.

    This allows scenarios to *emerge* rather than being hard-coded.
    """
    remaining = duration_minutes - match_minute

    # Late-game: trailing team is in possession
    if remaining <= 10:
        if home_score != away_score:
            trailing_is_home = home_score < away_score
            if trailing_is_home == possession_team_is_home:
                return Scenario.DESPERATE_CHASE
        return Scenario.LATE_GAME_CHAOS

    # Dominant possession
    if possession_pct_home > 0.65 and possession_team_is_home:
        return Scenario.DOMINANT_POSSESSION
    if possession_pct_home < 0.35 and not possession_team_is_home:
        return Scenario.DOMINANT_POSSESSION

    return Scenario.NORMAL
