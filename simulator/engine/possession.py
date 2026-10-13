"""Possession state tracking.

Maintains which team / player has the ball, current sequence IDs,
durations, and field zone.
"""

from __future__ import annotations

import random as _random_mod
import uuid
from dataclasses import dataclass, field

from simulator.models.match import PitchThird, PitchLane, classify_third, classify_lane


@dataclass
class PossessionState:
    """Mutable possession tracker used by the match engine."""

    team_id: str = ""
    player_id: str = ""
    sequence_id: str = ""
    sequence_start_ts: float = 0.0
    ball_x: float = 52.5
    ball_y: float = 34.0

    # Cumulative seconds each team has been in possession.
    team_durations: dict[str, float] = field(default_factory=dict)

    def start_new_sequence(self, team_id: str, player_id: str, ts: float, rng: _random_mod.Random) -> str:
        """Begin a new possession sequence and return its ID."""
        # Close out old duration.
        self._accrue_duration(ts)

        self.team_id = team_id
        self.player_id = player_id
        self.sequence_id = str(uuid.UUID(int=rng.getrandbits(128), version=4))
        self.sequence_start_ts = ts
        return self.sequence_id

    def update_player(self, player_id: str) -> None:
        self.player_id = player_id

    def update_ball(self, x: float, y: float) -> None:
        self.ball_x = x
        self.ball_y = y

    def current_third(self, *, attacking_right: bool) -> PitchThird:
        return classify_third(self.ball_x, attacking_right=attacking_right)

    def current_lane(self) -> PitchLane:
        return classify_lane(self.ball_y)

    def sequence_duration(self, ts: float) -> float:
        return ts - self.sequence_start_ts

    def possession_pct(self, team_id: str) -> float:
        """Return 0–1 possession share for *team_id*."""
        total = sum(self.team_durations.values())
        if total <= 0:
            return 0.5
        return self.team_durations.get(team_id, 0.0) / total

    # internal helpers
    def _accrue_duration(self, ts: float) -> None:
        if self.team_id:
            elapsed = ts - self.sequence_start_ts
            if elapsed > 0:
                self.team_durations[self.team_id] = (
                    self.team_durations.get(self.team_id, 0.0) + elapsed
                )
