"""Strict, serializable contracts for deterministic explanation output."""
from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from simulator.intelligence.models import Evidence, SignificanceTier

VERSION = "1.0"

class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class ExplanationType(str, Enum):
    GOAL = "GOAL"
    PRESSURE_SHIFT = "PRESSURE_SHIFT"
    FIELD_TILT_SHIFT = "FIELD_TILT_SHIFT"
    STATE_CHANGE = "STATE_CHANGE"
    POSSESSION_SWING = "POSSESSION_SWING"
    SHOT_THREAT = "SHOT_THREAT"
    PROGRESSION_SURGE = "PROGRESSION_SURGE"
    PLAYER_ACTIVITY_SHIFT = "PLAYER_ACTIVITY_SHIFT"
    HIGH_PRESSURE_PHASE = "HIGH_PRESSURE_PHASE"
    TRANSITION_PHASE = "TRANSITION_PHASE"
    CONTROL_SHIFT = "CONTROL_SHIFT"
    CHAOS_PHASE = "CHAOS_PHASE"
    MAJOR_MOMENT = "MAJOR_MOMENT"

class EvidenceChain(Model):
    chain_id: str
    evidence_ids: list[str]
    logical_relationship: Literal["SUPPORTS", "CONTEXTUALIZES", "PRECEDES", "FOLLOWS", "CORROBORATES"]
    strength: float = Field(ge=0, le=1)

class SignificanceBreakdown(Model):
    score: float = Field(ge=0, le=100)
    contributing_factors: list[str]
    thresholds_triggered: dict[str, float] = Field(default_factory=dict)
    evidence_ids: list[str]
    formula: str
    reconstructed_score: float | None = Field(default=None, ge=0, le=100)

class MetricChange(Model):
    metric: str
    baseline: float | None = None
    current: float
    delta: float | None = None
    percentage_delta: float | None = None
    unit: str = "value"
    window: str | None = None
    baseline_timestamp: float | None = None
    current_timestamp: float
    evidence_id: str
    team_id: str | None = None
    player_id: str | None = None

class GoalContext(Model):
    goal_time: float = Field(ge=0)
    scoring_team: str
    scorer: str | None = None
    preceding_sequence_id: str | None = None
    sequence_duration: float | None = Field(default=None, ge=0)
    passes: int | None = Field(default=None, ge=0)
    progressive_actions: int | None = Field(default=None, ge=0)
    pressure_received: int | None = Field(default=None, ge=0)
    shot_context: dict[str, Any] = Field(default_factory=dict)
    direct_event_ids: list[str] = Field(default_factory=list)
    contextual_event_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)

class GameRhythmAssessment(Model):
    rhythm_type: Literal["CONTROLLED", "CONTESTED", "HIGH_TEMPO", "CHAOTIC", "TRANSITION_HEAVY", "LOW_EVENT", "BALANCED"]
    score: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    timestamp: float = Field(ge=0)
    team_id: str | None = None
    evidence_ids: list[str]
    contributing_metrics: dict[str, float]
    previous_rhythm: str | None = None
    changes: dict[str, float] = Field(default_factory=dict)
    explanation: str = ""

class TraceEvidence(Evidence):
    """Phase 3 projections copy existing values; source_paths resolve in Phase 2 JSON."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    source_paths: list[str] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)
    origin: Literal["PHASE2", "SOURCE_EVENT"] = "PHASE2"

class MomentExplanation(Model):
    explanation_id: str
    moment_id: str
    timestamp: float = Field(ge=0)
    explanation_type: ExplanationType
    title: str
    what_happened: str
    what_changed: str
    why_it_matters: str
    affected_team_id: str | None = None
    affected_player_ids: list[str] = Field(default_factory=list)
    significance_score: float = Field(ge=0, le=100)
    significance_tier: SignificanceTier
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str]
    source_event_ids: list[str]
    supporting_metrics: dict[str, MetricChange]
    context: dict[str, Any]
    caveats: list[str]
    evidence_chains: list[EvidenceChain]
    significance_breakdown: SignificanceBreakdown
    goal_context: GoalContext | None = None
    explanation_version: str = VERSION

class ExplanationReport(Model):
    match_id: str
    explanation_version: str = VERSION
    explanations: list[MomentExplanation]
    evidence: list[TraceEvidence]
    rhythm_assessments: list[GameRhythmAssessment]

class ExplanationConfig(Model):
    minimum_score: float = Field(default=40, ge=0, le=100)
    explain_notable: bool = True
    max_evidence: int = Field(default=6, ge=3, le=6)
    goal_lookback_seconds: int = Field(default=120, gt=0)
