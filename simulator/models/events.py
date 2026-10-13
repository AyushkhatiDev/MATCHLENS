"""Event data models for MatchLens simulator.

All match events are Pydantic-validated and carry full provenance
(match_id, team_id, player_id, coordinates, etc.).

Coordinate system
-----------------
x : 0–105 m   (0 = home goal-line, 105 = away goal-line)
y : 0–68 m    (0 = left touchline when facing away goal, 68 = right touchline)
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from simulator.models.match import PITCH_LENGTH_M, PITCH_WIDTH_M


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    """Catalogue of recognised event types."""

    MATCH_START = "MATCH_START"
    HALF_TIME = "HALF_TIME"
    SECOND_HALF_START = "SECOND_HALF_START"
    MATCH_END = "MATCH_END"

    PASS = "PASS"
    SHOT = "SHOT"
    TACKLE = "TACKLE"
    POSSESSION_CHANGE = "POSSESSION_CHANGE"
    PRESSURE = "PRESSURE"
    CARRY = "CARRY"
    INTERCEPTION = "INTERCEPTION"
    RECOVERY = "RECOVERY"
    CROSS = "CROSS"
    FOUL = "FOUL"
    CORNER = "CORNER"
    FREE_KICK = "FREE_KICK"
    GOAL_KICK = "GOAL_KICK"
    GOAL = "GOAL"


# ---------------------------------------------------------------------------
# Shot outcomes / body parts
# ---------------------------------------------------------------------------

class ShotOutcome(str, Enum):
    GOAL = "goal"
    SAVED = "saved"
    BLOCKED = "blocked"
    OFF_TARGET = "off_target"


class BodyPart(str, Enum):
    RIGHT_FOOT = "right_foot"
    LEFT_FOOT = "left_foot"
    HEAD = "head"


# ---------------------------------------------------------------------------
# Helper: Euclidean distance
# ---------------------------------------------------------------------------

def _distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


# ---------------------------------------------------------------------------
# Base event
# ---------------------------------------------------------------------------

class MatchEvent(BaseModel):
    """Base event — every event in the match stream inherits these fields."""

    event_id: str
    match_id: str
    timestamp: float = Field(ge=0.0, description="Simulation time in seconds from kick-off.")
    minute: int = Field(ge=0, description="Match minute (0-indexed display minute).")
    event_type: EventType
    team_id: Optional[str] = None
    player_id: Optional[str] = None
    start_x: Optional[float] = Field(default=None, ge=0.0, le=PITCH_LENGTH_M)
    start_y: Optional[float] = Field(default=None, ge=0.0, le=PITCH_WIDTH_M)
    possession_sequence_id: Optional[str] = None
    synthetic: bool = Field(default=True)


# ---------------------------------------------------------------------------
# Pass event
# ---------------------------------------------------------------------------

class PassEvent(MatchEvent):
    """A pass between two players."""

    event_type: EventType = EventType.PASS
    receiver_player_id: Optional[str] = None
    end_x: float = Field(ge=0.0, le=PITCH_LENGTH_M)
    end_y: float = Field(ge=0.0, le=PITCH_WIDTH_M)
    distance_meters: float = Field(ge=0.0)
    successful: bool = True
    pressure_level: float = Field(ge=0.0, le=1.0, default=0.0)
    progressive: bool = False
    difficulty: float = Field(ge=0.0, le=1.0, default=0.5)

    @model_validator(mode="after")
    def _validate_pass(self) -> "PassEvent":
        # Distance must match coordinates (within 0.5 m tolerance).
        if self.start_x is not None and self.start_y is not None:
            expected = _distance(self.start_x, self.start_y, self.end_x, self.end_y)
            if abs(expected - self.distance_meters) > 0.5:
                raise ValueError(
                    f"distance_meters ({self.distance_meters:.2f}) does not match "
                    f"coordinate distance ({expected:.2f})"
                )
        # Receiver must differ from passer.
        if self.receiver_player_id is not None and self.receiver_player_id == self.player_id:
            raise ValueError("receiver_player_id must differ from player_id")
        return self


# ---------------------------------------------------------------------------
# Shot event
# ---------------------------------------------------------------------------

class ShotEvent(MatchEvent):
    """A shot on goal."""

    event_type: EventType = EventType.SHOT
    end_x: float = Field(ge=0.0, le=PITCH_LENGTH_M)
    end_y: float = Field(ge=0.0, le=PITCH_WIDTH_M)
    shot_distance_meters: float = Field(ge=0.0)
    shot_speed_kph: Optional[float] = Field(default=None, ge=0.0, le=200.0)
    body_part: BodyPart = BodyPart.RIGHT_FOOT
    under_pressure: bool = False
    outcome: ShotOutcome = ShotOutcome.OFF_TARGET
    shot_quality: float = Field(ge=0.0, le=1.0, default=0.5)


# ---------------------------------------------------------------------------
# Tackle event
# ---------------------------------------------------------------------------

class TackleEvent(MatchEvent):
    """A tackle attempt."""

    event_type: EventType = EventType.TACKLE
    target_player_id: Optional[str] = None
    successful: bool = True


# ---------------------------------------------------------------------------
# Pressure event
# ---------------------------------------------------------------------------

class PressureEvent(MatchEvent):
    """Pressure applied to the ball-carrier."""

    event_type: EventType = EventType.PRESSURE
    pressing_team_id: str = ""
    target_team_id: str = ""
    target_player_id: Optional[str] = None
    intensity: float = Field(ge=0.0, le=1.0, default=0.5)
    zone: str = "middle_central"
    duration_seconds: float = Field(ge=0.0, default=2.0)
    successful: bool = False


# ---------------------------------------------------------------------------
# Carry event
# ---------------------------------------------------------------------------

class CarryEvent(MatchEvent):
    """A player carrying (dribbling) the ball."""

    event_type: EventType = EventType.CARRY
    end_x: float = Field(ge=0.0, le=PITCH_LENGTH_M)
    end_y: float = Field(ge=0.0, le=PITCH_WIDTH_M)
    distance_meters: float = Field(ge=0.0)
    successful: bool = True


# ---------------------------------------------------------------------------
# Interception event
# ---------------------------------------------------------------------------

class InterceptionEvent(MatchEvent):
    """An interception by the defending team."""

    event_type: EventType = EventType.INTERCEPTION
    intercepting_player_id: Optional[str] = None
    successful: bool = True


# ---------------------------------------------------------------------------
# Recovery event
# ---------------------------------------------------------------------------

class RecoveryEvent(MatchEvent):
    """A ball recovery."""

    event_type: EventType = EventType.RECOVERY


# ---------------------------------------------------------------------------
# Cross event
# ---------------------------------------------------------------------------

class CrossEvent(MatchEvent):
    """A cross into the box."""

    event_type: EventType = EventType.CROSS
    end_x: float = Field(ge=0.0, le=PITCH_LENGTH_M)
    end_y: float = Field(ge=0.0, le=PITCH_WIDTH_M)
    successful: bool = True


# ---------------------------------------------------------------------------
# Foul event
# ---------------------------------------------------------------------------

class FoulEvent(MatchEvent):
    """A foul committed."""

    event_type: EventType = EventType.FOUL
    fouled_player_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Set-piece events
# ---------------------------------------------------------------------------

class CornerEvent(MatchEvent):
    """A corner kick."""

    event_type: EventType = EventType.CORNER


class FreeKickEvent(MatchEvent):
    """A free kick."""

    event_type: EventType = EventType.FREE_KICK


class GoalKickEvent(MatchEvent):
    """A goal kick."""

    event_type: EventType = EventType.GOAL_KICK


# ---------------------------------------------------------------------------
# Possession change (meta-event)
# ---------------------------------------------------------------------------

class PossessionChangeEvent(MatchEvent):
    """Signals a change of possession from one team to the other."""

    event_type: EventType = EventType.POSSESSION_CHANGE
    from_team_id: Optional[str] = None
    to_team_id: Optional[str] = None
    cause: str = "turnover"


# ---------------------------------------------------------------------------
# Goal event (convenience event emitted alongside the SHOT with outcome=goal)
# ---------------------------------------------------------------------------

class GoalEvent(MatchEvent):
    """Explicit goal event for easy downstream filtering."""

    event_type: EventType = EventType.GOAL
    scorer_player_id: Optional[str] = None
    assist_player_id: Optional[str] = None
    home_score: int = Field(ge=0)
    away_score: int = Field(ge=0)
