"""Pydantic models for the MatchLens Intelligence output.

Every model is JSON-serializable and carries stable IDs for downstream
consumption by AI agents, APIs, or frontends.

All metrics labelled "MatchLens …" are synthetic heuristics defined by
this project — they are NOT official football statistics.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────

class PossessionOutcome(str, Enum):
    SHOT = "SHOT"
    GOAL = "GOAL"
    TURNOVER = "TURNOVER"
    FOUL_WON = "FOUL_WON"
    HALF_TIME = "HALF_TIME"
    MATCH_END = "MATCH_END"


class MatchStateName(str, Enum):
    BALANCED = "BALANCED"
    HOME_CONTROL = "HOME_CONTROL"
    AWAY_CONTROL = "AWAY_CONTROL"
    HIGH_PRESSURE = "HIGH_PRESSURE"
    TRANSITION_PHASE = "TRANSITION_PHASE"
    ATTACKING_PHASE = "ATTACKING_PHASE"
    CHAOTIC_PHASE = "CHAOTIC_PHASE"


class SignificanceTier(str, Enum):
    LOW = "LOW"
    NOTABLE = "NOTABLE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EvidenceType(str, Enum):
    METRIC = "METRIC"
    SHIFT = "SHIFT"
    SEQUENCE = "SEQUENCE"
    STATE_CHANGE = "STATE_CHANGE"
    THRESHOLD = "THRESHOLD"
    PLAYER_SHIFT = "PLAYER_SHIFT"


class MomentType(str, Enum):
    GOAL = "GOAL"
    SHOT_ON_TARGET = "SHOT_ON_TARGET"
    SHOT = "SHOT"
    TACTICAL_SHIFT = "TACTICAL_SHIFT"
    PRESSURE_SHIFT = "PRESSURE_SHIFT"
    FIELD_TILT_SHIFT = "FIELD_TILT_SHIFT"
    STATE_CHANGE = "STATE_CHANGE"
    POSSESSION_SHIFT = "POSSESSION_SHIFT"
    PLAYER_SPIKE = "PLAYER_SPIKE"


# ── Match summary ────────────────────────────────────────────────────────

class MatchSummaryIntel(BaseModel):
    total_events: int = 0
    total_passes: int = 0
    successful_passes: int = 0
    unsuccessful_passes: int = 0
    pass_accuracy: float = 0.0
    total_shots: int = 0
    shots_on_target: int = 0
    goals: int = 0
    tackles: int = 0
    pressure_events: int = 0
    possession_changes: int = 0
    fouls: int = 0
    carries: int = 0
    crosses: int = 0
    interceptions: int = 0
    recoveries: int = 0
    match_duration_s: float = 0.0


# ── Team-level stats ─────────────────────────────────────────────────────

class PossessionStats(BaseModel):
    possession_pct: float = 50.0
    possession_duration_s: float = 0.0
    sequences: int = 0
    avg_sequence_duration_s: float = 0.0
    longest_sequence_duration_s: float = 0.0


class PassingStats(BaseModel):
    attempts: int = 0
    successful: int = 0
    unsuccessful: int = 0
    accuracy: float = 0.0
    avg_distance_m: float = 0.0
    progressive: int = 0
    progressive_pct: float = 0.0
    under_pressure: int = 0
    under_pressure_success: int = 0
    pressured_accuracy: float = 0.0
    by_zone: dict[str, int] = Field(default_factory=dict)
    into_attacking_third: int = 0
    long_passes: int = 0
    long_pass_success: int = 0
    long_pass_accuracy: float = 0.0


class AttackingStats(BaseModel):
    shots: int = 0
    shots_on_target: int = 0
    goals: int = 0
    avg_shot_distance_m: float = 0.0
    avg_shot_speed_kph: float = 0.0
    avg_shot_quality: float = 0.0
    progressive_actions: int = 0
    attacking_third_entries: int = 0


class DefensiveStats(BaseModel):
    tackles: int = 0
    successful_tackles: int = 0
    pressure_events: int = 0
    successful_pressures: int = 0
    interceptions: int = 0
    recoveries: int = 0
    final_third_recoveries: int = 0


class TransitionStats(BaseModel):
    possession_won: int = 0
    possession_lost: int = 0
    transition_shots: int = 0


class TeamIntelligence(BaseModel):
    team_id: str
    team_name: str
    possession: PossessionStats = Field(default_factory=PossessionStats)
    passing: PassingStats = Field(default_factory=PassingStats)
    attacking: AttackingStats = Field(default_factory=AttackingStats)
    defensive: DefensiveStats = Field(default_factory=DefensiveStats)
    transitions: TransitionStats = Field(default_factory=TransitionStats)
    pressure_index: float = 50.0
    field_tilt_pct: float = 50.0


# ── Player-level stats ───────────────────────────────────────────────────

class PlayerIntelligence(BaseModel):
    player_id: str
    team_id: str
    name: str
    position: str
    passes: int = 0
    completed_passes: int = 0
    pass_accuracy: float = 0.0
    progressive_passes: int = 0
    avg_pass_distance_m: float = 0.0
    shots: int = 0
    shots_on_target: int = 0
    goals: int = 0
    tackles: int = 0
    successful_tackles: int = 0
    pressure_events: int = 0
    successful_pressures: int = 0
    interceptions: int = 0
    recoveries: int = 0
    carries: int = 0
    touches: int = 0
    crosses: int = 0
    fouls_committed: int = 0
    avg_x: Optional[float] = None
    avg_y: Optional[float] = None


# ── Possession sequence analysis ─────────────────────────────────────────

class PossessionSequenceAnalysis(BaseModel):
    sequence_id: str
    team_id: str
    start_time: float
    end_time: float
    duration_s: float
    starting_zone: str = "unknown"
    ending_zone: str = "unknown"
    num_events: int = 0
    passes: int = 0
    progressive_actions: int = 0
    pressure_received: int = 0
    shot_generated: bool = False
    outcome: PossessionOutcome = PossessionOutcome.TURNOVER


class PossessionIntelligence(BaseModel):
    """Aggregate possession analytics across all sequences."""
    total_sequences: int = 0
    avg_duration_s: float = 0.0
    median_duration_s: float = 0.0
    longest_duration_s: float = 0.0
    sequences_ending_shot: int = 0
    sequences_ending_goal: int = 0
    sequences_ending_turnover: int = 0
    sequences_with_progression: int = 0
    sequences_reaching_attacking_third: int = 0


# ── Pressure index ───────────────────────────────────────────────────────

class PressureIntelligence(BaseModel):
    """MatchLens Pressure Index (0–100).

    Formula:
        pressure_index = 25 * norm(volume) + 25 * norm(success_rate)
                       + 25 * norm(att_third_share) + 25 * norm(forced_turnovers_rate)

    This is a synthetic MatchLens metric — NOT an official statistic.
    """
    pressure_index: float = 50.0
    events_per_minute: float = 0.0
    success_rate: float = 0.0
    attacking_third_pressure: int = 0
    attacking_third_pressure_pct: float = 0.0
    turnovers_after_pressure: int = 0
    total_events: int = 0


# ── Shot intelligence ────────────────────────────────────────────────────

class ShotIntelligence(BaseModel):
    """Aggregate shot analytics.

    shot_quality is the MatchLens Shot Quality heuristic from Phase 1.
    """
    total: int = 0
    on_target: int = 0
    goals: int = 0
    avg_distance_m: float = 0.0
    avg_speed_kph: float = 0.0
    avg_quality: float = 0.0
    under_pressure: int = 0
    by_zone: dict[str, int] = Field(default_factory=dict)


# ── Territorial / field tilt ─────────────────────────────────────────────

class TerritorialIntelligence(BaseModel):
    """MatchLens Field Tilt and territorial analytics.

    field_tilt_pct = team attacking-third actions / total attacking-third actions.
    This is a synthetic MatchLens metric — NOT an official statistic.
    """
    field_tilt_pct: float = 50.0
    attacking_third_actions: int = 0
    middle_third_actions: int = 0
    defensive_third_actions: int = 0
    total_progressive_distance_m: float = 0.0
    avg_progressive_distance_m: float = 0.0
    progressive_action_count: int = 0
    progression_into_att_third: int = 0


# ── Rolling window snapshot ──────────────────────────────────────────────

class RollingWindowMetrics(BaseModel):
    timestamp: float
    window_seconds: int
    home_possession_pct: float = 50.0
    away_possession_pct: float = 50.0
    home_pressure_count: int = 0
    away_pressure_count: int = 0
    home_pressure_index: float = 50.0
    away_pressure_index: float = 50.0
    home_field_tilt: float = 50.0
    away_field_tilt: float = 50.0
    home_progressive_actions: int = 0
    away_progressive_actions: int = 0
    home_shots: int = 0
    away_shots: int = 0
    home_passes: int = 0
    away_passes: int = 0
    home_pass_success: int = 0
    away_pass_success: int = 0
    turnovers: int = 0
    event_density: float = 0.0


# ── Match state classification ───────────────────────────────────────────

class MatchStateClassification(BaseModel):
    """Deterministic state classification based on transparent thresholds.

    This is a MatchLens-derived classification — NOT an official metric.
    """
    state: MatchStateName
    confidence: float = Field(ge=0.0, le=1.0)
    timestamp: float
    minute: int
    team_id: Optional[str] = None
    evidence_ids: list[str] = Field(default_factory=list)


class StateTransition(BaseModel):
    transition_id: str
    timestamp: float
    minute: int
    previous_state: MatchStateName
    new_state: MatchStateName
    magnitude: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_summaries: list[str] = Field(default_factory=list)


# ── Significance ─────────────────────────────────────────────────────────

class SignificantMoment(BaseModel):
    moment_id: str
    timestamp: float
    minute: int
    moment_type: MomentType
    significance_score: float = Field(ge=0.0, le=100.0)
    tier: SignificanceTier
    team_id: Optional[str] = None
    player_id: Optional[str] = None
    reason_codes: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)


# ── Evidence ─────────────────────────────────────────────────────────────

class Evidence(BaseModel):
    evidence_id: str
    source_event_ids: list[str] = Field(default_factory=list)
    metric_name: str
    value: float
    baseline_value: Optional[float] = None
    delta: Optional[float] = None
    window: Optional[str] = None
    team_id: Optional[str] = None
    player_id: Optional[str] = None
    timestamp: float = 0.0
    evidence_type: EvidenceType = EvidenceType.METRIC


# ── Explanation context ──────────────────────────────────────────────────

class ExplanationContext(BaseModel):
    """Structured context for a significant moment.

    This object will be consumed by future AI Explanation Agents.
    It does NOT contain generated prose.
    """
    moment_id: str
    timestamp: float
    minute: int
    event_type: str
    primary_metric: str
    current_value: float
    baseline_value: Optional[float] = None
    delta: Optional[float] = None
    affected_team_id: Optional[str] = None
    related_player_ids: list[str] = Field(default_factory=list)
    related_event_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


# ── Team comparison ──────────────────────────────────────────────────────

class TeamComparison(BaseModel):
    home_team_id: str
    away_team_id: str
    possession_home: float = 50.0
    possession_away: float = 50.0
    pressure_index_home: float = 50.0
    pressure_index_away: float = 50.0
    field_tilt_home: float = 50.0
    field_tilt_away: float = 50.0
    pass_accuracy_home: float = 0.0
    pass_accuracy_away: float = 0.0
    progressive_passes_home: int = 0
    progressive_passes_away: int = 0
    shots_home: int = 0
    shots_away: int = 0
    shots_on_target_home: int = 0
    shots_on_target_away: int = 0
    goals_home: int = 0
    goals_away: int = 0
    avg_poss_duration_home: float = 0.0
    avg_poss_duration_away: float = 0.0


# ── Timeline ─────────────────────────────────────────────────────────────

class TimelineEntry(BaseModel):
    timestamp: float
    minute: int
    label: str
    moment_type: Optional[MomentType] = None
    significance_score: float = 0.0
    tier: Optional[SignificanceTier] = None
    team_id: Optional[str] = None
    event_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    state_before: Optional[MatchStateName] = None
    state_after: Optional[MatchStateName] = None


# ── Top-level intelligence ───────────────────────────────────────────────

class MatchIntelligence(BaseModel):
    """Complete intelligence output for one match.

    Deterministically derived from the Phase 1 event stream.
    """
    match_id: str
    analysis_version: str = "0.2.0"
    generated_at: str = ""

    summary: MatchSummaryIntel = Field(default_factory=MatchSummaryIntel)
    home_team: TeamIntelligence
    away_team: TeamIntelligence
    players: list[PlayerIntelligence] = Field(default_factory=list)

    possession_sequences: list[PossessionSequenceAnalysis] = Field(default_factory=list)
    possession_intelligence: PossessionIntelligence = Field(default_factory=PossessionIntelligence)

    rolling_windows: list[RollingWindowMetrics] = Field(default_factory=list)

    tactical_states: list[MatchStateClassification] = Field(default_factory=list)
    state_transitions: list[StateTransition] = Field(default_factory=list)

    detected_moments: list[SignificantMoment] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    explanation_contexts: list[ExplanationContext] = Field(default_factory=list)

    team_comparison: Optional[TeamComparison] = None
    timeline: list[TimelineEntry] = Field(default_factory=list)
