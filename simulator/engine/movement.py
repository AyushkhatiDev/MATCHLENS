"""Ball / player movement helpers.

Provides realistic coordinate generation for events like passes,
carries, and shots.  All functions accept a seeded ``random.Random``.
"""

from __future__ import annotations

import math
import random as _random_mod

from simulator.models.match import PITCH_LENGTH_M, PITCH_WIDTH_M


def clamp_x(x: float) -> float:
    return max(0.0, min(PITCH_LENGTH_M, round(x, 2)))


def clamp_y(y: float) -> float:
    return max(0.0, min(PITCH_WIDTH_M, round(y, 2)))


def random_position(rng: _random_mod.Random) -> tuple[float, float]:
    """Uniformly random pitch position."""
    return clamp_x(rng.uniform(0, PITCH_LENGTH_M)), clamp_y(rng.uniform(0, PITCH_WIDTH_M))


def jitter(
    rng: _random_mod.Random,
    x: float,
    y: float,
    max_dx: float = 15.0,
    max_dy: float = 10.0,
) -> tuple[float, float]:
    """Add bounded random jitter to a coordinate."""
    return (
        clamp_x(x + rng.uniform(-max_dx, max_dx)),
        clamp_y(y + rng.uniform(-max_dy, max_dy)),
    )


def pass_end_position(
    rng: _random_mod.Random,
    start_x: float,
    start_y: float,
    *,
    progressive: bool = False,
    attacking_right: bool = True,
) -> tuple[float, float]:
    """Generate a plausible pass destination.

    For progressive passes, the end-x is biased towards the attacking goal.
    """
    if progressive:
        advance = rng.uniform(8.0, 30.0) * (1 if attacking_right else -1)
    else:
        advance = rng.uniform(-5.0, 15.0) * (1 if attacking_right else -1)

    end_x = clamp_x(start_x + advance)
    end_y = clamp_y(start_y + rng.uniform(-15.0, 15.0))
    return end_x, end_y


def carry_end_position(
    rng: _random_mod.Random,
    start_x: float,
    start_y: float,
    *,
    attacking_right: bool = True,
) -> tuple[float, float]:
    """Carry destination — shorter than a pass, generally forward."""
    advance = rng.uniform(2.0, 12.0) * (1 if attacking_right else -1)
    return (
        clamp_x(start_x + advance),
        clamp_y(start_y + rng.uniform(-6.0, 6.0)),
    )


def shot_position(
    rng: _random_mod.Random,
    *,
    attacking_right: bool = True,
) -> tuple[float, float]:
    """Position from which a shot is taken — biased towards the box."""
    if attacking_right:
        x = rng.uniform(PITCH_LENGTH_M - 30, PITCH_LENGTH_M - 2)
    else:
        x = rng.uniform(2, 30)
    y = rng.uniform(10, PITCH_WIDTH_M - 10)
    return clamp_x(x), clamp_y(y)


def cross_end_position(
    rng: _random_mod.Random,
    *,
    attacking_right: bool = True,
) -> tuple[float, float]:
    """Cross destination — into the box area."""
    if attacking_right:
        end_x = rng.uniform(PITCH_LENGTH_M - 18, PITCH_LENGTH_M - 5)
    else:
        end_x = rng.uniform(5, 18)
    end_y = rng.uniform(15, PITCH_WIDTH_M - 15)
    return clamp_x(end_x), clamp_y(end_y)


def distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return round(math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2), 2)


def goal_position(*, attacking_right: bool = True) -> tuple[float, float]:
    """Centre of the goal being attacked."""
    if attacking_right:
        return PITCH_LENGTH_M, PITCH_WIDTH_M / 2
    return 0.0, PITCH_WIDTH_M / 2
