"""Event factory functions.

Each function creates a fully-validated Pydantic event object.  All
coordinate calculations happen here so the engine can stay high-level.
"""

from __future__ import annotations

import math
import random as _random_mod
import uuid
from typing import Optional

from simulator.models.events import (
    BodyPart,
    CarryEvent,
    CornerEvent,
    CrossEvent,
    FoulEvent,
    FreeKickEvent,
    GoalEvent,
    GoalKickEvent,
    InterceptionEvent,
    MatchEvent,
    EventType,
    PassEvent,
    PossessionChangeEvent,
    PressureEvent,
    RecoveryEvent,
    ShotEvent,
    ShotOutcome,
    TackleEvent,
)
from simulator.models.match import (
    PITCH_LENGTH_M,
    PITCH_WIDTH_M,
    classify_third,
    classify_lane,
)
from simulator.models.player import Player
from simulator.engine.movement import (
    clamp_x,
    clamp_y,
    distance,
    pass_end_position,
    carry_end_position,
    shot_position,
    cross_end_position,
    goal_position,
)


def _uid(rng: _random_mod.Random) -> str:
    """Generate a deterministic UUID from the seeded RNG."""
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


# ---------------------------------------------------------------------------
# Lifecycle events
# ---------------------------------------------------------------------------

def make_match_start(rng: _random_mod.Random, match_id: str, ts: float = 0.0) -> MatchEvent:
    return MatchEvent(
        event_id=_uid(rng), match_id=match_id, timestamp=ts, minute=0,
        event_type=EventType.MATCH_START,
    )


def make_half_time(rng: _random_mod.Random, match_id: str, ts: float) -> MatchEvent:
    return MatchEvent(
        event_id=_uid(rng), match_id=match_id, timestamp=ts, minute=45,
        event_type=EventType.HALF_TIME,
    )


def make_second_half_start(rng: _random_mod.Random, match_id: str, ts: float) -> MatchEvent:
    return MatchEvent(
        event_id=_uid(rng), match_id=match_id, timestamp=ts, minute=45,
        event_type=EventType.SECOND_HALF_START,
    )


def make_match_end(rng: _random_mod.Random, match_id: str, ts: float, minute: int) -> MatchEvent:
    return MatchEvent(
        event_id=_uid(rng), match_id=match_id, timestamp=ts, minute=minute,
        event_type=EventType.MATCH_END,
    )


# ---------------------------------------------------------------------------
# Pass
# ---------------------------------------------------------------------------

def make_pass(
    rng: _random_mod.Random,
    *,
    match_id: str,
    ts: float,
    minute: int,
    passer: Player,
    receiver: Player,
    start_x: float,
    start_y: float,
    attacking_right: bool,
    pressure_level: float,
    possession_seq_id: str,
) -> PassEvent:
    progressive = rng.random() < 0.30
    end_x, end_y = pass_end_position(
        rng, start_x, start_y,
        progressive=progressive,
        attacking_right=attacking_right,
    )
    dist = distance(start_x, start_y, end_x, end_y)

    # difficulty: f(distance, pressure, zone)
    dist_factor = min(dist / 60.0, 1.0)
    difficulty = round(0.3 * dist_factor + 0.4 * pressure_level + 0.3 * rng.uniform(0, 0.5), 4)
    difficulty = min(1.0, difficulty)

    # success probability — tuned for ~80% match-level accuracy
    base = passer.attributes.passing / 100.0
    success_prob = 0.45 + 0.50 * base * (1.0 - 0.2 * difficulty) * (1.0 - 0.15 * pressure_level)
    successful = rng.random() < max(0.25, min(0.98, success_prob))

    return PassEvent(
        event_id=_uid(rng),
        match_id=match_id,
        timestamp=round(ts, 2),
        minute=minute,
        team_id=passer.team_id,
        player_id=passer.player_id,
        receiver_player_id=receiver.player_id if successful else None,
        start_x=round(start_x, 2),
        start_y=round(start_y, 2),
        end_x=round(end_x, 2),
        end_y=round(end_y, 2),
        distance_meters=round(dist, 2),
        successful=successful,
        pressure_level=round(pressure_level, 4),
        progressive=progressive,
        difficulty=round(difficulty, 4),
        possession_sequence_id=possession_seq_id,
    )


# ---------------------------------------------------------------------------
# Shot
# ---------------------------------------------------------------------------

def make_shot(
    rng: _random_mod.Random,
    *,
    match_id: str,
    ts: float,
    minute: int,
    shooter: Player,
    start_x: float,
    start_y: float,
    attacking_right: bool,
    under_pressure: bool,
    possession_seq_id: str,
) -> ShotEvent:
    goal_x, goal_y = goal_position(attacking_right=attacking_right)
    dist = distance(start_x, start_y, goal_x, goal_y)

    # Shot quality: f(distance, finishing, pressure)
    dist_factor = max(0, 1.0 - dist / 40.0)
    finish_factor = shooter.attributes.finishing / 100.0
    pressure_penalty = 0.15 if under_pressure else 0.0
    quality = round(0.4 * dist_factor + 0.45 * finish_factor - pressure_penalty + rng.uniform(-0.05, 0.1), 4)
    quality = max(0.02, min(1.0, quality))

    r = rng.random()
    if r < quality * 0.35:
        outcome = ShotOutcome.GOAL
    elif r < quality * 0.35 + 0.25:
        outcome = ShotOutcome.SAVED
    elif r < quality * 0.35 + 0.25 + 0.15:
        outcome = ShotOutcome.BLOCKED
    else:
        outcome = ShotOutcome.OFF_TARGET

    bp_roll = rng.random()
    if bp_roll < 0.55:
        body_part = BodyPart.RIGHT_FOOT
    elif bp_roll < 0.85:
        body_part = BodyPart.LEFT_FOOT
    else:
        body_part = BodyPart.HEAD

    speed = round(rng.uniform(60, 130), 1)

    return ShotEvent(
        event_id=_uid(rng),
        match_id=match_id,
        timestamp=round(ts, 2),
        minute=minute,
        event_type=EventType.SHOT,
        team_id=shooter.team_id,
        player_id=shooter.player_id,
        start_x=round(start_x, 2),
        start_y=round(start_y, 2),
        end_x=round(goal_x, 2),
        end_y=round(goal_y, 2),
        shot_distance_meters=round(dist, 2),
        shot_speed_kph=speed,
        body_part=body_part,
        under_pressure=under_pressure,
        outcome=outcome,
        shot_quality=round(quality, 4),
        possession_sequence_id=possession_seq_id,
    )


# ---------------------------------------------------------------------------
# Pressure
# ---------------------------------------------------------------------------

def make_pressure(
    rng: _random_mod.Random,
    *,
    match_id: str,
    ts: float,
    minute: int,
    pressing_player: Player,
    target_player: Player,
    ball_x: float,
    ball_y: float,
    possession_seq_id: str,
) -> PressureEvent:
    third = classify_third(ball_x, attacking_right=True).value
    lane = classify_lane(ball_y).value
    zone = f"{third}_{lane}"

    intensity = round(rng.uniform(0.3, 1.0), 4)
    dur = round(rng.uniform(1.0, 4.0), 2)
    successful = rng.random() < (intensity * pressing_player.attributes.pressing / 150.0)

    return PressureEvent(
        event_id=_uid(rng),
        match_id=match_id,
        timestamp=round(ts, 2),
        minute=minute,
        team_id=pressing_player.team_id,
        player_id=pressing_player.player_id,
        pressing_team_id=pressing_player.team_id,
        target_team_id=target_player.team_id,
        target_player_id=target_player.player_id,
        intensity=intensity,
        zone=zone,
        duration_seconds=dur,
        successful=successful,
        start_x=round(ball_x, 2),
        start_y=round(ball_y, 2),
        possession_sequence_id=possession_seq_id,
    )


# ---------------------------------------------------------------------------
# Carry
# ---------------------------------------------------------------------------

def make_carry(
    rng: _random_mod.Random,
    *,
    match_id: str,
    ts: float,
    minute: int,
    player: Player,
    start_x: float,
    start_y: float,
    attacking_right: bool,
    possession_seq_id: str,
) -> CarryEvent:
    end_x, end_y = carry_end_position(rng, start_x, start_y, attacking_right=attacking_right)
    dist = distance(start_x, start_y, end_x, end_y)
    successful = rng.random() < (player.attributes.dribbling / 100.0 * 0.85 + 0.10)

    return CarryEvent(
        event_id=_uid(rng),
        match_id=match_id,
        timestamp=round(ts, 2),
        minute=minute,
        team_id=player.team_id,
        player_id=player.player_id,
        start_x=round(start_x, 2),
        start_y=round(start_y, 2),
        end_x=round(end_x, 2),
        end_y=round(end_y, 2),
        distance_meters=round(dist, 2),
        successful=successful,
        possession_sequence_id=possession_seq_id,
    )


# ---------------------------------------------------------------------------
# Tackle
# ---------------------------------------------------------------------------

def make_tackle(
    rng: _random_mod.Random,
    *,
    match_id: str,
    ts: float,
    minute: int,
    tackler: Player,
    target_player: Player,
    ball_x: float,
    ball_y: float,
    possession_seq_id: str,
) -> TackleEvent:
    success_prob = tackler.attributes.tackling / 100.0 * 0.7 + 0.15
    successful = rng.random() < success_prob

    return TackleEvent(
        event_id=_uid(rng),
        match_id=match_id,
        timestamp=round(ts, 2),
        minute=minute,
        team_id=tackler.team_id,
        player_id=tackler.player_id,
        target_player_id=target_player.player_id,
        successful=successful,
        start_x=round(ball_x, 2),
        start_y=round(ball_y, 2),
        possession_sequence_id=possession_seq_id,
    )


# ---------------------------------------------------------------------------
# Interception
# ---------------------------------------------------------------------------

def make_interception(
    rng: _random_mod.Random,
    *,
    match_id: str,
    ts: float,
    minute: int,
    interceptor: Player,
    ball_x: float,
    ball_y: float,
    possession_seq_id: str,
) -> InterceptionEvent:
    return InterceptionEvent(
        event_id=_uid(rng),
        match_id=match_id,
        timestamp=round(ts, 2),
        minute=minute,
        team_id=interceptor.team_id,
        player_id=interceptor.player_id,
        intercepting_player_id=interceptor.player_id,
        start_x=round(ball_x, 2),
        start_y=round(ball_y, 2),
        possession_sequence_id=possession_seq_id,
    )


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

def make_recovery(
    rng: _random_mod.Random,
    *,
    match_id: str,
    ts: float,
    minute: int,
    player: Player,
    ball_x: float,
    ball_y: float,
    possession_seq_id: str,
) -> RecoveryEvent:
    return RecoveryEvent(
        event_id=_uid(rng),
        match_id=match_id,
        timestamp=round(ts, 2),
        minute=minute,
        team_id=player.team_id,
        player_id=player.player_id,
        start_x=round(ball_x, 2),
        start_y=round(ball_y, 2),
        possession_sequence_id=possession_seq_id,
    )


# ---------------------------------------------------------------------------
# Cross
# ---------------------------------------------------------------------------

def make_cross(
    rng: _random_mod.Random,
    *,
    match_id: str,
    ts: float,
    minute: int,
    crosser: Player,
    start_x: float,
    start_y: float,
    attacking_right: bool,
    possession_seq_id: str,
) -> CrossEvent:
    end_x, end_y = cross_end_position(rng, attacking_right=attacking_right)
    successful = rng.random() < (crosser.attributes.passing / 100.0 * 0.5 + 0.2)

    return CrossEvent(
        event_id=_uid(rng),
        match_id=match_id,
        timestamp=round(ts, 2),
        minute=minute,
        team_id=crosser.team_id,
        player_id=crosser.player_id,
        start_x=round(start_x, 2),
        start_y=round(start_y, 2),
        end_x=round(end_x, 2),
        end_y=round(end_y, 2),
        successful=successful,
        possession_sequence_id=possession_seq_id,
    )


# ---------------------------------------------------------------------------
# Foul
# ---------------------------------------------------------------------------

def make_foul(
    rng: _random_mod.Random,
    *,
    match_id: str,
    ts: float,
    minute: int,
    fouler: Player,
    fouled_player: Player,
    ball_x: float,
    ball_y: float,
    possession_seq_id: str,
) -> FoulEvent:
    return FoulEvent(
        event_id=_uid(rng),
        match_id=match_id,
        timestamp=round(ts, 2),
        minute=minute,
        team_id=fouler.team_id,
        player_id=fouler.player_id,
        fouled_player_id=fouled_player.player_id,
        start_x=round(ball_x, 2),
        start_y=round(ball_y, 2),
        possession_sequence_id=possession_seq_id,
    )


# ---------------------------------------------------------------------------
# Possession change (meta-event)
# ---------------------------------------------------------------------------

def make_possession_change(
    rng: _random_mod.Random,
    *,
    match_id: str,
    ts: float,
    minute: int,
    from_team_id: str,
    to_team_id: str,
    cause: str,
    ball_x: float,
    ball_y: float,
    possession_seq_id: str,
) -> PossessionChangeEvent:
    return PossessionChangeEvent(
        event_id=_uid(rng),
        match_id=match_id,
        timestamp=round(ts, 2),
        minute=minute,
        team_id=to_team_id,
        from_team_id=from_team_id,
        to_team_id=to_team_id,
        cause=cause,
        start_x=round(ball_x, 2),
        start_y=round(ball_y, 2),
        possession_sequence_id=possession_seq_id,
    )


# ---------------------------------------------------------------------------
# Goal (emitted alongside a shot with outcome=goal)
# ---------------------------------------------------------------------------

def make_goal(
    rng: _random_mod.Random,
    *,
    match_id: str,
    ts: float,
    minute: int,
    scorer: Player,
    home_score: int,
    away_score: int,
    ball_x: float,
    ball_y: float,
    possession_seq_id: str,
) -> GoalEvent:
    return GoalEvent(
        event_id=_uid(rng),
        match_id=match_id,
        timestamp=round(ts, 2),
        minute=minute,
        team_id=scorer.team_id,
        player_id=scorer.player_id,
        scorer_player_id=scorer.player_id,
        start_x=round(ball_x, 2),
        start_y=round(ball_y, 2),
        home_score=home_score,
        away_score=away_score,
        possession_sequence_id=possession_seq_id,
    )


# ---------------------------------------------------------------------------
# Set pieces
# ---------------------------------------------------------------------------

def make_corner(
    rng: _random_mod.Random,
    *,
    match_id: str,
    ts: float,
    minute: int,
    team_id: str,
    attacking_right: bool,
    possession_seq_id: str,
) -> CornerEvent:
    if attacking_right:
        x, y = PITCH_LENGTH_M, 0.0  # corner flag
    else:
        x, y = 0.0, 0.0
    return CornerEvent(
        event_id=_uid(rng),
        match_id=match_id,
        timestamp=round(ts, 2),
        minute=minute,
        team_id=team_id,
        start_x=x,
        start_y=y,
        possession_sequence_id=possession_seq_id,
    )


def make_goal_kick(
    rng: _random_mod.Random,
    *,
    match_id: str,
    ts: float,
    minute: int,
    team_id: str,
    player_id: str,
    attacking_right: bool,
    possession_seq_id: str,
) -> GoalKickEvent:
    if attacking_right:
        x = 4.0
    else:
        x = PITCH_LENGTH_M - 4.0
    return GoalKickEvent(
        event_id=_uid(rng),
        match_id=match_id,
        timestamp=round(ts, 2),
        minute=minute,
        team_id=team_id,
        player_id=player_id,
        start_x=x,
        start_y=PITCH_WIDTH_M / 2,
        possession_sequence_id=possession_seq_id,
    )
