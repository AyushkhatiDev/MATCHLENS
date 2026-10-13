"""All tool implementations are projections of Phase 1/2/3 data."""
from typing import Annotated, Any, Literal
from pydantic import ConfigDict, Field, validate_call
from .store import IntelligenceStore, ToolError

Identifier = Annotated[str, Field(min_length=1, max_length=128)]
Timestamp = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Window = Literal[60, 180, 300, 600]
EvidenceIDs = Annotated[list[Identifier], Field(min_length=1, max_length=24)]
PageLimit = Annotated[int, Field(ge=1, le=200)]
Offset = Annotated[int, Field(ge=0)]
checked = validate_call(config=ConfigDict(strict=True))

class MatchLensTools:
    def __init__(self, store: IntelligenceStore):
        self.store = store

    @checked
    def get_match_summary(self) -> dict[str, Any]:
        """Read Phase 2 match totals only when the aggregate snapshot is not in the future."""
        self.store.require_aggregate()
        return {'match_id':self.store.intelligence.match_id,
                'snapshot_time':self.store.intelligence.summary.match_duration_s,
                'summary':self.store.intelligence.summary.model_dump(mode='json')}

    @checked
    def get_current_match_state(self) -> dict[str, Any]:
        """Read the latest published tactical state and recorded goal scoreline at the cutoff."""
        states = [s for s in self.store.intelligence.tactical_states if s.timestamp <= self.store.current_time]
        state = max(states, key=lambda s:s.timestamp) if states else None
        goals = [e for e in self.store.events if e.event_type.value=='GOAL' and e.timestamp<=self.store.current_time]
        goal = goals[-1] if goals else None
        return {'current_time':self.store.current_time, 'state':state.model_dump(mode='json') if state else None,
                'score':{'home':goal.home_score,'away':goal.away_score} if goal else None,
                'score_source_event_id':goal.event_id if goal else None,
                'score_caveat':None if goal else 'No goal scoreline event is available; score is not inferred.',
                'evidence_ids':state.evidence_ids if state else []}

    @checked
    def get_team_summary(self, team_id: Identifier) -> dict[str, Any]:
        """Read an existing Phase 2 team summary; future aggregate totals are withheld."""
        team = self.store.team(team_id)
        self.store.require_aggregate()
        return team.model_dump(mode='json')

    @checked
    def get_player_summary(self, player_id: Identifier) -> dict[str, Any]:
        """Read an existing Phase 2 player summary; future aggregate totals are withheld."""
        player = self.store.player(player_id)
        self.store.require_aggregate()
        return player.model_dump(mode='json')

    @checked
    def get_player_activity(self, player_id: Identifier, window: Window = 300) -> dict[str, Any]:
        """Retrieve bounded source activity and existing player evidence; never invent rolling player metrics."""
        self.store.player(player_id)
        records = [e.model_dump(mode='json') for e in self.store.events
            if max(0,self.store.current_time-window)<=e.timestamp<=self.store.current_time
            and (e.player_id==player_id or getattr(e,'scorer_player_id',None)==player_id)]
        evidence = [e.model_dump(mode='json') for e in self.store.evidence.values()
                    if e.player_id==player_id and max(0,self.store.current_time-window)<=e.timestamp<=self.store.current_time]
        return {'player_id':player_id,'window_seconds':window,'as_of':self.store.current_time,
                'events':records[:200], 'truncated':len(records)>200, 'evidence':evidence,
                'rolling_player_metrics':None,
                'caveat':'Phase 2 does not publish rolling player statistics; these are source records, not involvement rates.'}

    @checked
    def get_recent_events(self, start_time: Timestamp, end_time: Timestamp,
                          limit: PageLimit = 100, offset: Offset = 0) -> dict[str, Any]:
        """Retrieve a paginated source-event interval, at most 200 events per call."""
        self._interval(start_time,end_time)
        rows = [e.model_dump(mode='json') for e in self.store.events if start_time<=e.timestamp<=end_time]
        return {'events':rows[offset:offset+limit],
                'next_offset':offset+limit if offset+limit<len(rows) else None}

    @checked
    def get_possession_sequence(self, sequence_id: Identifier) -> dict[str, Any]:
        """Read a completed Phase 2 possession sequence without using future events."""
        for s in self.store.intelligence.possession_sequences:
            if s.sequence_id==sequence_id and s.end_time<=self.store.current_time:
                return s.model_dump(mode='json')
        raise ToolError('NOT_FOUND','Possession sequence is unknown or not completed at this time.')

    @checked
    def get_recent_possession_sequences(self, team_id: Identifier, window: Window = 300,
                                        limit: PageLimit = 100) -> dict[str, Any]:
        """Read recently completed team possession sequences from Phase 2."""
        self.store.team(team_id)
        rows = [s.model_dump(mode='json') for s in self.store.intelligence.possession_sequences
                if s.team_id==team_id and max(0,self.store.current_time-window)<=s.end_time<=self.store.current_time]
        return {'sequences':rows[-limit:], 'truncated':len(rows)>limit}

    def _team_window(self, team_id: str, window: int, attributes: list[str]) -> dict[str, Any]:
        self.store.team(team_id)
        w = self.store.window(window)
        side = 'home' if team_id==self.store.intelligence.home_team.team_id else 'away'
        return {'team_id':team_id,'timestamp':w.timestamp,'window_seconds':w.window_seconds,
                'metrics':{attr:getattr(w,side+'_'+attr) for attr in attributes},
                'source':'Phase 2 rolling_windows', 'metric_family':'MatchLens synthetic heuristic'}

    @checked
    def get_pressure_metrics(self, team_id: Identifier, window: Window = 300) -> dict[str, Any]:
        """Read published pressure index and pressure count for a team and window."""
        return self._team_window(team_id,window,['pressure_index','pressure_count'])

    @checked
    def get_field_tilt(self, team_id: Identifier, window: Window = 300) -> dict[str, Any]:
        """Read the Phase 2 share of attacking-third actions, not time-based possession."""
        return self._team_window(team_id,window,['field_tilt'])

    @checked
    def get_progression_metrics(self, team_id: Identifier, window: Window = 300) -> dict[str, Any]:
        """Read the existing progressive-action count for a team and rolling window."""
        return self._team_window(team_id,window,['progressive_actions'])

    @checked
    def get_shot_context(self, event_id: Identifier) -> dict[str, Any]:
        """Read an actual source shot with distance, quality, pressure and coordinates."""
        for e in self.store.events:
            if e.event_id==event_id and e.event_type.value=='SHOT' and e.timestamp<=self.store.current_time:
                return e.model_dump(mode='json')
        raise ToolError('NOT_FOUND','Shot event is unknown or unavailable at this time.')

    @checked
    def get_evidence(self, evidence_id: Identifier) -> dict[str, Any]:
        """Resolve one original Phase 2 or projected Phase 3 evidence packet."""
        e = self.store.evidence.get(evidence_id)
        if e is None or e.timestamp>self.store.current_time:
            raise ToolError('NOT_FOUND','Evidence is unknown or unavailable at this time.')
        return e.model_dump(mode='json')

    @checked
    def get_evidence_bundle(self, evidence_ids: EvidenceIDs) -> dict[str, Any]:
        """Resolve a bounded list of evidence IDs, failing on any unavailable ID."""
        return {'evidence':[self.get_evidence(eid) for eid in dict.fromkeys(evidence_ids)]}

    @checked
    def get_moment(self, moment_id: Identifier) -> dict[str, Any]:
        """Read an existing Phase 2 significant moment, preserving score and reason codes."""
        return self.store.moment(moment_id).model_dump(mode='json')

    @checked
    def get_explanation_context(self, moment_id: Identifier) -> dict[str, Any]:
        """Read canonical Phase 3 context, selected packets, caveats and preserved Phase 2 context."""
        return self.store.explanation(moment_id).model_dump(mode='json')

    @checked
    def get_timeline(self, start_time: Timestamp, end_time: Timestamp,
                     limit: PageLimit = 100, offset: Offset = 0) -> dict[str, Any]:
        """Read the existing Phase 2 timeline. Future state_after labels are redacted."""
        self._interval(start_time,end_time)
        rows = []
        for entry in self.store.intelligence.timeline:
            if start_time<=entry.timestamp<=end_time:
                value = entry.model_dump(mode='json')
                # Phase 2 state_after may reference a checkpoint after the event.
                value['state_after'] = None
                rows.append(value)
        return {'timeline':rows[offset:offset+limit],
                'next_offset':offset+limit if offset+limit<len(rows) else None}

    def _interval(self, start_time: float, end_time: float) -> None:
        if start_time>end_time:
            raise ToolError('INVALID_ARGUMENT','start_time must not exceed end_time.')
        if end_time>self.store.current_time:
            raise ToolError('FUTURE_DATA','Requested interval exceeds the snapshot cutoff.')
