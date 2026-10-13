"""Read-only snapshots; no statistical calculations or future-data access."""
import math
from simulator.intelligence.models import MatchIntelligence
from simulator.intelligence.explanation import ExplanationEngine, ExplanationConfig, ExplanationReport
from simulator.intelligence.explanation.validator import validate_input
from simulator.models.events import MatchEvent

class ToolError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

class IntelligenceStore:
    def __init__(self, intelligence: MatchIntelligence, explanations: ExplanationReport,
                 events: list[MatchEvent], current_time: float | None = None,
                 explanation_config: ExplanationConfig | None = None):
        validate_input(intelligence, events, True)
        if explanations.match_id != intelligence.match_id:
            raise ValueError('Explanation report belongs to another match.')
        # Validate incoming Phase 3 material once at the boundary. Scope() reuses
        # this trusted immutable snapshot, rather than recomputing analytics.
        expected = ExplanationEngine(explanation_config, events).explain(intelligence)
        if expected != explanations:
            raise ValueError('Explanation report is inconsistent with deterministic sources.')
        self.intelligence = intelligence.model_copy(deep=True)
        self.report = explanations.model_copy(deep=True)
        self.events = sorted([e.model_copy(deep=True) for e in events], key=lambda e:(e.timestamp,e.event_id))
        self.current_time = intelligence.summary.match_duration_s if current_time is None else current_time
        self._validate_time(self.current_time)
        if self.current_time > intelligence.summary.match_duration_s:
            raise ValueError('Snapshot cutoff exceeds the published analysis horizon.')
        self.evidence = {e.evidence_id:e for e in self.intelligence.evidence}
        self.evidence.update({e.evidence_id:e for e in self.report.evidence})

    @staticmethod
    def _validate_time(value: float) -> None:
        if not math.isfinite(value) or value < 0:
            raise ValueError('Invalid snapshot time.')

    def scope(self, timestamp: float) -> 'IntelligenceStore':
        self._validate_time(timestamp)
        if timestamp > self.current_time:
            raise ToolError('FUTURE_DATA', 'Requested time exceeds the available snapshot.')
        result = object.__new__(IntelligenceStore)
        result.__dict__ = {**self.__dict__, 'current_time': timestamp}
        return result

    def require_aggregate(self) -> None:
        if self.intelligence.summary.match_duration_s > self.current_time:
            raise ToolError('UNAVAILABLE_AT_TIME', 'Aggregate totals describe a later snapshot; request a current Phase 2 snapshot.')

    def team(self, team_id: str):
        for team in (self.intelligence.home_team, self.intelligence.away_team):
            if team.team_id == team_id:
                return team
        raise ToolError('NOT_FOUND', 'Unknown team_id.')

    def player(self, player_id: str):
        for player in self.intelligence.players:
            if player.player_id == player_id:
                return player
        raise ToolError('NOT_FOUND', 'Unknown player_id.')

    def moment(self, moment_id: str):
        for moment in self.intelligence.detected_moments:
            if moment.moment_id == moment_id and moment.timestamp <= self.current_time:
                return moment
        raise ToolError('NOT_FOUND', 'Moment is unknown or unavailable at this time.')

    def explanation(self, moment_id: str):
        self.moment(moment_id)
        for explanation in self.report.explanations:
            if explanation.moment_id == moment_id:
                return explanation
        raise ToolError('NOT_EXPLAINED', 'Moment was filtered by Phase 3 prioritization.')

    def window(self, window: int):
        rows = [w for w in self.intelligence.rolling_windows
                if w.window_seconds == window and w.timestamp <= self.current_time]
        if not rows:
            raise ToolError('UNAVAILABLE_AT_TIME', 'No published rolling window is available at this time.')
        return max(rows, key=lambda w:w.timestamp)
