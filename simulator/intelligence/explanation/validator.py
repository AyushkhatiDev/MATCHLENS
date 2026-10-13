"""Fail closed on inconsistent sources and untraceable generated claims."""
import math
import re
from simulator.intelligence.models import MatchIntelligence
from simulator.models.events import MatchEvent
from .models import MomentExplanation

FORBIDDEN = re.compile(r"\b(caused|causes|forced|led to|because of|resulted in|elite|dominant|dominated|best|worst|greatest|unbelievable|man of the match|Premier League|Manchester|Liverpool|Haaland|Salah)\b", re.I)

def validate_input(intelligence: MatchIntelligence, events: list[MatchEvent], has_events: bool) -> None:
    teams = {intelligence.home_team.team_id, intelligence.away_team.team_id}
    players = {p.player_id for p in intelligence.players}
    if len(teams) != 2 or len(players) != len(intelligence.players):
        raise ValueError("Duplicate team/player IDs")
    for collection, attr in ((intelligence.evidence, "evidence_id"),
                             (intelligence.detected_moments, "moment_id"),
                             (events, "event_id")):
        ids = [getattr(x, attr) for x in collection]
        if len(set(ids)) != len(ids):
            raise ValueError(f"Duplicate {attr}")
    event_map = {e.event_id: e for e in events}
    evidence_map = {e.evidence_id: e for e in intelligence.evidence}
    def references(item):
        timestamp = getattr(item, "timestamp", 0)
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("Impossible source timestamp")
        if getattr(item, "team_id", None) and item.team_id not in teams:
            raise ValueError("Unknown team")
        if getattr(item, "player_id", None) and item.player_id not in players:
            raise ValueError("Unknown player")
        for eid in getattr(item, "evidence_ids", []):
            if eid not in evidence_map:
                raise ValueError("Dangling evidence reference")
        for eid in getattr(item, "source_event_ids", []):
            if has_events and eid not in event_map:
                raise ValueError("Dangling source-event reference")
            if eid in event_map and event_map[eid].timestamp > item.timestamp:
                raise ValueError("Future source event")
    for item in [*intelligence.evidence, *intelligence.detected_moments,
                 *intelligence.tactical_states, *intelligence.state_transitions]:
        references(item)
    for e in events:
        references(e)
        if e.match_id != intelligence.match_id:
            raise ValueError("Source event belongs to another match")
        for field in ("scorer_player_id", "receiver_player_id", "target_player_id", "assist_player_id"):
            pid = getattr(e, field, None)
            if pid and pid not in players:
                raise ValueError("Unknown event player")
        for field in ("pressing_team_id", "target_team_id", "from_team_id", "to_team_id"):
            tid = getattr(e, field, None)
            if tid and tid not in teams:
                raise ValueError("Unknown event team")
    def nonnegative_metrics(value):
        if isinstance(value, dict):
            for v in value.values():
                nonnegative_metrics(v)
        elif isinstance(value, list):
            for v in value:
                nonnegative_metrics(v)
        elif isinstance(value, (int, float)) and (not math.isfinite(value) or value < 0):
            raise ValueError("Impossible aggregate or sequence metric")
    for model in [intelligence.summary, intelligence.home_team, intelligence.away_team,
                  intelligence.possession_intelligence, *intelligence.players,
                  *intelligence.possession_sequences]:
        nonnegative_metrics(model.model_dump())
    for sequence in intelligence.possession_sequences:
        if sequence.end_time < sequence.start_time or abs(sequence.end_time-sequence.start_time-sequence.duration_s) > .011:
            raise ValueError("Conflicting possession sequence timestamps")
    for p in intelligence.players:
        if p.team_id not in teams:
            raise ValueError("Player belongs to unknown team")
    moment_map = {m.moment_id: m for m in intelligence.detected_moments}
    for c in intelligence.explanation_contexts:
        if c.moment_id not in moment_map or c.timestamp != moment_map[c.moment_id].timestamp:
            raise ValueError("Conflicting explanation context timestamp/moment")
        references(c)
        if c.affected_team_id and c.affected_team_id not in teams:
            raise ValueError("Unknown context team")
        if has_events and not set(c.related_event_ids) <= event_map.keys():
            raise ValueError("Unknown context source event")
        if not set(c.related_player_ids) <= players:
            raise ValueError("Unknown context player")
    for e in intelligence.evidence:
        if not math.isfinite(e.value) or e.value < 0:
            raise ValueError("Impossible evidence value")
        if e.baseline_value is not None:
            if not math.isfinite(e.baseline_value) or e.baseline_value < 0:
                raise ValueError("Impossible baseline")
            if e.delta is None or abs(e.value - e.baseline_value - e.delta) > .011:
                raise ValueError("Inconsistent metric delta")
        if e.metric_name in ("pressure_index", "field_tilt", "possession_pct") and e.value > 100:
            raise ValueError("Metric outside percentage/index range")
    for m in intelligence.detected_moments:
        for event_id in m.source_event_ids:
            if event_id in event_map:
                event = event_map[event_id]
                if event.timestamp != m.timestamp or (m.team_id and event.team_id != m.team_id):
                    raise ValueError("Moment and triggering event disagree")
                if m.player_id and m.player_id != (getattr(event, "scorer_player_id", None) or event.player_id):
                    raise ValueError("Moment and triggering player disagree")
        if m.moment_type.value == "PLAYER_SPIKE":
            if not m.player_id or not m.evidence_ids or evidence_map[m.evidence_ids[0]].player_id != m.player_id:
                raise ValueError("Player spike requires player-specific evidence")
        for eid in m.evidence_ids:
            if evidence_map[eid].timestamp > m.timestamp:
                raise ValueError("Future moment evidence")
    for e in intelligence.evidence:
        if e.window == "5m" and e.metric_name in ("pressure_index", "field_tilt", "possession_pct"):
            snapshots = sorted([w for w in intelligence.rolling_windows
                                if w.window_seconds == 300 and w.timestamp <= e.timestamp],
                               key=lambda w: w.timestamp)
            if not snapshots or snapshots[-1].timestamp != e.timestamp or e.team_id not in teams:
                raise ValueError("Shift evidence does not resolve to its rolling snapshot")
            side = "home" if e.team_id == intelligence.home_team.team_id else "away"
            attr = side + "_" + e.metric_name
            if abs(getattr(snapshots[-1], attr) - e.value) > .011:
                raise ValueError("Shift evidence conflicts with rolling current value")
            if e.baseline_value is not None and (len(snapshots) < 2 or
                abs(getattr(snapshots[-2], attr) - e.baseline_value) > .011):
                raise ValueError("Shift evidence conflicts with rolling baseline")
    for w in intelligence.rolling_windows:
        if w.window_seconds <= 0 or w.timestamp < 0:
            raise ValueError("Impossible window")
        for key, value in w.model_dump().items():
            if not math.isfinite(value) or value < 0:
                raise ValueError("Impossible rolling metric")
            if any(x in key for x in ("_pct", "_field_tilt", "_pressure_index")) and value > 100:
                raise ValueError("Rolling share/index outside range")
        for side in ("home", "away"):
            if getattr(w, side + "_pass_success") > getattr(w, side + "_passes"):
                raise ValueError("Pass successes exceed attempts")
        if abs(w.home_possession_pct + w.away_possession_pct - 100) > .11:
            raise ValueError("Possession shares do not sum to 100")
        if abs(w.home_field_tilt + w.away_field_tilt - 100) > .11:
            raise ValueError("Field tilt shares do not sum to 100")

def validate_text_fields(fields: dict[str, str]) -> None:
    if FORBIDDEN.search(" ".join(fields.values())):
        raise ValueError("Unsupported causal or subjective language")

class ExplanationValidator:
    def validate(self, explanation: MomentExplanation, *, engine, intelligence: MatchIntelligence) -> None:
        """Rebuild canonical claims from immutable input; arbitrary prose fails closed.

        Matching a numeric token anywhere in a match is insufficient: the whole
        claim, its metric/team/window and all structured fields must match.
        """
        text = " ".join(getattr(explanation, field) for field in
                        ("title", "what_happened", "what_changed", "why_it_matters"))
        if FORBIDDEN.search(text):
            raise ValueError("Unsupported causal or subjective language")
        moments = {m.moment_id: m for m in intelligence.detected_moments}
        if explanation.moment_id not in moments:
            raise ValueError("Unknown explanation moment")
        expected = engine.explain_moment(moment=moments[explanation.moment_id],
                                         intelligence=intelligence)
        if expected != explanation:
            raise ValueError("Explanation claims do not match source evidence/templates")
