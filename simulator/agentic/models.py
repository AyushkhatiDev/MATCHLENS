"""Serializable shared state, typed handoffs, and bounded execution traces."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from simulator.intelligence.models import SignificantMoment
from simulator.intelligence.explanation.models import MomentExplanation, TraceEvidence

Role = Literal['tactical', 'player', 'context', 'explanation', 'narrative']
Status = Literal['PENDING', 'RUNNING', 'COMPLETE', 'PARTIAL', 'FAILED']

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class AudienceProfile(Model):
    audience: Literal['fan', 'analyst'] = 'fan'
    duration_hint: Literal['short', 'standard'] = 'short'

class EvidenceStatement(Model):
    statement_id: str
    text: str
    evidence_ids: list[str] = Field(min_length=1)
    category: Literal['trigger', 'change', 'interpretation', 'metric', 'player', 'context', 'contradiction']

class Finding(Model):
    finding_type: str
    finding: str
    statement_ids: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    caveats: list[str]
    team_id: str | None = None
    player_id: str | None = None
    event_ids: list[str] = Field(default_factory=list)
    timestamp: float = Field(ge=0)

class TacticalFinding(Finding):
    agent: Literal['tactical'] = 'tactical'
    contradictions: list[str] = Field(default_factory=list)

class PlayerFinding(Finding):
    agent: Literal['player'] = 'player'

class ContextFinding(Finding):
    agent: Literal['context'] = 'context'

class ExplanationResult(Model):
    explanation_id: str
    moment_id: str
    what_happened: str
    what_changed: str
    why_it_matters: str
    statement_ids: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    caveats: list[str]

class NarrativeResult(Model):
    headline: str
    summary: str
    audience: Literal['fan', 'analyst']
    duration_hint: Literal['short', 'standard']
    statement_ids: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    caveats: list[str]

class AgentError(Model):
    agent: str
    stage: str
    code: str
    message: str
    recoverable: bool

class ToolCallRecord(Model):
    tool: str
    arguments: dict[str, Any]
    evidence_ids: list[str] = Field(default_factory=list)
    status: Literal['OK', 'ERROR']
    error_code: str | None = None

class AgentExecutionRecord(Model):
    agent: Role
    status: Literal['OK', 'ERROR']
    tool_calls: list[ToolCallRecord]
    evidence_ids: list[str] = Field(default_factory=list)

class TraceStep(Model):
    stage: str
    status: Literal['OK', 'ERROR', 'SKIPPED'] = 'OK'
    detail: str = ''

class MatchAgentState(Model):
    match_id: str
    current_time: float = Field(ge=0)
    triggered_moment: SignificantMoment
    explanation_context: MomentExplanation
    evidence: list[TraceEvidence]
    statements: list[EvidenceStatement]
    tactical_findings: list[TacticalFinding] = Field(default_factory=list)
    player_findings: list[PlayerFinding] = Field(default_factory=list)
    context_findings: list[ContextFinding] = Field(default_factory=list)
    explanation: ExplanationResult | None = None
    narrative: NarrativeResult | None = None
    audience_profile: AudienceProfile = Field(default_factory=AudienceProfile)
    workflow_status: Status = 'PENDING'
    errors: list[AgentError] = Field(default_factory=list)
    missing_agents: list[str] = Field(default_factory=list)
    selected_agents: list[Role] = Field(default_factory=list)
    confidence_cap: float = Field(ge=0, le=1)
    required_caveats: list[str]
    explanation_validated: bool = False
    trace_id: str
    agent_executions: list[AgentExecutionRecord] = Field(default_factory=list)
    trace_steps: list[TraceStep] = Field(default_factory=list)

class WorkflowResult(Model):
    trace_id: str
    match_id: str
    moment_id: str
    runtime_mode: Literal['mock', 'foundry']
    errors: list[AgentError]
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str]
    final_explanation_id: str | None
    workflow_status: Status
    selected_agents: list[Role]
    missing_agents: list[str]
    explanation: ExplanationResult | None
    narrative: NarrativeResult | None
    state: MatchAgentState
