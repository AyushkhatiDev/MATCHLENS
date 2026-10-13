"""Phase 2 — Intelligence Engine tests.

Covers:
  - MatchIntelligence generation
  - Possession percentages
  - Team pass accuracy
  - Player statistics
  - Progressive pass detection
  - Pressure Index bounds
  - Field Tilt bounds
  - Rolling windows
  - State classification determinism
  - State transition determinism
  - Significance score bounds
  - Evidence references
  - ExplanationContext validity
  - Determinism (same input → same output)
  - Different input → different output
  - Timeline chronological order
  - Goal events reflected
  - No impossible references
"""

from __future__ import annotations

import json
import pytest

from simulator.generation.generator import generate_match
from simulator.engine.match_engine import MatchEngine
from simulator.intelligence.engine import IntelligenceEngine
from simulator.intelligence.models import (
    MatchIntelligence,
    SignificanceTier,
    MatchStateName,
    PossessionOutcome,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

def _build_intel(seed: int = 42) -> tuple[MatchIntelligence, list]:
    match = generate_match(seed=seed, duration_minutes=90)
    engine = MatchEngine(match)
    events = engine.simulate()
    intel_engine = IntelligenceEngine(match, events)
    intel = intel_engine.analyze()
    return intel, events


@pytest.fixture(scope="module")
def intel42():
    intel, _ = _build_intel(42)
    return intel


@pytest.fixture(scope="module")
def intel99():
    intel, _ = _build_intel(99)
    return intel


# ── 1. Basic generation ──────────────────────────────────────────────────

class TestBasicGeneration:
    def test_intelligence_is_generated(self, intel42):
        assert intel42 is not None
        assert isinstance(intel42, MatchIntelligence)

    def test_has_match_id(self, intel42):
        assert intel42.match_id

    def test_has_summary(self, intel42):
        assert intel42.summary.total_events > 0

    def test_has_teams(self, intel42):
        assert intel42.home_team.team_id
        assert intel42.away_team.team_id
        assert intel42.home_team.team_id != intel42.away_team.team_id

    def test_has_players(self, intel42):
        assert len(intel42.players) == 22  # 11 per team

    def test_has_sequences(self, intel42):
        assert len(intel42.possession_sequences) > 0


# ── 2. Possession percentages ────────────────────────────────────────────

class TestPossession:
    def test_possession_sums_to_approximately_100(self, intel42):
        h = intel42.home_team.possession.possession_pct
        a = intel42.away_team.possession.possession_pct
        assert 98.0 <= h + a <= 102.0, f"Possession sum = {h + a}"

    def test_possession_within_bounds(self, intel42):
        for t in (intel42.home_team, intel42.away_team):
            assert 0 <= t.possession.possession_pct <= 100

    def test_possession_sequences_count(self, intel42):
        h_seq = intel42.home_team.possession.sequences
        a_seq = intel42.away_team.possession.sequences
        assert h_seq + a_seq == intel42.possession_intelligence.total_sequences


# ── 3. Team pass accuracy ────────────────────────────────────────────────

class TestTeamPassing:
    def test_pass_accuracy_calculated(self, intel42):
        for t in (intel42.home_team, intel42.away_team):
            assert 0 <= t.passing.accuracy <= 100
            if t.passing.attempts > 0:
                expected = round(t.passing.successful / t.passing.attempts * 100, 1)
                assert abs(t.passing.accuracy - expected) < 0.2

    def test_passes_sum_correctly(self, intel42):
        for t in (intel42.home_team, intel42.away_team):
            assert t.passing.successful + t.passing.unsuccessful == t.passing.attempts

    def test_total_passes_match_summary(self, intel42):
        h = intel42.home_team.passing.attempts
        a = intel42.away_team.passing.attempts
        assert h + a == intel42.summary.total_passes

    def test_pass_accuracy_realistic(self, intel42):
        """After tuning, pass accuracy should be in a realistic range."""
        for t in (intel42.home_team, intel42.away_team):
            assert 60 <= t.passing.accuracy <= 95, f"Pass accuracy = {t.passing.accuracy}%"


# ── 4. Player statistics ─────────────────────────────────────────────────

class TestPlayerStats:
    def test_all_players_present(self, intel42):
        assert len(intel42.players) == 22

    def test_player_passes_sum_to_team(self, intel42):
        for team in (intel42.home_team, intel42.away_team):
            team_players = [p for p in intel42.players if p.team_id == team.team_id]
            total_passes = sum(p.passes for p in team_players)
            assert total_passes == team.passing.attempts

    def test_player_pass_accuracy_bounded(self, intel42):
        for p in intel42.players:
            assert 0 <= p.pass_accuracy <= 100

    def test_player_belongs_to_valid_team(self, intel42):
        valid_teams = {intel42.home_team.team_id, intel42.away_team.team_id}
        for p in intel42.players:
            assert p.team_id in valid_teams


# ── 5. Progressive passes ────────────────────────────────────────────────

class TestProgressivePasses:
    def test_progressive_detected(self, intel42):
        total = intel42.home_team.passing.progressive + intel42.away_team.passing.progressive
        assert total > 0, "No progressive passes detected"

    def test_progressive_less_than_total(self, intel42):
        for t in (intel42.home_team, intel42.away_team):
            assert t.passing.progressive <= t.passing.attempts


# ── 6. Pressure Index ────────────────────────────────────────────────────

class TestPressureIndex:
    def test_pressure_index_bounded(self, intel42):
        for t in (intel42.home_team, intel42.away_team):
            assert 0 <= t.pressure_index <= 100

    def test_pressure_not_zero(self, intel42):
        # At least one team should have a non-trivial index
        total = intel42.home_team.pressure_index + intel42.away_team.pressure_index
        assert total > 0


# ── 7. Field Tilt ────────────────────────────────────────────────────────

class TestFieldTilt:
    def test_field_tilt_bounded(self, intel42):
        for t in (intel42.home_team, intel42.away_team):
            assert 0 <= t.field_tilt_pct <= 100

    def test_field_tilt_sums_to_100(self, intel42):
        total = intel42.home_team.field_tilt_pct + intel42.away_team.field_tilt_pct
        assert 98.0 <= total <= 102.0


# ── 8. Rolling windows ──────────────────────────────────────────────────

class TestRollingWindows:
    def test_windows_exist(self, intel42):
        assert len(intel42.rolling_windows) > 0

    def test_window_timestamps_valid(self, intel42):
        for w in intel42.rolling_windows:
            assert w.timestamp > 0
            assert w.window_seconds in (60, 180, 300, 600)

    def test_window_possession_bounded(self, intel42):
        for w in intel42.rolling_windows:
            assert 0 <= w.home_possession_pct <= 100
            assert 0 <= w.away_possession_pct <= 100

    def test_window_pressure_bounded(self, intel42):
        for w in intel42.rolling_windows:
            assert 0 <= w.home_pressure_index <= 100
            assert 0 <= w.away_pressure_index <= 100


# ── 9. State classification determinism ──────────────────────────────────

class TestStateClassification:
    def test_states_exist(self, intel42):
        assert len(intel42.tactical_states) > 0

    def test_states_are_valid(self, intel42):
        valid = {s.value for s in MatchStateName}
        for s in intel42.tactical_states:
            assert s.state.value in valid

    def test_confidence_bounded(self, intel42):
        for s in intel42.tactical_states:
            assert 0 <= s.confidence <= 1.0

    def test_deterministic(self):
        """Same seed → same states."""
        i1, _ = _build_intel(42)
        i2, _ = _build_intel(42)
        assert len(i1.tactical_states) == len(i2.tactical_states)
        for a, b in zip(i1.tactical_states, i2.tactical_states):
            assert a.state == b.state
            assert a.confidence == b.confidence
            assert a.timestamp == b.timestamp


# ── 10. State transitions ────────────────────────────────────────────────

class TestStateTransitions:
    def test_transitions_have_evidence(self, intel42):
        for t in intel42.state_transitions:
            assert len(t.evidence_ids) > 0

    def test_transitions_magnitude_bounded(self, intel42):
        for t in intel42.state_transitions:
            assert 0 <= t.magnitude <= 1.0

    def test_transitions_deterministic(self):
        i1, _ = _build_intel(42)
        i2, _ = _build_intel(42)
        assert len(i1.state_transitions) == len(i2.state_transitions)
        for a, b in zip(i1.state_transitions, i2.state_transitions):
            assert a.previous_state == b.previous_state
            assert a.new_state == b.new_state


# ── 11. Significance scores ──────────────────────────────────────────────

class TestSignificance:
    def test_scores_bounded(self, intel42):
        for m in intel42.detected_moments:
            assert 0 <= m.significance_score <= 100

    def test_tiers_valid(self, intel42):
        valid = {t.value for t in SignificanceTier}
        for m in intel42.detected_moments:
            assert m.tier.value in valid


# ── 12. Evidence IDs ─────────────────────────────────────────────────────

class TestEvidence:
    def test_evidence_exists(self, intel42):
        assert len(intel42.evidence) > 0

    def test_moments_have_evidence(self, intel42):
        for m in intel42.detected_moments:
            assert len(m.evidence_ids) > 0

    def test_evidence_ids_resolve(self, intel42):
        evi_map = {e.evidence_id: e for e in intel42.evidence}
        for m in intel42.detected_moments:
            for eid in m.evidence_ids:
                assert eid in evi_map, f"Evidence {eid} not found"


# ── 13. ExplanationContext ───────────────────────────────────────────────

class TestExplanationContext:
    def test_contexts_exist(self, intel42):
        assert len(intel42.explanation_contexts) > 0

    def test_context_references_valid_evidence(self, intel42):
        evi_ids = {e.evidence_id for e in intel42.evidence}
        for ctx in intel42.explanation_contexts:
            for eid in ctx.evidence_ids:
                assert eid in evi_ids, f"ExplanationContext references missing evidence {eid}"

    def test_context_has_moment_id(self, intel42):
        moment_ids = {m.moment_id for m in intel42.detected_moments}
        for ctx in intel42.explanation_contexts:
            assert ctx.moment_id in moment_ids


# ── 14/15. Determinism ──────────────────────────────────────────────────

class TestDeterminism:
    def test_same_input_same_output(self):
        i1, _ = _build_intel(42)
        i2, _ = _build_intel(42)
        j1 = i1.model_dump(mode="json", exclude={"generated_at"})
        j2 = i2.model_dump(mode="json", exclude={"generated_at"})
        assert j1 == j2

    def test_different_input_different_output(self):
        i1, _ = _build_intel(42)
        i2, _ = _build_intel(99)
        j1 = i1.model_dump(mode="json", exclude={"generated_at"})
        j2 = i2.model_dump(mode="json", exclude={"generated_at"})
        assert j1 != j2


# ── 16. Timeline ─────────────────────────────────────────────────────────

class TestTimeline:
    def test_timeline_exists(self, intel42):
        assert len(intel42.timeline) > 0

    def test_timeline_chronological(self, intel42):
        timestamps = [e.timestamp for e in intel42.timeline]
        assert timestamps == sorted(timestamps)

    def test_timeline_starts_with_match_start(self, intel42):
        assert intel42.timeline[0].label == "MATCH_START"


# ── 17. Goals reflected ──────────────────────────────────────────────────

class TestGoals:
    def test_goals_create_moments(self):
        """Use seed 99 which produces goals."""
        intel, _ = _build_intel(99)
        if intel.summary.goals > 0:
            goal_moments = [m for m in intel.detected_moments if m.moment_type.value == "GOAL"]
            assert len(goal_moments) == intel.summary.goals

    def test_goal_moments_are_critical(self):
        intel, _ = _build_intel(99)
        for m in intel.detected_moments:
            if m.moment_type.value == "GOAL":
                assert m.tier == SignificanceTier.CRITICAL
                assert m.significance_score == 100


# ── 18. No impossible references ─────────────────────────────────────────

class TestReferenceIntegrity:
    def test_player_team_references(self, intel42):
        valid_teams = {intel42.home_team.team_id, intel42.away_team.team_id}
        for p in intel42.players:
            assert p.team_id in valid_teams

    def test_evidence_team_references(self, intel42):
        valid_teams = {intel42.home_team.team_id, intel42.away_team.team_id, None}
        for e in intel42.evidence:
            assert e.team_id in valid_teams

    def test_sequence_teams_valid(self, intel42):
        valid_teams = {intel42.home_team.team_id, intel42.away_team.team_id}
        for seq in intel42.possession_sequences:
            assert seq.team_id in valid_teams


# ── 19. Possession sequences ────────────────────────────────────────────

class TestPossessionSequences:
    def test_sequences_have_positive_duration(self, intel42):
        for s in intel42.possession_sequences:
            assert s.duration_s >= 0

    def test_outcomes_valid(self, intel42):
        valid = {o.value for o in PossessionOutcome}
        for s in intel42.possession_sequences:
            assert s.outcome.value in valid

    def test_sequences_chronological(self, intel42):
        times = [s.start_time for s in intel42.possession_sequences]
        assert times == sorted(times)


# ── 20. Team comparison ─────────────────────────────────────────────────

class TestTeamComparison:
    def test_comparison_exists(self, intel42):
        assert intel42.team_comparison is not None

    def test_comparison_teams_correct(self, intel42):
        assert intel42.team_comparison.home_team_id == intel42.home_team.team_id
        assert intel42.team_comparison.away_team_id == intel42.away_team.team_id


# ── 21. JSON serialization ──────────────────────────────────────────────

class TestSerialization:
    def test_serializable_to_json(self, intel42):
        data = intel42.model_dump(mode="json")
        json_str = json.dumps(data)
        assert len(json_str) > 100

    def test_round_trip(self, intel42):
        data = intel42.model_dump(mode="json")
        restored = MatchIntelligence(**data)
        assert restored.match_id == intel42.match_id
        assert restored.summary.total_events == intel42.summary.total_events
