"""Tests for data models (Player, Team, Match, Events)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from simulator.models.player import (
    Player,
    PlayerAttributes,
    Position,
    PreferredFoot,
)
from simulator.models.match import (
    Match,
    Team,
    Formation,
    PlayingStyle,
    PITCH_LENGTH_M,
    PITCH_WIDTH_M,
    classify_third,
    classify_lane,
    PitchThird,
    PitchLane,
)
from simulator.models.events import (
    PassEvent,
    ShotEvent,
    EventType,
    ShotOutcome,
    BodyPart,
    MatchEvent,
    _distance,
)


# ---------------------------------------------------------------------------
# PlayerAttributes validation
# ---------------------------------------------------------------------------

class TestPlayerAttributes:
    def test_valid_attributes(self) -> None:
        attrs = PlayerAttributes(
            passing=70, finishing=60, tackling=55, pace=80,
            dribbling=65, vision=72, positioning=68, stamina=75,
            pressing=62, ball_control=70,
        )
        assert attrs.passing == 70

    def test_attribute_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            PlayerAttributes(
                passing=110, finishing=60, tackling=55, pace=80,
                dribbling=65, vision=72, positioning=68, stamina=75,
                pressing=62, ball_control=70,
            )

    def test_negative_attribute(self) -> None:
        with pytest.raises(ValidationError):
            PlayerAttributes(
                passing=-5, finishing=60, tackling=55, pace=80,
                dribbling=65, vision=72, positioning=68, stamina=75,
                pressing=62, ball_control=70,
            )


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------

class TestPlayer:
    @pytest.fixture()
    def player(self) -> Player:
        return Player(
            player_id="p01",
            name="Test Player",
            shirt_number=7,
            position=Position.ST,
            team_id="t01",
            preferred_foot=PreferredFoot.RIGHT,
            role="Striker",
            attributes=PlayerAttributes(
                passing=60, finishing=80, tackling=30, pace=85,
                dribbling=78, vision=60, positioning=75, stamina=70,
                pressing=55, ball_control=75,
            ),
        )

    def test_is_attacker(self, player: Player) -> None:
        assert player.is_attacker is True

    def test_is_not_goalkeeper(self, player: Player) -> None:
        assert player.is_goalkeeper is False


# ---------------------------------------------------------------------------
# Zone classification
# ---------------------------------------------------------------------------

class TestZones:
    def test_defensive_third(self) -> None:
        assert classify_third(10.0) == PitchThird.DEFENSIVE

    def test_middle_third(self) -> None:
        assert classify_third(52.0) == PitchThird.MIDDLE

    def test_attacking_third(self) -> None:
        assert classify_third(90.0) == PitchThird.ATTACKING

    def test_reversed_direction(self) -> None:
        assert classify_third(90.0, attacking_right=False) == PitchThird.DEFENSIVE
        assert classify_third(10.0, attacking_right=False) == PitchThird.ATTACKING

    def test_lane_left(self) -> None:
        assert classify_lane(5.0) == PitchLane.LEFT

    def test_lane_central(self) -> None:
        assert classify_lane(34.0) == PitchLane.CENTRAL

    def test_lane_right(self) -> None:
        assert classify_lane(60.0) == PitchLane.RIGHT


# ---------------------------------------------------------------------------
# Pass event validation
# ---------------------------------------------------------------------------

class TestPassEvent:
    def test_distance_matches_coordinates(self) -> None:
        dist = _distance(0, 0, 30, 40)  # 50.0
        pe = PassEvent(
            event_id="e1", match_id="m1", timestamp=10.0, minute=0,
            team_id="t1", player_id="p1", receiver_player_id="p2",
            start_x=0, start_y=0, end_x=30, end_y=40,
            distance_meters=round(dist, 2),
            possession_sequence_id="seq1",
        )
        assert pe.distance_meters == pytest.approx(50.0, abs=0.5)

    def test_distance_mismatch_raises(self) -> None:
        with pytest.raises(ValidationError, match="distance_meters"):
            PassEvent(
                event_id="e1", match_id="m1", timestamp=10.0, minute=0,
                team_id="t1", player_id="p1", receiver_player_id="p2",
                start_x=0, start_y=0, end_x=30, end_y=40,
                distance_meters=5.0,  # wrong
                possession_sequence_id="seq1",
            )

    def test_passer_equals_receiver_raises(self) -> None:
        with pytest.raises(ValidationError, match="receiver_player_id"):
            PassEvent(
                event_id="e1", match_id="m1", timestamp=10.0, minute=0,
                team_id="t1", player_id="p1", receiver_player_id="p1",
                start_x=0, start_y=0, end_x=10, end_y=0,
                distance_meters=10.0,
                possession_sequence_id="seq1",
            )

    def test_coordinates_out_of_pitch(self) -> None:
        with pytest.raises(ValidationError):
            PassEvent(
                event_id="e1", match_id="m1", timestamp=10.0, minute=0,
                team_id="t1", player_id="p1",
                start_x=0, start_y=0, end_x=200, end_y=0,  # out of pitch
                distance_meters=200.0,
                possession_sequence_id="seq1",
            )


# ---------------------------------------------------------------------------
# Shot event
# ---------------------------------------------------------------------------

class TestShotEvent:
    def test_valid_shot(self) -> None:
        se = ShotEvent(
            event_id="e1", match_id="m1", timestamp=10.0, minute=0,
            team_id="t1", player_id="p1",
            start_x=90, start_y=34, end_x=105, end_y=34,
            shot_distance_meters=15.0, shot_speed_kph=100.0,
            body_part=BodyPart.RIGHT_FOOT,
            outcome=ShotOutcome.SAVED,
            shot_quality=0.6,
        )
        assert se.outcome == ShotOutcome.SAVED

    def test_speed_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            ShotEvent(
                event_id="e1", match_id="m1", timestamp=10.0, minute=0,
                team_id="t1", player_id="p1",
                start_x=90, start_y=34, end_x=105, end_y=34,
                shot_distance_meters=15.0, shot_speed_kph=300.0,  # too fast
                body_part=BodyPart.RIGHT_FOOT,
                outcome=ShotOutcome.SAVED,
            )
