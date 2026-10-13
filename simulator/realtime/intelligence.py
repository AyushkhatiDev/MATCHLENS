"""Incremental indexes around Phase 2's deterministic projection components.

No analyze() call on ingestion and no parallel implementation of its formulas.
The batch component builders project only indexed inputs; windows inspect at
most the last 600 simulation seconds. Snapshot assembly runs at moments/end.
"""
from collections import defaultdict, deque
import hashlib
import json
from simulator.intelligence.engine import IntelligenceEngine, WINDOW_SIZES
from simulator.models.events import EventType

def moment_key(moment):
    # Phase 2 ordinal IDs change as categories grow; identity across snapshots
    # comes from immutable semantic fields, never from those ordinal IDs.
    value = [moment.timestamp, moment.moment_type.value, moment.team_id,
             moment.player_id, moment.source_event_ids, moment.reason_codes]
    return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()

class IncrementalIntelligence:
    def __init__(self, match):
        self.match = match
        self.events = []
        self.engine = IntelligenceEngine(match, self.events)
        self.typed = defaultdict(list)
        self.sequence_events = defaultdict(list)
        self.sequences = {}
        self.player_events = defaultdict(lambda: defaultdict(list))
        self.team_events = {t: defaultdict(list) for t in (self.engine.home_id, self.engine.away_id)}
        self.players = {p.player_id: p for p in self.engine._build_player_stats(self.typed)}
        self.teams = {t: self.engine._build_team_stats(t, self.team_events[t], []) for t in self.team_events}
        self.summary = self.engine._build_summary(self.typed)
        self.recent = deque()
        self.windows = []
        self.next_checkpoint = 300
        self.possession_team_id = None
        self.sealed = False
        self.latest_snapshot = None

    def snapshot(self, horizon=None):
        summary = self.summary.model_copy(deep=True)
        if horizon is not None:
            summary.match_duration_s = horizon
        self.latest_snapshot = self.engine.assemble(
            self.typed, sorted(self.sequences.values(), key=lambda s: s.start_time), summary,
            self.teams[self.engine.home_id].model_copy(deep=True),
            self.teams[self.engine.away_id].model_copy(deep=True),
            sorted(self.players.values(), key=lambda p: (p.team_id, p.player_id)),
            windows=self.windows.copy())
        return self.latest_snapshot

    def _checkpoint(self, timestamp):
        while self.recent and self.recent[0].timestamp < timestamp - 600:
            self.recent.popleft()
        for size in WINDOW_SIZES:
            self.windows.append(self.engine._calc_window(list(self.recent), timestamp, size))
        snapshot = self.snapshot(max(self.summary.match_duration_s, timestamp))
        return snapshot, [m for m in snapshot.detected_moments if m.timestamp == timestamp]

    def push(self, event):
        if self.sealed:
            raise ValueError('Match has already ended')
        if event.match_id != self.match.match_id:
            raise ValueError('Event belongs to another match')
        updates = []
        # Close only after advancing past a checkpoint so tied timestamps are
        # included together. The advancing event is not visible to that snapshot.
        while self.next_checkpoint < event.timestamp:
            updates.append(self._checkpoint(float(self.next_checkpoint)))
            self.next_checkpoint += 300
        self.events.append(event)
        self.typed['all'].append(event)
        self.typed[event.event_type.value].append(event)
        self.recent.append(event)
        while self.recent and self.recent[0].timestamp < event.timestamp - 600:
            self.recent.popleft()
        sid = event.possession_sequence_id
        if sid:
            self.sequence_events[sid].append(event)
            sequence = self.engine._build_possession_sequences({'all': self.sequence_events[sid]})
            self.sequences[sid] = sequence[0]
            self.possession_team_id = sequence[0].team_id
        if event.event_type == EventType.POSSESSION_CHANGE:
            self.possession_team_id = event.to_team_id
        # Team indexes include shared possession-change inputs for transition
        # formulas; all other events are indexed only for their attributed team.
        for team_id, typed in self.team_events.items():
            if (event.team_id == team_id or getattr(event, 'pressing_team_id', None) == team_id
                    or event.event_type == EventType.POSSESSION_CHANGE):
                typed['all'].append(event)
                typed[event.event_type.value].append(event)
        sequences = list(self.sequences.values())
        for team_id in self.teams:
            self.teams[team_id] = self.engine._build_team_stats(team_id, self.team_events[team_id], sequences)
        if event.player_id in self.engine._players:
            typed = self.player_events[event.player_id]
            typed['all'].append(event)
            typed[event.event_type.value].append(event)
            all_players = self.engine._players
            try:
                self.engine._players = {event.player_id: all_players[event.player_id]}
                self.players[event.player_id] = self.engine._build_player_stats(typed)[0]
            finally:
                self.engine._players = all_players
        self.summary = self.engine._build_summary(self.typed)
        self.engine.update_team_indexes(self.typed, event.timestamp,
            self.teams[self.engine.home_id], self.teams[self.engine.away_id])
        if event.event_type in (EventType.GOAL, EventType.SHOT):
            # Reuse the significance detector on this single event to avoid
            # constructing snapshots for shots that produce no moment.
            scratch = IntelligenceEngine(self.match, [])
            candidates = scratch._detect_moments({event.event_type.value: [event]}, [], [], [])
            if candidates:
                snapshot = self.snapshot()
                updates.append((snapshot, [m for m in snapshot.detected_moments
                                            if event.event_id in m.source_event_ids]))
        if event.event_type == EventType.MATCH_END:
            while self.next_checkpoint <= event.timestamp:
                updates.append(self._checkpoint(float(self.next_checkpoint)))
                self.next_checkpoint += 300
            # Preserve Phase 2's integer final checkpoint, including its unusual
            # subsecond endpoint behavior. No metric/timing correction is made.
            if self.windows and self.windows[-1].timestamp < event.timestamp:
                updates.append(self._checkpoint(float(int(event.timestamp))))
            self.sealed = True
            self.snapshot()
        return updates

    def finish(self):
        if not self.sealed:
            # Partial streams need explicit flush, rather than fabricating END.
            ts = self.summary.match_duration_s
            while self.next_checkpoint <= ts:
                self._checkpoint(float(self.next_checkpoint))
                self.next_checkpoint += 300
            if self.windows and self.windows[-1].timestamp < ts:
                self._checkpoint(float(int(ts)))
            self.sealed = True
        return self.snapshot()
