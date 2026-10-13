"""Main intelligence engine — orchestrates all analytics modules.

Takes a Phase 1 event stream and produces a complete :class:`MatchIntelligence`.
All computation is deterministic: identical inputs always produce identical outputs.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from simulator.models.match import (
    Match,
    PITCH_LENGTH_M,
    classify_third,
    PitchThird,
)
from simulator.models.events import (
    EventType,
    MatchEvent,
    PassEvent,
    ShotEvent,
    ShotOutcome,
    TackleEvent,
    PressureEvent,
    CarryEvent,
    InterceptionEvent,
    RecoveryEvent,
    CrossEvent,
    FoulEvent,
    PossessionChangeEvent,
    GoalEvent,
)
from simulator.engine.match_engine import HALF_DURATION_S, HALF_TIME_BREAK_S

from simulator.intelligence.models import (
    MatchIntelligence,
    MatchSummaryIntel,
    TeamIntelligence,
    PlayerIntelligence,
    PossessionStats,
    PassingStats,
    AttackingStats,
    DefensiveStats,
    TransitionStats,
    PossessionSequenceAnalysis,
    PossessionIntelligence,
    PossessionOutcome,
    PressureIntelligence,
    ShotIntelligence,
    TerritorialIntelligence,
    RollingWindowMetrics,
    MatchStateClassification,
    MatchStateName,
    StateTransition,
    SignificantMoment,
    SignificanceTier,
    MomentType,
    Evidence,
    EvidenceType,
    ExplanationContext,
    TeamComparison,
    TimelineEntry,
)

# ── Constants ─────────────────────────────────────────────────────────────

SECOND_HALF_START_S = HALF_DURATION_S + HALF_TIME_BREAK_S
LONG_PASS_THRESHOLD_M = 25.0
WINDOW_SIZES = [60, 180, 300, 600]  # 1m, 3m, 5m, 10m
STATE_CHECK_INTERVAL_S = 60  # classify state every minute


# ── Helpers ───────────────────────────────────────────────────────────────

def _is_first_half(ts: float) -> bool:
    return ts < SECOND_HALF_START_S


def _attacking_right(team_id: str, home_team_id: str, ts: float) -> bool:
    is_home = team_id == home_team_id
    first = _is_first_half(ts)
    return (is_home and first) or (not is_home and not first)


def _progression_distance(
    start_x: float, end_x: float, team_id: str, home_team_id: str, ts: float,
) -> float:
    """Positive = toward the opponent's goal."""
    if _attacking_right(team_id, home_team_id, ts):
        return end_x - start_x
    else:
        return start_x - end_x


def _event_third(x: float, team_id: str, home_team_id: str, ts: float) -> PitchThird:
    ar = _attacking_right(team_id, home_team_id, ts)
    return classify_third(x, attacking_right=ar)


def _safe_div(num: float, den: float, default: float = 0.0) -> float:
    return round(num / den, 4) if den > 0 else default


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, round(v, 2)))


# ── Intelligence engine ──────────────────────────────────────────────────

class IntelligenceEngine:
    """Deterministic analytics engine for a single match."""

    def __init__(self, match: Match, events: list[MatchEvent]) -> None:
        self.match = match
        self.events = events
        self.home_id = match.home_team.team_id
        self.away_id = match.away_team.team_id

        # Build player lookup
        self._players: dict[str, Any] = {}
        for p in match.home_team.players + match.away_team.players:
            self._players[p.player_id] = p

        # Evidence counter
        self._evi_counter = 0
        self._evidence: list[Evidence] = []

    # ── public API ────────────────────────────────────────────────────────

    def analyze(self) -> MatchIntelligence:
        """Run full analysis and return the intelligence object."""
        # 1. Classify typed events
        typed = self._classify_events()

        # 2. Build possession sequences
        sequences = self._build_possession_sequences(typed)

        # 3. Match summary
        summary = self._build_summary(typed)

        # 4. Team stats
        home_stats = self._build_team_stats(self.home_id, typed, sequences)
        away_stats = self._build_team_stats(self.away_id, typed, sequences)

        # 5. Player stats
        players = self._build_player_stats(typed)

        return self.assemble(typed, sequences, summary, home_stats, away_stats, players)

    def assemble(self, typed, sequences, summary, home_stats, away_stats, players,
                 windows=None) -> MatchIntelligence:
        """Compose deterministic projections, also used by the streaming adapter.

        Callers supply updated component projections and optionally already
        published windows. All formulas remain in these shared methods.
        """
        self._evidence = []
        self._evi_counter = 0
        # 6. Possession intelligence
        poss_intel = self._build_possession_intelligence(sequences)

        # 7–8. Shared team indexes, also refreshed by the streaming adapter.
        self.update_team_indexes(typed, summary.match_duration_s, home_stats, away_stats)

        # 9. Rolling windows
        if windows is None:
            windows = self._build_rolling_windows(typed, summary.match_duration_s)

        # 10. State classification
        states = self._classify_states(windows)

        # 11. State transitions
        transitions = self._detect_transitions(states)

        # 12. Significant moments
        moments = self._detect_moments(typed, states, transitions, windows)

        # 13. Explanation contexts
        explanations = self._build_explanation_contexts(moments)

        # 14. Team comparison
        comparison = TeamComparison(
            home_team_id=self.home_id,
            away_team_id=self.away_id,
            possession_home=home_stats.possession.possession_pct,
            possession_away=away_stats.possession.possession_pct,
            pressure_index_home=home_stats.pressure_index,
            pressure_index_away=away_stats.pressure_index,
            field_tilt_home=home_stats.field_tilt_pct,
            field_tilt_away=away_stats.field_tilt_pct,
            pass_accuracy_home=home_stats.passing.accuracy,
            pass_accuracy_away=away_stats.passing.accuracy,
            progressive_passes_home=home_stats.passing.progressive,
            progressive_passes_away=away_stats.passing.progressive,
            shots_home=home_stats.attacking.shots,
            shots_away=away_stats.attacking.shots,
            shots_on_target_home=home_stats.attacking.shots_on_target,
            shots_on_target_away=away_stats.attacking.shots_on_target,
            goals_home=home_stats.attacking.goals,
            goals_away=away_stats.attacking.goals,
            avg_poss_duration_home=home_stats.possession.avg_sequence_duration_s,
            avg_poss_duration_away=away_stats.possession.avg_sequence_duration_s,
        )

        # 15. Timeline
        timeline = self._build_timeline(typed, moments, states)

        return MatchIntelligence(
            match_id=self.match.match_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
            summary=summary,
            home_team=home_stats,
            away_team=away_stats,
            players=players,
            possession_sequences=sequences,
            possession_intelligence=poss_intel,
            rolling_windows=windows,
            tactical_states=states,
            state_transitions=transitions,
            detected_moments=moments,
            evidence=self._evidence,
            explanation_contexts=explanations,
            team_comparison=comparison,
            timeline=timeline,
        )

    def update_team_indexes(self, typed, duration, home_stats, away_stats):
        """Refresh the original pressure and territorial formulas in one place."""
        home_pi = self._calc_pressure_index(self.home_id, typed, duration)
        away_pi = self._calc_pressure_index(self.away_id, typed, duration)
        home_stats.pressure_index = home_pi.pressure_index
        away_stats.pressure_index = away_pi.pressure_index

        # 8. Territorial / field tilt
        home_terr = self._calc_territorial(self.home_id, typed)
        away_terr = self._calc_territorial(self.away_id, typed)
        h_att = home_terr.attacking_third_actions
        a_att = away_terr.attacking_third_actions
        total_att = h_att + a_att
        home_stats.field_tilt_pct = _clamp(_safe_div(h_att, total_att, 0.5) * 100)
        away_stats.field_tilt_pct = _clamp(_safe_div(a_att, total_att, 0.5) * 100)

    # ── event classification ──────────────────────────────────────────────

    def _classify_events(self) -> dict[str, list]:
        """Group events by type for efficient access."""
        typed: dict[str, list] = defaultdict(list)
        for ev in self.events:
            typed["all"].append(ev)
            typed[ev.event_type.value].append(ev)
        return typed

    # ── possession sequences ──────────────────────────────────────────────

    def _build_possession_sequences(self, typed: dict[str, list]) -> list[PossessionSequenceAnalysis]:
        """Reconstruct possession sequences from sequence IDs."""
        seq_events: dict[str, list[MatchEvent]] = defaultdict(list)
        seq_team: dict[str, str] = {}

        for ev in typed["all"]:
            sid = getattr(ev, "possession_sequence_id", None)
            if sid:
                seq_events[sid].append(ev)
                if ev.team_id and sid not in seq_team:
                    seq_team[sid] = ev.team_id

        sequences: list[PossessionSequenceAnalysis] = []
        for sid, evts in seq_events.items():
            if not evts:
                continue
            team_id = seq_team.get(sid, "")
            start_ts = evts[0].timestamp
            end_ts = evts[-1].timestamp
            dur = end_ts - start_ts

            passes = sum(1 for e in evts if e.event_type == EventType.PASS)
            prog = sum(1 for e in evts if (
                e.event_type in (EventType.PASS, EventType.CARRY)
                and hasattr(e, "progressive") and getattr(e, "progressive", False)
            ))
            # Also count carries with positive progression
            for e in evts:
                if isinstance(e, CarryEvent) and e.start_x is not None:
                    pd = _progression_distance(
                        e.start_x, e.end_x, team_id, self.home_id, e.timestamp,
                    )
                    if pd > 5.0:
                        prog += 1

            pressure_received = sum(1 for e in evts if e.event_type == EventType.PRESSURE)
            shot = any(e.event_type == EventType.SHOT for e in evts)
            goal = any(e.event_type == EventType.GOAL for e in evts)

            # Determine outcome
            if goal:
                outcome = PossessionOutcome.GOAL
            elif shot:
                outcome = PossessionOutcome.SHOT
            else:
                # Check if sequence ended with a turnover
                last = evts[-1]
                if last.event_type in (EventType.HALF_TIME, EventType.MATCH_END):
                    outcome = PossessionOutcome.HALF_TIME if last.event_type == EventType.HALF_TIME else PossessionOutcome.MATCH_END
                elif last.event_type == EventType.FOUL:
                    outcome = PossessionOutcome.FOUL_WON
                else:
                    outcome = PossessionOutcome.TURNOVER

            # Zones
            first_ev = evts[0]
            last_ev = evts[-1]
            s_zone = "unknown"
            e_zone = "unknown"
            if first_ev.start_x is not None and team_id:
                t = _event_third(first_ev.start_x, team_id, self.home_id, first_ev.timestamp)
                s_zone = t.value
            if last_ev.start_x is not None and team_id:
                t = _event_third(last_ev.start_x, team_id, self.home_id, last_ev.timestamp)
                e_zone = t.value

            sequences.append(PossessionSequenceAnalysis(
                sequence_id=sid,
                team_id=team_id,
                start_time=start_ts,
                end_time=end_ts,
                duration_s=round(dur, 2),
                starting_zone=s_zone,
                ending_zone=e_zone,
                num_events=len(evts),
                passes=passes,
                progressive_actions=prog,
                pressure_received=pressure_received,
                shot_generated=shot or goal,
                outcome=outcome,
            ))

        sequences.sort(key=lambda s: s.start_time)
        return sequences

    # ── match summary ─────────────────────────────────────────────────────

    def _build_summary(self, typed: dict[str, list]) -> MatchSummaryIntel:
        all_ev = typed["all"]
        passes = typed.get("PASS", [])
        succ = sum(1 for p in passes if isinstance(p, PassEvent) and p.successful)

        # Match duration from last event
        match_dur = all_ev[-1].timestamp if all_ev else 0.0

        return MatchSummaryIntel(
            total_events=len(all_ev),
            total_passes=len(passes),
            successful_passes=succ,
            unsuccessful_passes=len(passes) - succ,
            pass_accuracy=round(_safe_div(succ, len(passes)) * 100, 1),
            total_shots=len(typed.get("SHOT", [])),
            shots_on_target=sum(
                1 for s in typed.get("SHOT", [])
                if isinstance(s, ShotEvent) and s.outcome in (ShotOutcome.GOAL, ShotOutcome.SAVED)
            ),
            goals=len(typed.get("GOAL", [])),
            tackles=len(typed.get("TACKLE", [])),
            pressure_events=len(typed.get("PRESSURE", [])),
            possession_changes=len(typed.get("POSSESSION_CHANGE", [])),
            fouls=len(typed.get("FOUL", [])),
            carries=len(typed.get("CARRY", [])),
            crosses=len(typed.get("CROSS", [])),
            interceptions=len(typed.get("INTERCEPTION", [])),
            recoveries=len(typed.get("RECOVERY", [])),
            match_duration_s=match_dur,
        )

    # ── team stats ────────────────────────────────────────────────────────

    def _build_team_stats(
        self, team_id: str, typed: dict[str, list],
        sequences: list[PossessionSequenceAnalysis],
    ) -> TeamIntelligence:
        team = self.match.home_team if team_id == self.home_id else self.match.away_team

        # Possession
        team_seqs = [s for s in sequences if s.team_id == team_id]
        total_dur = sum(s.duration_s for s in sequences)
        team_dur = sum(s.duration_s for s in team_seqs)
        poss_pct = _clamp(_safe_div(team_dur, total_dur, 0.5) * 100)
        durations = [s.duration_s for s in team_seqs]
        longest = max(durations) if durations else 0.0

        poss = PossessionStats(
            possession_pct=poss_pct,
            possession_duration_s=round(team_dur, 2),
            sequences=len(team_seqs),
            avg_sequence_duration_s=round(_safe_div(team_dur, len(team_seqs)), 2),
            longest_sequence_duration_s=round(longest, 2),
        )

        # Passing
        team_passes = [p for p in typed.get("PASS", []) if p.team_id == team_id]
        succ_passes = [p for p in team_passes if isinstance(p, PassEvent) and p.successful]
        prog_passes = [p for p in team_passes if isinstance(p, PassEvent) and p.progressive]
        pressured = [p for p in team_passes if isinstance(p, PassEvent) and p.pressure_level > 0.3]
        pressured_succ = [p for p in pressured if p.successful]
        long_p = [p for p in team_passes if isinstance(p, PassEvent) and p.distance_meters >= LONG_PASS_THRESHOLD_M]
        long_succ = [p for p in long_p if p.successful]

        total_dist = sum(p.distance_meters for p in team_passes if isinstance(p, PassEvent))

        # Passes by zone
        by_zone: dict[str, int] = defaultdict(int)
        into_att = 0
        for p in team_passes:
            if isinstance(p, PassEvent) and p.start_x is not None:
                t = _event_third(p.start_x, team_id, self.home_id, p.timestamp)
                by_zone[t.value] += 1
                # Check if pass ends in attacking third
                if p.end_x is not None:
                    end_t = _event_third(p.end_x, team_id, self.home_id, p.timestamp)
                    if end_t == PitchThird.ATTACKING and t != PitchThird.ATTACKING:
                        into_att += 1

        passing = PassingStats(
            attempts=len(team_passes),
            successful=len(succ_passes),
            unsuccessful=len(team_passes) - len(succ_passes),
            accuracy=round(_safe_div(len(succ_passes), len(team_passes)) * 100, 1),
            avg_distance_m=round(_safe_div(total_dist, len(team_passes)), 1),
            progressive=len(prog_passes),
            progressive_pct=round(_safe_div(len(prog_passes), len(team_passes)) * 100, 1),
            under_pressure=len(pressured),
            under_pressure_success=len(pressured_succ),
            pressured_accuracy=round(_safe_div(len(pressured_succ), len(pressured)) * 100, 1),
            by_zone=dict(by_zone),
            into_attacking_third=into_att,
            long_passes=len(long_p),
            long_pass_success=len(long_succ),
            long_pass_accuracy=round(_safe_div(len(long_succ), len(long_p)) * 100, 1),
        )

        # Attacking
        team_shots = [s for s in typed.get("SHOT", []) if s.team_id == team_id]
        on_target = [s for s in team_shots if isinstance(s, ShotEvent) and s.outcome in (ShotOutcome.GOAL, ShotOutcome.SAVED)]
        goals = [s for s in team_shots if isinstance(s, ShotEvent) and s.outcome == ShotOutcome.GOAL]
        shot_dists = [s.shot_distance_meters for s in team_shots if isinstance(s, ShotEvent)]
        shot_speeds = [s.shot_speed_kph for s in team_shots if isinstance(s, ShotEvent) and s.shot_speed_kph]
        shot_quals = [s.shot_quality for s in team_shots if isinstance(s, ShotEvent)]

        # Progressive actions = progressive passes + progressive carries
        team_carries = [c for c in typed.get("CARRY", []) if c.team_id == team_id]
        prog_carries = 0
        for c in team_carries:
            if isinstance(c, CarryEvent) and c.start_x is not None:
                pd = _progression_distance(c.start_x, c.end_x, team_id, self.home_id, c.timestamp)
                if pd > 5.0:
                    prog_carries += 1

        # Attacking-third entries from passes
        att_entries = into_att
        for c in team_carries:
            if isinstance(c, CarryEvent) and c.start_x is not None:
                s_t = _event_third(c.start_x, team_id, self.home_id, c.timestamp)
                e_t = _event_third(c.end_x, team_id, self.home_id, c.timestamp)
                if e_t == PitchThird.ATTACKING and s_t != PitchThird.ATTACKING:
                    att_entries += 1

        attacking = AttackingStats(
            shots=len(team_shots),
            shots_on_target=len(on_target),
            goals=len(goals),
            avg_shot_distance_m=round(_safe_div(sum(shot_dists), len(shot_dists)), 1),
            avg_shot_speed_kph=round(_safe_div(sum(shot_speeds), len(shot_speeds)), 1),
            avg_shot_quality=round(_safe_div(sum(shot_quals), len(shot_quals)), 4),
            progressive_actions=len(prog_passes) + prog_carries,
            attacking_third_entries=att_entries,
        )

        # Defensive
        team_tackles = [t for t in typed.get("TACKLE", []) if t.team_id == team_id]
        succ_tackles = [t for t in team_tackles if isinstance(t, TackleEvent) and t.successful]
        team_pressure = [p for p in typed.get("PRESSURE", []) if isinstance(p, PressureEvent) and p.pressing_team_id == team_id]
        succ_pressure = [p for p in team_pressure if p.successful]
        team_intercept = [i for i in typed.get("INTERCEPTION", []) if i.team_id == team_id]
        team_recovery = [r for r in typed.get("RECOVERY", []) if r.team_id == team_id]

        # Final-third recoveries
        ft_recoveries = 0
        for r in team_recovery:
            if r.start_x is not None:
                t = _event_third(r.start_x, team_id, self.home_id, r.timestamp)
                if t == PitchThird.ATTACKING:
                    ft_recoveries += 1

        defensive = DefensiveStats(
            tackles=len(team_tackles),
            successful_tackles=len(succ_tackles),
            pressure_events=len(team_pressure),
            successful_pressures=len(succ_pressure),
            interceptions=len(team_intercept),
            recoveries=len(team_recovery),
            final_third_recoveries=ft_recoveries,
        )

        # Transitions
        poss_won = sum(1 for pc in typed.get("POSSESSION_CHANGE", [])
                       if isinstance(pc, PossessionChangeEvent) and pc.to_team_id == team_id)
        poss_lost = sum(1 for pc in typed.get("POSSESSION_CHANGE", [])
                        if isinstance(pc, PossessionChangeEvent) and pc.from_team_id == team_id)
        # Transition shots: shots within 10s of gaining possession
        trans_shots = 0
        poss_gain_times = [pc.timestamp for pc in typed.get("POSSESSION_CHANGE", [])
                           if isinstance(pc, PossessionChangeEvent) and pc.to_team_id == team_id]
        for s in team_shots:
            for gt in poss_gain_times:
                if 0 < s.timestamp - gt < 10:
                    trans_shots += 1
                    break

        trans = TransitionStats(
            possession_won=poss_won,
            possession_lost=poss_lost,
            transition_shots=trans_shots,
        )

        return TeamIntelligence(
            team_id=team_id,
            team_name=team.name,
            possession=poss,
            passing=passing,
            attacking=attacking,
            defensive=defensive,
            transitions=trans,
        )

    # ── player stats ──────────────────────────────────────────────────────

    def _build_player_stats(self, typed: dict[str, list]) -> list[PlayerIntelligence]:
        stats: dict[str, dict[str, Any]] = {}
        for pid, p in self._players.items():
            stats[pid] = {
                "player_id": pid, "team_id": p.team_id, "name": p.name,
                "position": p.position.value,
                "passes": 0, "completed": 0, "progressive_passes": 0,
                "total_pass_dist": 0.0,
                "shots": 0, "sot": 0, "goals": 0,
                "tackles": 0, "succ_tackles": 0,
                "pressure": 0, "succ_pressure": 0,
                "interceptions": 0, "recoveries": 0,
                "carries": 0, "touches": 0, "crosses": 0, "fouls": 0,
                "xs": [], "ys": [],
            }

        for ev in typed["all"]:
            pid = ev.player_id
            if pid and pid in stats:
                stats[pid]["touches"] += 1
                if ev.start_x is not None:
                    stats[pid]["xs"].append(ev.start_x)
                    stats[pid]["ys"].append(ev.start_y)

        for p in typed.get("PASS", []):
            pid = p.player_id
            if pid and pid in stats:
                stats[pid]["passes"] += 1
                if isinstance(p, PassEvent):
                    if p.successful:
                        stats[pid]["completed"] += 1
                    if p.progressive:
                        stats[pid]["progressive_passes"] += 1
                    stats[pid]["total_pass_dist"] += p.distance_meters

        for s in typed.get("SHOT", []):
            pid = s.player_id
            if pid and pid in stats:
                stats[pid]["shots"] += 1
                if isinstance(s, ShotEvent):
                    if s.outcome in (ShotOutcome.GOAL, ShotOutcome.SAVED):
                        stats[pid]["sot"] += 1
                    if s.outcome == ShotOutcome.GOAL:
                        stats[pid]["goals"] += 1

        for t in typed.get("TACKLE", []):
            pid = t.player_id
            if pid and pid in stats:
                stats[pid]["tackles"] += 1
                if isinstance(t, TackleEvent) and t.successful:
                    stats[pid]["succ_tackles"] += 1

        for pr in typed.get("PRESSURE", []):
            pid = pr.player_id
            if pid and pid in stats:
                stats[pid]["pressure"] += 1
                if isinstance(pr, PressureEvent) and pr.successful:
                    stats[pid]["succ_pressure"] += 1

        for i in typed.get("INTERCEPTION", []):
            pid = i.player_id
            if pid and pid in stats:
                stats[pid]["interceptions"] += 1

        for r in typed.get("RECOVERY", []):
            pid = r.player_id
            if pid and pid in stats:
                stats[pid]["recoveries"] += 1

        for c in typed.get("CARRY", []):
            pid = c.player_id
            if pid and pid in stats:
                stats[pid]["carries"] += 1

        for cr in typed.get("CROSS", []):
            pid = cr.player_id
            if pid and pid in stats:
                stats[pid]["crosses"] += 1

        for f in typed.get("FOUL", []):
            pid = f.player_id
            if pid and pid in stats:
                stats[pid]["fouls"] += 1

        result = []
        for pid, s in stats.items():
            result.append(PlayerIntelligence(
                player_id=pid,
                team_id=s["team_id"],
                name=s["name"],
                position=s["position"],
                passes=s["passes"],
                completed_passes=s["completed"],
                pass_accuracy=round(_safe_div(s["completed"], s["passes"]) * 100, 1),
                progressive_passes=s["progressive_passes"],
                avg_pass_distance_m=round(_safe_div(s["total_pass_dist"], s["passes"]), 1),
                shots=s["shots"],
                shots_on_target=s["sot"],
                goals=s["goals"],
                tackles=s["tackles"],
                successful_tackles=s["succ_tackles"],
                pressure_events=s["pressure"],
                successful_pressures=s["succ_pressure"],
                interceptions=s["interceptions"],
                recoveries=s["recoveries"],
                carries=s["carries"],
                touches=s["touches"],
                crosses=s["crosses"],
                fouls_committed=s["fouls"],
                avg_x=round(statistics.mean(s["xs"]), 1) if s["xs"] else None,
                avg_y=round(statistics.mean(s["ys"]), 1) if s["ys"] else None,
            ))
        result.sort(key=lambda p: (p.team_id, p.player_id))
        return result

    # ── possession intelligence ───────────────────────────────────────────

    def _build_possession_intelligence(
        self, sequences: list[PossessionSequenceAnalysis],
    ) -> PossessionIntelligence:
        if not sequences:
            return PossessionIntelligence()
        durs = [s.duration_s for s in sequences]
        return PossessionIntelligence(
            total_sequences=len(sequences),
            avg_duration_s=round(statistics.mean(durs), 2),
            median_duration_s=round(statistics.median(durs), 2),
            longest_duration_s=round(max(durs), 2),
            sequences_ending_shot=sum(1 for s in sequences if s.outcome == PossessionOutcome.SHOT),
            sequences_ending_goal=sum(1 for s in sequences if s.outcome == PossessionOutcome.GOAL),
            sequences_ending_turnover=sum(1 for s in sequences if s.outcome == PossessionOutcome.TURNOVER),
            sequences_with_progression=sum(1 for s in sequences if s.progressive_actions > 0),
            sequences_reaching_attacking_third=sum(
                1 for s in sequences if s.ending_zone == "attacking"
            ),
        )

    # ── pressure index ────────────────────────────────────────────────────

    def _calc_pressure_index(
        self, team_id: str, typed: dict[str, list], match_dur_s: float,
    ) -> PressureIntelligence:
        presses = [p for p in typed.get("PRESSURE", [])
                    if isinstance(p, PressureEvent) and p.pressing_team_id == team_id]
        total = len(presses)
        succ = sum(1 for p in presses if p.successful)
        match_min = match_dur_s / 60.0 if match_dur_s > 0 else 1.0

        # Attacking-third pressure
        att_third = 0
        for p in presses:
            if p.start_x is not None:
                # Pressure in opponent's defensive third = our attacking third
                opp_id = self.away_id if team_id == self.home_id else self.home_id
                t = _event_third(p.start_x, opp_id, self.home_id, p.timestamp)
                if t == PitchThird.DEFENSIVE:
                    att_third += 1

        # Turnovers after pressure (possession changes within 3s of successful pressure)
        turnovers_after = 0
        poss_changes = typed.get("POSSESSION_CHANGE", [])
        for p in presses:
            if p.successful:
                for pc in poss_changes:
                    if isinstance(pc, PossessionChangeEvent):
                        if pc.to_team_id == team_id and 0 <= pc.timestamp - p.timestamp <= 3:
                            turnovers_after += 1
                            break

        epm = total / match_min
        succ_rate = _safe_div(succ, total)
        att_pct = _safe_div(att_third, total)
        turn_rate = _safe_div(turnovers_after, total)

        # Normalize each component to 0–25 range
        n_vol = min(25.0, epm / 3.0 * 25)  # ~3 per min = max
        n_succ = succ_rate * 25
        n_att = att_pct * 25
        n_turn = min(25.0, turn_rate * 2.5 * 25)  # scale up

        idx = _clamp(n_vol + n_succ + n_att + n_turn)

        return PressureIntelligence(
            pressure_index=idx,
            events_per_minute=round(epm, 2),
            success_rate=round(succ_rate * 100, 1),
            attacking_third_pressure=att_third,
            attacking_third_pressure_pct=round(att_pct * 100, 1),
            turnovers_after_pressure=turnovers_after,
            total_events=total,
        )

    # ── territorial ───────────────────────────────────────────────────────

    def _calc_territorial(self, team_id: str, typed: dict[str, list]) -> TerritorialIntelligence:
        att = mid = dfe = 0
        total_prog = 0.0
        prog_count = 0
        prog_into_att = 0

        for ev in typed["all"]:
            if ev.team_id != team_id or ev.start_x is None:
                continue
            if ev.event_type in (EventType.MATCH_START, EventType.HALF_TIME,
                                 EventType.SECOND_HALF_START, EventType.MATCH_END):
                continue

            t = _event_third(ev.start_x, team_id, self.home_id, ev.timestamp)
            if t == PitchThird.ATTACKING:
                att += 1
            elif t == PitchThird.MIDDLE:
                mid += 1
            else:
                dfe += 1

            # Progression for pass/carry events with end coordinates
            end_x = getattr(ev, "end_x", None)
            if end_x is not None and ev.event_type in (EventType.PASS, EventType.CARRY):
                pd = _progression_distance(ev.start_x, end_x, team_id, self.home_id, ev.timestamp)
                if pd > 0:
                    total_prog += pd
                    prog_count += 1
                    end_t = _event_third(end_x, team_id, self.home_id, ev.timestamp)
                    start_t = t
                    if end_t == PitchThird.ATTACKING and start_t != PitchThird.ATTACKING:
                        prog_into_att += 1

        total_actions = att + mid + dfe
        return TerritorialIntelligence(
            field_tilt_pct=round(_safe_div(att, total_actions, 0.5) * 100, 1),
            attacking_third_actions=att,
            middle_third_actions=mid,
            defensive_third_actions=dfe,
            total_progressive_distance_m=round(total_prog, 1),
            avg_progressive_distance_m=round(_safe_div(total_prog, prog_count), 1),
            progressive_action_count=prog_count,
            progression_into_att_third=prog_into_att,
        )

    # ── rolling windows ──────────────────────────────────────────────────

    def _build_rolling_windows(
        self, typed: dict[str, list], match_dur_s: float,
    ) -> list[RollingWindowMetrics]:
        windows: list[RollingWindowMetrics] = []
        all_ev = typed["all"]

        # Compute at every 5-minute mark for the primary (300s) window,
        # and at the end of the match for all window sizes.
        checkpoints = list(range(300, int(match_dur_s) + 1, 300))
        if checkpoints and checkpoints[-1] < match_dur_s:
            checkpoints.append(int(match_dur_s))

        for cp in checkpoints:
            for ws in WINDOW_SIZES:
                wm = self._calc_window(all_ev, float(cp), ws)
                windows.append(wm)

        return windows

    def _calc_window(
        self, all_events: list[MatchEvent], ts: float, window_s: int,
    ) -> RollingWindowMetrics:
        start = max(0, ts - window_s)
        evts = [e for e in all_events if start <= e.timestamp <= ts]

        h_poss = h_press = a_press = 0
        h_prog = a_prog = h_shots = a_shots = 0
        h_pass = a_pass = h_succ = a_succ = 0
        turnovers = 0
        h_att_actions = a_att_actions = 0

        # Count possession-holding events per team
        h_poss_events = 0
        a_poss_events = 0

        for e in evts:
            tid = e.team_id
            if tid == self.home_id:
                h_poss_events += 1
            elif tid == self.away_id:
                a_poss_events += 1

            if e.event_type == EventType.PRESSURE and isinstance(e, PressureEvent):
                if e.pressing_team_id == self.home_id:
                    h_press += 1
                else:
                    a_press += 1
            elif e.event_type == EventType.PASS and isinstance(e, PassEvent):
                if tid == self.home_id:
                    h_pass += 1
                    if e.successful:
                        h_succ += 1
                    if e.progressive:
                        h_prog += 1
                elif tid == self.away_id:
                    a_pass += 1
                    if e.successful:
                        a_succ += 1
                    if e.progressive:
                        a_prog += 1
            elif e.event_type == EventType.SHOT:
                if tid == self.home_id:
                    h_shots += 1
                else:
                    a_shots += 1
            elif e.event_type == EventType.POSSESSION_CHANGE:
                turnovers += 1
            elif e.event_type == EventType.CARRY and isinstance(e, CarryEvent):
                if e.start_x is not None and tid:
                    pd = _progression_distance(e.start_x, e.end_x, tid, self.home_id, e.timestamp)
                    if pd > 5:
                        if tid == self.home_id:
                            h_prog += 1
                        else:
                            a_prog += 1

            # Field tilt (attacking third actions)
            if e.start_x is not None and tid and e.event_type not in (
                EventType.MATCH_START, EventType.HALF_TIME,
                EventType.SECOND_HALF_START, EventType.MATCH_END,
            ):
                t = _event_third(e.start_x, tid, self.home_id, e.timestamp)
                if t == PitchThird.ATTACKING:
                    if tid == self.home_id:
                        h_att_actions += 1
                    else:
                        a_att_actions += 1

        total_poss = h_poss_events + a_poss_events
        h_poss_pct = _safe_div(h_poss_events, total_poss, 0.5) * 100
        a_poss_pct = 100.0 - h_poss_pct

        total_att = h_att_actions + a_att_actions
        h_tilt = _safe_div(h_att_actions, total_att, 0.5) * 100
        a_tilt = 100.0 - h_tilt

        dur_min = window_s / 60.0
        density = len(evts) / dur_min if dur_min > 0 else 0.0

        # Simple pressure index for window
        h_pi = _clamp(min(h_press / dur_min / 3.0 * 50, 50) + (_safe_div(h_press, h_press + a_press, 0.5)) * 50) if dur_min > 0 else 50.0
        a_pi = _clamp(min(a_press / dur_min / 3.0 * 50, 50) + (_safe_div(a_press, h_press + a_press, 0.5)) * 50) if dur_min > 0 else 50.0

        return RollingWindowMetrics(
            timestamp=round(ts, 2),
            window_seconds=window_s,
            home_possession_pct=round(h_poss_pct, 1),
            away_possession_pct=round(a_poss_pct, 1),
            home_pressure_count=h_press,
            away_pressure_count=a_press,
            home_pressure_index=round(h_pi, 1),
            away_pressure_index=round(a_pi, 1),
            home_field_tilt=round(h_tilt, 1),
            away_field_tilt=round(a_tilt, 1),
            home_progressive_actions=h_prog,
            away_progressive_actions=a_prog,
            home_shots=h_shots,
            away_shots=a_shots,
            home_passes=h_pass,
            away_passes=a_pass,
            home_pass_success=h_succ,
            away_pass_success=a_succ,
            turnovers=turnovers,
            event_density=round(density, 1),
        )

    # ── state classification ──────────────────────────────────────────────

    def _classify_states(
        self, windows: list[RollingWindowMetrics],
    ) -> list[MatchStateClassification]:
        """Classify tactical state using 5-minute window at each checkpoint."""
        states: list[MatchStateClassification] = []
        w5 = [w for w in windows if w.window_seconds == 300]
        w5.sort(key=lambda w: w.timestamp)

        for w in w5:
            state, conf, tid = self._classify_single(w)
            evi = self._make_evidence(
                metric="state_classification",
                value=conf,
                team_id=tid,
                timestamp=w.timestamp,
                etype=EvidenceType.STATE_CHANGE,
            )
            states.append(MatchStateClassification(
                state=state,
                confidence=round(conf, 2),
                timestamp=w.timestamp,
                minute=int(w.timestamp // 60),
                team_id=tid,
                evidence_ids=[evi.evidence_id],
            ))
        return states

    def _classify_single(self, w: RollingWindowMetrics) -> tuple[MatchStateName, float, str | None]:
        """Transparent threshold-based state classification."""
        poss_diff = w.home_possession_pct - w.away_possession_pct
        press_diff = w.home_pressure_index - w.away_pressure_index
        tilt_diff = w.home_field_tilt - w.away_field_tilt

        # Check for chaotic phase (high turnover + high event density)
        if w.turnovers >= 8 and w.event_density > 25:
            return MatchStateName.CHAOTIC_PHASE, 0.65, None

        # High pressure (one team pressing heavily)
        if abs(press_diff) > 20:
            tid = self.home_id if press_diff > 0 else self.away_id
            return MatchStateName.HIGH_PRESSURE, min(0.95, abs(press_diff) / 40), tid

        # One team in control (possession + field tilt dominance)
        control_score_h = poss_diff * 0.4 + tilt_diff * 0.4 + press_diff * 0.2
        control_score_a = -control_score_h

        if control_score_h > 12:
            return MatchStateName.HOME_CONTROL, min(0.95, control_score_h / 25), self.home_id
        if control_score_a > 12:
            return MatchStateName.AWAY_CONTROL, min(0.95, control_score_a / 25), self.away_id

        # Attacking phase (lots of shots)
        if w.home_shots + w.away_shots >= 3:
            tid = self.home_id if w.home_shots > w.away_shots else self.away_id
            return MatchStateName.ATTACKING_PHASE, 0.6, tid

        # Transition phase (many turnovers)
        if w.turnovers >= 6:
            return MatchStateName.TRANSITION_PHASE, 0.55, None

        return MatchStateName.BALANCED, 0.5, None

    # ── state transitions ─────────────────────────────────────────────────

    def _detect_transitions(
        self, states: list[MatchStateClassification],
    ) -> list[StateTransition]:
        transitions: list[StateTransition] = []
        for i in range(1, len(states)):
            prev = states[i - 1]
            curr = states[i]
            if prev.state != curr.state:
                mag = abs(curr.confidence - prev.confidence)
                summaries = [
                    f"State changed from {prev.state.value} to {curr.state.value}",
                ]
                if curr.team_id:
                    summaries.append(f"Driven by {curr.team_id}")

                evi = self._make_evidence(
                    metric="state_transition",
                    value=curr.confidence,
                    baseline=prev.confidence,
                    timestamp=curr.timestamp,
                    etype=EvidenceType.STATE_CHANGE,
                )

                transitions.append(StateTransition(
                    transition_id=f"trans_{i:04d}",
                    timestamp=curr.timestamp,
                    minute=curr.minute,
                    previous_state=prev.state,
                    new_state=curr.state,
                    magnitude=round(min(1.0, mag + 0.3), 2),
                    evidence_ids=[evi.evidence_id] + curr.evidence_ids,
                    evidence_summaries=summaries,
                ))
        return transitions

    # ── significant moments ───────────────────────────────────────────────

    def _detect_moments(
        self,
        typed: dict[str, list],
        states: list[MatchStateClassification],
        transitions: list[StateTransition],
        windows: list[RollingWindowMetrics],
    ) -> list[SignificantMoment]:
        moments: list[SignificantMoment] = []
        moment_counter = 0

        def _mid() -> str:
            nonlocal moment_counter
            moment_counter += 1
            return f"mom_{moment_counter:04d}"

        # Goals
        for g in typed.get("GOAL", []):
            if isinstance(g, GoalEvent):
                evi = self._make_evidence("goal", 100, timestamp=g.timestamp, team_id=g.team_id,
                                          etype=EvidenceType.THRESHOLD, event_ids=[g.event_id])
                moments.append(SignificantMoment(
                    moment_id=_mid(), timestamp=g.timestamp, minute=g.minute,
                    moment_type=MomentType.GOAL, significance_score=100,
                    tier=SignificanceTier.CRITICAL,
                    team_id=g.team_id, player_id=g.scorer_player_id,
                    reason_codes=["goal_scored", f"score_{g.home_score}_{g.away_score}"],
                    evidence_ids=[evi.evidence_id],
                    source_event_ids=[g.event_id],
                ))

        # Shots on target (non-goal)
        for s in typed.get("SHOT", []):
            if isinstance(s, ShotEvent) and s.outcome == ShotOutcome.SAVED:
                score = _clamp(50 + s.shot_quality * 30)
                evi = self._make_evidence("shot_on_target", score, timestamp=s.timestamp,
                                          team_id=s.team_id, etype=EvidenceType.METRIC,
                                          event_ids=[s.event_id])
                moments.append(SignificantMoment(
                    moment_id=_mid(), timestamp=s.timestamp, minute=s.minute,
                    moment_type=MomentType.SHOT_ON_TARGET, significance_score=score,
                    tier=SignificanceTier.HIGH if score >= 65 else SignificanceTier.NOTABLE,
                    team_id=s.team_id, player_id=s.player_id,
                    reason_codes=["shot_on_target", f"quality_{s.shot_quality:.2f}"],
                    evidence_ids=[evi.evidence_id],
                    source_event_ids=[s.event_id],
                ))

        # State transitions
        for t in transitions:
            score = _clamp(35 + t.magnitude * 50)
            tier = SignificanceTier.HIGH if score >= 65 else SignificanceTier.NOTABLE
            moments.append(SignificantMoment(
                moment_id=_mid(), timestamp=t.timestamp, minute=t.minute,
                moment_type=MomentType.STATE_CHANGE, significance_score=score,
                tier=tier, team_id=None,
                reason_codes=[f"state_{t.previous_state.value}_to_{t.new_state.value}"],
                evidence_ids=t.evidence_ids,
            ))

        # Pressure shifts (compare consecutive 5-min windows)
        w5 = sorted([w for w in windows if w.window_seconds == 300], key=lambda w: w.timestamp)
        for i in range(1, len(w5)):
            prev_w, curr_w = w5[i - 1], w5[i]
            for label, team_id, prev_v, curr_v in [
                ("home_pressure", self.home_id, prev_w.home_pressure_index, curr_w.home_pressure_index),
                ("away_pressure", self.away_id, prev_w.away_pressure_index, curr_w.away_pressure_index),
            ]:
                delta = curr_v - prev_v
                if abs(delta) >= 15:
                    evi = self._make_evidence(
                        "pressure_index", curr_v, baseline=prev_v, timestamp=curr_w.timestamp,
                        team_id=team_id, etype=EvidenceType.SHIFT, window="5m",
                    )
                    score = _clamp(30 + abs(delta))
                    moments.append(SignificantMoment(
                        moment_id=_mid(), timestamp=curr_w.timestamp,
                        minute=int(curr_w.timestamp // 60),
                        moment_type=MomentType.PRESSURE_SHIFT,
                        significance_score=score,
                        tier=SignificanceTier.NOTABLE if score < 60 else SignificanceTier.HIGH,
                        team_id=team_id,
                        reason_codes=[f"pressure_delta_{delta:+.1f}"],
                        evidence_ids=[evi.evidence_id],
                    ))

            # Field tilt shifts
            for label, team_id, prev_v, curr_v in [
                ("home_tilt", self.home_id, prev_w.home_field_tilt, curr_w.home_field_tilt),
            ]:
                delta = curr_v - prev_v
                if abs(delta) >= 12:
                    evi = self._make_evidence(
                        "field_tilt", curr_v, baseline=prev_v, timestamp=curr_w.timestamp,
                        team_id=team_id, etype=EvidenceType.SHIFT, window="5m",
                    )
                    score = _clamp(25 + abs(delta) * 1.5)
                    moments.append(SignificantMoment(
                        moment_id=_mid(), timestamp=curr_w.timestamp,
                        minute=int(curr_w.timestamp // 60),
                        moment_type=MomentType.FIELD_TILT_SHIFT,
                        significance_score=score,
                        tier=SignificanceTier.NOTABLE,
                        team_id=self.home_id if delta > 0 else self.away_id,
                        reason_codes=[f"field_tilt_delta_{delta:+.1f}"],
                        evidence_ids=[evi.evidence_id],
                    ))

        moments.sort(key=lambda m: m.timestamp)
        return moments

    # ── explanation contexts ──────────────────────────────────────────────

    def _build_explanation_contexts(
        self, moments: list[SignificantMoment],
    ) -> list[ExplanationContext]:
        contexts: list[ExplanationContext] = []
        for m in moments:
            if m.tier in (SignificanceTier.HIGH, SignificanceTier.CRITICAL):
                # Gather related evidence
                related_evi = [e for e in self._evidence if e.evidence_id in m.evidence_ids]
                primary_metric = related_evi[0].metric_name if related_evi else m.moment_type.value
                current_val = related_evi[0].value if related_evi else m.significance_score
                baseline = related_evi[0].baseline_value if related_evi else None
                delta = related_evi[0].delta if related_evi else None

                ctx_dict: dict[str, Any] = {}
                for ev in related_evi:
                    ctx_dict[ev.metric_name] = ev.value

                contexts.append(ExplanationContext(
                    moment_id=m.moment_id,
                    timestamp=m.timestamp,
                    minute=m.minute,
                    event_type=m.moment_type.value,
                    primary_metric=primary_metric,
                    current_value=current_val,
                    baseline_value=baseline,
                    delta=delta,
                    affected_team_id=m.team_id,
                    related_player_ids=[m.player_id] if m.player_id else [],
                    related_event_ids=m.source_event_ids,
                    evidence_ids=m.evidence_ids,
                    context=ctx_dict,
                ))
        return contexts

    # ── timeline ──────────────────────────────────────────────────────────

    def _build_timeline(
        self,
        typed: dict[str, list],
        moments: list[SignificantMoment],
        states: list[MatchStateClassification],
    ) -> list[TimelineEntry]:
        entries: list[TimelineEntry] = []

        # Lifecycle events
        for ev in typed["all"]:
            if ev.event_type in (EventType.MATCH_START, EventType.HALF_TIME,
                                 EventType.SECOND_HALF_START, EventType.MATCH_END):
                entries.append(TimelineEntry(
                    timestamp=ev.timestamp, minute=int(ev.timestamp // 60),
                    label=ev.event_type.value, event_ids=[ev.event_id],
                ))

        # Significant moments
        state_lookup = {s.timestamp: s for s in states}
        for m in moments:
            if m.tier in (SignificanceTier.NOTABLE, SignificanceTier.HIGH, SignificanceTier.CRITICAL):
                # Find state before/after
                s_before = s_after = None
                for s in states:
                    if s.timestamp <= m.timestamp:
                        s_before = s.state
                    elif s_after is None:
                        s_after = s.state

                entries.append(TimelineEntry(
                    timestamp=m.timestamp,
                    minute=m.minute,
                    label=f"{m.moment_type.value}" + (f" ({m.team_id})" if m.team_id else ""),
                    moment_type=m.moment_type,
                    significance_score=m.significance_score,
                    tier=m.tier,
                    team_id=m.team_id,
                    event_ids=m.source_event_ids,
                    evidence_ids=m.evidence_ids,
                    state_before=s_before,
                    state_after=s_after,
                ))

        entries.sort(key=lambda e: e.timestamp)
        return entries

    # ── evidence factory ──────────────────────────────────────────────────

    def _make_evidence(
        self,
        metric: str,
        value: float,
        *,
        baseline: float | None = None,
        timestamp: float = 0.0,
        team_id: str | None = None,
        player_id: str | None = None,
        etype: EvidenceType = EvidenceType.METRIC,
        window: str | None = None,
        event_ids: list[str] | None = None,
    ) -> Evidence:
        self._evi_counter += 1
        eid = f"evi_{self._evi_counter:04d}"
        delta = round(value - baseline, 4) if baseline is not None else None
        ev = Evidence(
            evidence_id=eid,
            source_event_ids=event_ids or [],
            metric_name=metric,
            value=round(value, 4),
            baseline_value=round(baseline, 4) if baseline is not None else None,
            delta=delta,
            window=window,
            team_id=team_id,
            player_id=player_id,
            timestamp=round(timestamp, 2),
            evidence_type=etype,
        )
        self._evidence.append(ev)
        return ev
