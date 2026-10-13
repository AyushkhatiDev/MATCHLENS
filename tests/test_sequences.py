"""Tests for event sequence realism and consistency."""

from __future__ import annotations

import pytest

from simulator.generation.generator import generate_match
from simulator.engine.match_engine import MatchEngine
from simulator.models.events import (
    EventType,
    MatchEvent,
    PassEvent,
    ShotEvent,
    ShotOutcome,
    GoalEvent,
    PossessionChangeEvent,
)
from simulator.models.match import PITCH_LENGTH_M, PITCH_WIDTH_M


@pytest.fixture(scope="module")
def simulated_events() -> list[MatchEvent]:
    """Run one simulation to share across the sequence tests."""
    match = generate_match(seed=100)
    engine = MatchEngine(match)
    return engine.simulate()


@pytest.fixture(scope="module")
def engine_state():
    match = generate_match(seed=100)
    engine = MatchEngine(match)
    engine.simulate()
    return engine.state


# ---------------------------------------------------------------------------
# Chronological ordering
# ---------------------------------------------------------------------------

class TestChronology:
    def test_timestamps_non_decreasing(self, simulated_events: list[MatchEvent]) -> None:
        for i in range(1, len(simulated_events)):
            assert simulated_events[i].timestamp >= simulated_events[i - 1].timestamp, (
                f"Event {i} timestamp {simulated_events[i].timestamp} < "
                f"previous {simulated_events[i - 1].timestamp}"
            )

    def test_starts_with_match_start(self, simulated_events: list[MatchEvent]) -> None:
        assert simulated_events[0].event_type == EventType.MATCH_START

    def test_ends_with_match_end(self, simulated_events: list[MatchEvent]) -> None:
        assert simulated_events[-1].event_type == EventType.MATCH_END

    def test_has_half_time(self, simulated_events: list[MatchEvent]) -> None:
        ht = [e for e in simulated_events if e.event_type == EventType.HALF_TIME]
        assert len(ht) == 1

    def test_has_second_half_start(self, simulated_events: list[MatchEvent]) -> None:
        shs = [e for e in simulated_events if e.event_type == EventType.SECOND_HALF_START]
        assert len(shs) == 1


# ---------------------------------------------------------------------------
# Event validity
# ---------------------------------------------------------------------------

class TestEventValidity:
    def test_all_events_belong_to_match(self, simulated_events: list[MatchEvent]) -> None:
        match_ids = {e.match_id for e in simulated_events}
        # All events should reference the same match_id (lifecycle events have match_id too).
        assert len(match_ids) == 1

    def test_coordinates_within_pitch(self, simulated_events: list[MatchEvent]) -> None:
        for ev in simulated_events:
            if ev.start_x is not None:
                assert 0.0 <= ev.start_x <= PITCH_LENGTH_M, f"Bad start_x: {ev.start_x}"
            if ev.start_y is not None:
                assert 0.0 <= ev.start_y <= PITCH_WIDTH_M, f"Bad start_y: {ev.start_y}"

    def test_pass_distances_match_coords(self, simulated_events: list[MatchEvent]) -> None:
        passes = [e for e in simulated_events if isinstance(e, PassEvent)]
        assert len(passes) > 50, "Should have a meaningful number of passes"
        for pe in passes:
            if pe.start_x is not None:
                import math
                expected = math.sqrt(
                    (pe.end_x - pe.start_x) ** 2 + (pe.end_y - pe.start_y) ** 2
                )
                assert abs(expected - pe.distance_meters) < 0.6, (
                    f"Pass distance mismatch: expected {expected:.2f}, got {pe.distance_meters}"
                )


# ---------------------------------------------------------------------------
# Sequence realism
# ---------------------------------------------------------------------------

class TestSequenceRealism:
    def test_reasonable_event_count(self, simulated_events: list[MatchEvent]) -> None:
        """A 90-min match should produce 500–2500 events."""
        count = len(simulated_events)
        assert 500 <= count <= 2500, f"Event count {count} seems unrealistic"

    def test_not_all_same_type(self, simulated_events: list[MatchEvent]) -> None:
        types = {e.event_type for e in simulated_events}
        assert len(types) > 5, "Should have diverse event types"

    def test_passes_outnumber_shots(self, simulated_events: list[MatchEvent]) -> None:
        passes = sum(1 for e in simulated_events if e.event_type == EventType.PASS)
        shots = sum(1 for e in simulated_events if e.event_type == EventType.SHOT)
        assert passes > shots * 5, "Passes should significantly outnumber shots"

    def test_shots_are_infrequent(self, simulated_events: list[MatchEvent]) -> None:
        shots = sum(1 for e in simulated_events if e.event_type == EventType.SHOT)
        assert shots < 40, f"Too many shots ({shots}); unrealistic"

    def test_goals_are_rare(self, simulated_events: list[MatchEvent]) -> None:
        goals = sum(1 for e in simulated_events if e.event_type == EventType.GOAL)
        assert goals < 12, f"Too many goals ({goals}); unrealistic"

    def test_no_impossible_player_references(self, engine_state) -> None:
        """All player_ids in events should belong to one of the two teams."""
        valid_ids = {
            p.player_id
            for p in engine_state.home_team.players + engine_state.away_team.players
        }
        for ev in engine_state.events:
            if ev.player_id is not None:
                assert ev.player_id in valid_ids, f"Unknown player_id: {ev.player_id}"


# ---------------------------------------------------------------------------
# Possession changes
# ---------------------------------------------------------------------------

class TestPossessionChanges:
    def test_possession_changes_exist(self, simulated_events: list[MatchEvent]) -> None:
        pcs = [e for e in simulated_events if e.event_type == EventType.POSSESSION_CHANGE]
        assert len(pcs) > 20, "Should have many possession changes in 90 min"

    def test_possession_change_teams_differ(self, simulated_events: list[MatchEvent]) -> None:
        pcs = [e for e in simulated_events if isinstance(e, PossessionChangeEvent)]
        for pc in pcs:
            if pc.from_team_id and pc.to_team_id:
                assert pc.from_team_id != pc.to_team_id, "Possession change to same team"


# ---------------------------------------------------------------------------
# Goals and scoring
# ---------------------------------------------------------------------------

class TestScoring:
    def test_goal_events_update_score(self, simulated_events: list[MatchEvent]) -> None:
        goals = [e for e in simulated_events if isinstance(e, GoalEvent)]
        if not goals:
            pytest.skip("No goals in this simulation")
        prev_total = 0
        for g in goals:
            total = g.home_score + g.away_score
            assert total > prev_total, "Score should increase after a goal"
            prev_total = total

    def test_score_never_decreases(self, simulated_events: list[MatchEvent]) -> None:
        goals = [e for e in simulated_events if isinstance(e, GoalEvent)]
        for i in range(1, len(goals)):
            assert goals[i].home_score >= goals[i - 1].home_score
            assert goals[i].away_score >= goals[i - 1].away_score
