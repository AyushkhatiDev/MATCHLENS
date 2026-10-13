from typing import Literal, Annotated
from pydantic import BaseModel, Field
from simulator.models.events import MatchEvent
from simulator.intelligence.models import TeamIntelligence, PlayerIntelligence, MatchStateClassification, RollingWindowMetrics, SignificantMoment
from simulator.intelligence.explanation.models import MomentExplanation, TraceEvidence
from simulator.agentic.models import ExplanationResult, NarrativeResult

from .temporal import Temporal, Change, Truth
from .execution import Execution

class Club(BaseModel):
    team_id: str
    name: str
    short_name: str

class MatchInfo(BaseModel):
    match_id: str
    home: Club
    away: Club
    competition: str
    venue: str
    synthetic: bool = True
    runtime_mode: Literal['mock','foundry'] = 'mock'
    event_source: Literal['local','eventhub'] = 'local'
    demo_controls: bool = True
    foundry_hero_only: bool = False

class Insight(BaseModel):
    insight_id: str
    moment: SignificantMoment
    explanation: MomentExplanation | None = None
    evidence: list[TraceEvidence] = []
    source_events: list[dict] = []
    agent_explanation: ExplanationResult | None = None
    narrative: NarrativeResult | None = None
    workflow_status: Literal['PROCESSING','COMPLETE','PARTIAL','FAILED','NO_EXPLANATION'] = 'PROCESSING'
    trace_id: str | None = None
    change: Change | None = None
    truth: Truth | None = None
    execution: Execution | None = None

class State(BaseModel):
    status: Literal['waiting','running','paused','complete','error'] = 'waiting'
    simulation_time: float = 0
    home_score: int = 0
    away_score: int = 0
    total_events: int = 0
    speed: float = 100
    tactical_state: MatchStateClassification | None = None
    home_team: TeamIntelligence
    away_team: TeamIntelligence
    players: list[PlayerIntelligence]
    windows: list[RollingWindowMetrics] = []
    possession_player_id: str | None = None
    temporal: Temporal = Field(default_factory=Temporal)

class Snapshot(BaseModel):
    match: MatchInfo
    state: State
    events: list[dict]
    insights: list[Insight]
    stream_cursor: int

class Update(BaseModel):
    type: Literal['MATCH_STATE_UPDATED','RESET']
    match_id: str
    timestamp: float
    sequence_number: int
    trace_id: str | None = None
    payload: State | Snapshot

class EventMessage(BaseModel):
    type: Literal['EVENT_RECEIVED'] = 'EVENT_RECEIVED'
    match_id: str
    timestamp: float
    sequence_number: int
    trace_id: str | None = None
    payload: dict

class InsightMessage(BaseModel):
    type: Literal['MOMENT_DETECTED','EXPLANATION_READY','NARRATIVE_READY']
    match_id: str
    timestamp: float
    sequence_number: int
    trace_id: str | None = None
    payload: Insight

Message = Annotated[Update | EventMessage | InsightMessage, Field(discriminator='type')]

class Control(BaseModel):
    action: Literal['start','pause','resume','speed']
    speed: Literal[1,10,20,100] = 100
