"""Player data models for MatchLens simulator.

Defines the Player, PlayerAttributes, Position, and PreferredFoot types.
Attributes are normalised 0–100 integers that influence event generation.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Position(str, Enum):
    """Standard football positions."""

    GK = "GK"
    CB = "CB"
    LB = "LB"
    RB = "RB"
    CDM = "CDM"
    CM = "CM"
    CAM = "CAM"
    LM = "LM"
    RM = "RM"
    LW = "LW"
    RW = "RW"
    ST = "ST"


class PreferredFoot(str, Enum):
    """Preferred foot of a player."""

    LEFT = "left"
    RIGHT = "right"
    BOTH = "both"


class PlayerAttributes(BaseModel):
    """Normalised 0–100 player attributes that influence simulation probabilities."""

    passing: int = Field(ge=0, le=100)
    finishing: int = Field(ge=0, le=100)
    tackling: int = Field(ge=0, le=100)
    pace: int = Field(ge=0, le=100)
    dribbling: int = Field(ge=0, le=100)
    vision: int = Field(ge=0, le=100)
    positioning: int = Field(ge=0, le=100)
    stamina: int = Field(ge=0, le=100)
    pressing: int = Field(ge=0, le=100)
    ball_control: int = Field(ge=0, le=100)


class Player(BaseModel):
    """A synthetic football player."""

    player_id: str
    name: str
    shirt_number: int = Field(ge=1, le=99)
    position: Position
    team_id: str
    preferred_foot: PreferredFoot
    role: str
    attributes: PlayerAttributes

    @property
    def is_goalkeeper(self) -> bool:
        return self.position == Position.GK

    @property
    def is_defender(self) -> bool:
        return self.position in {Position.CB, Position.LB, Position.RB}

    @property
    def is_midfielder(self) -> bool:
        return self.position in {
            Position.CDM, Position.CM, Position.CAM,
            Position.LM, Position.RM,
        }

    @property
    def is_attacker(self) -> bool:
        return self.position in {Position.LW, Position.RW, Position.ST}
