"""Core match simulation engine.

Drives a 90-minute (configurable) match by maintaining a ``MatchState``
and producing events via weighted probabilistic transitions that depend
on the current game context — field zone, team style, player attributes,
score, and scenario modifiers.

All randomness flows through a seeded ``random.Random`` instance so the
match is fully reproducible.
"""

from __future__ import annotations

import random as _random_mod
import uuid
from dataclasses import dataclass, field
from typing import Optional

from simulator.models.match import (
    Match,
    Team,
    PlayingStyle,
    PitchThird,
    PITCH_LENGTH_M,
    PITCH_WIDTH_M,
    classify_third,
)
from simulator.models.player import Player, Position
from simulator.models.events import (
    EventType,
    MatchEvent,
    ShotOutcome,
)
from simulator.engine.possession import PossessionState
from simulator.engine.scenarios import (
    Scenario,
    ScenarioModifiers,
    SCENARIO_MODS,
    detect_scenario,
)
from simulator.engine import events as evt
from simulator.engine.movement import (
    clamp_x,
    clamp_y,
    jitter,
    shot_position,
    distance,
)


# ---------------------------------------------------------------------------
# Configurable constants
# ---------------------------------------------------------------------------

HALF_DURATION_S: float = 45 * 60  # 45 minutes per half in seconds.
HALF_TIME_BREAK_S: float = 15 * 60  # 15-minute half-time interval.

# Base event weights (before style/scenario multipliers).
BASE_WEIGHTS: dict[str, float] = {
    "pass": 45.0,
    "carry": 15.0,
    "pressure": 12.0,
    "shot": 2.0,
    "cross": 4.0,
    "tackle": 5.0,
    "interception": 4.0,
    "foul": 2.5,
}

# Style multipliers layered on top of base weights.
STYLE_MULTIPLIERS: dict[PlayingStyle, dict[str, float]] = {
    PlayingStyle.POSSESSION: {
        "pass": 1.35, "carry": 1.15, "pressure": 0.8,
        "shot": 0.85, "cross": 0.9, "tackle": 0.85,
        "interception": 0.85, "foul": 0.8,
    },
    PlayingStyle.BALANCED: {
        "pass": 1.0, "carry": 1.0, "pressure": 1.0,
        "shot": 1.0, "cross": 1.0, "tackle": 1.0,
        "interception": 1.0, "foul": 1.0,
    },
    PlayingStyle.HIGH_PRESS: {
        "pass": 0.95, "carry": 0.9, "pressure": 1.7,
        "shot": 1.1, "cross": 1.0, "tackle": 1.3,
        "interception": 1.3, "foul": 1.15,
    },
    PlayingStyle.COUNTER_ATTACK: {
        "pass": 0.85, "carry": 1.3, "pressure": 0.75,
        "shot": 1.2, "cross": 1.1, "tackle": 1.0,
        "interception": 1.1, "foul": 0.95,
    },
    PlayingStyle.DEFENSIVE: {
        "pass": 0.9, "carry": 0.85, "pressure": 0.9,
        "shot": 0.7, "cross": 0.8, "tackle": 1.2,
        "interception": 1.2, "foul": 1.1,
    },
    PlayingStyle.DIRECT: {
        "pass": 0.8, "carry": 1.1, "pressure": 1.0,
        "shot": 1.25, "cross": 1.3, "tackle": 1.05,
        "interception": 1.0, "foul": 1.0,
    },
}

# Zone multipliers — shots/crosses only likely in attacking third.
_ZONE_SHOT_MUL: dict[PitchThird, float] = {
    PitchThird.DEFENSIVE: 0.01,
    PitchThird.MIDDLE: 0.10,
    PitchThird.ATTACKING: 1.0,
}
_ZONE_CROSS_MUL: dict[PitchThird, float] = {
    PitchThird.DEFENSIVE: 0.02,
    PitchThird.MIDDLE: 0.15,
    PitchThird.ATTACKING: 1.0,
}

# Time gap between events (seconds of simulation time).
MIN_GAP_S: float = 2.0
MAX_GAP_S: float = 8.0

# Minimum events between possession changes (prevents ping-pong).
MIN_EVENTS_BEFORE_TURNOVER: int = 2

# Possession-change base probability per event tick.
BASE_TURNOVER_PROB: float = 0.08


# ---------------------------------------------------------------------------
# Match state
# ---------------------------------------------------------------------------

@dataclass
class MatchState:
    """Mutable state carried through the entire simulation."""

    match: Match
    rng: _random_mod.Random

    # Clock
    sim_time: float = 0.0
    half: int = 1  # 1 or 2

    # Score
    home_score: int = 0
    away_score: int = 0

    # Possession
    possession: PossessionState = field(default_factory=PossessionState)

    # Event stream
    events: list[MatchEvent] = field(default_factory=list)

    # Counters (for the summary report)
    home_passes: int = 0
    away_passes: int = 0
    home_pass_success: int = 0
    away_pass_success: int = 0
    home_progressive_passes: int = 0
    away_progressive_passes: int = 0
    home_shots: int = 0
    away_shots: int = 0
    home_shots_on_target: int = 0
    away_shots_on_target: int = 0
    home_pressure: int = 0
    away_pressure: int = 0
    home_fouls: int = 0
    away_fouls: int = 0
    home_tackles: int = 0
    away_tackles: int = 0
    home_interceptions: int = 0
    away_interceptions: int = 0
    home_carries: int = 0
    away_carries: int = 0
    home_crosses: int = 0
    away_crosses: int = 0

    # Sequence length counter (events since last possession change).
    events_in_current_sequence: int = 0

    # Scenario override (if None, auto-detect).
    forced_scenario: Optional[Scenario] = None

    # Running sums for average-pass-distance calculation.
    total_pass_distance: float = 0.0
    total_passes: int = 0
    total_possession_sequences: int = 0
    total_possession_duration: float = 0.0

    @property
    def home_team(self) -> Team:
        return self.match.home_team

    @property
    def away_team(self) -> Team:
        return self.match.away_team

    @property
    def duration_s(self) -> float:
        return self.match.duration_minutes * 60.0

    @property
    def match_minute(self) -> int:
        return int(self.sim_time // 60)

    def is_home_possession(self) -> bool:
        return self.possession.team_id == self.home_team.team_id

    def possession_team(self) -> Team:
        if self.is_home_possession():
            return self.home_team
        return self.away_team

    def defending_team(self) -> Team:
        if self.is_home_possession():
            return self.away_team
        return self.home_team

    def attacking_right(self) -> bool:
        """Home attacks right in the first half, left in the second."""
        is_home = self.is_home_possession()
        if self.half == 1:
            return is_home
        return not is_home


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class MatchEngine:
    """Simulates a complete football match and returns an event stream."""

    def __init__(self, match: Match, *, seed: int | None = None) -> None:
        self.match = match
        seed = seed if seed is not None else match.random_seed
        self.rng = _random_mod.Random(seed)

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def simulate(self) -> list[MatchEvent]:
        """Run the full simulation and return the chronological event list."""
        state = MatchState(match=self.match, rng=self.rng)

        # --- First half ---
        state.events.append(evt.make_match_start(self.rng, self.match.match_id))
        self._kick_off(state)
        self._run_half(state, half=1)

        # --- Half-time ---
        ht_ts = HALF_DURATION_S
        state.events.append(evt.make_half_time(self.rng, self.match.match_id, ht_ts))

        # --- Second half ---
        state.sim_time = HALF_DURATION_S + HALF_TIME_BREAK_S
        state.half = 2
        state.events.append(
            evt.make_second_half_start(self.rng, self.match.match_id, state.sim_time)
        )
        self._kick_off(state, second_half=True)
        self._run_half(state, half=2)

        # --- Full time ---
        state.events.append(
            evt.make_match_end(self.rng, self.match.match_id, state.sim_time, state.match_minute)
        )

        # Accrue final possession duration.
        state.possession._accrue_duration(state.sim_time)

        self._state = state  # stash for report generation
        return state.events

    @property
    def state(self) -> MatchState:
        """Access match state after simulation (for reporting)."""
        return self._state

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------

    def _kick_off(self, state: MatchState, *, second_half: bool = False) -> None:
        """Award possession at centre-circle after kick-off."""
        if second_half:
            team = state.away_team
        else:
            team = state.home_team
        player = self.rng.choice(team.outfield_players())
        state.possession.start_new_sequence(team.team_id, player.player_id, state.sim_time, self.rng)
        state.possession.update_ball(PITCH_LENGTH_M / 2, PITCH_WIDTH_M / 2)
        state.events_in_current_sequence = 0
        state.total_possession_sequences += 1

    def _run_half(self, state: MatchState, *, half: int) -> None:
        """Simulate one half of football."""
        if half == 1:
            half_end = HALF_DURATION_S
        else:
            half_end = HALF_DURATION_S + HALF_TIME_BREAK_S + HALF_DURATION_S

        while state.sim_time < half_end:
            gap = self.rng.uniform(MIN_GAP_S, MAX_GAP_S)
            state.sim_time = round(state.sim_time + gap, 2)
            if state.sim_time >= half_end:
                break

            self._tick(state)

    def _tick(self, state: MatchState) -> None:
        """Process one simulation tick — pick and emit the next event."""
        # Detect scenario.
        poss_pct_home = state.possession.possession_pct(state.home_team.team_id)
        scenario = state.forced_scenario or detect_scenario(
            match_minute=state.match_minute,
            duration_minutes=state.match.duration_minutes,
            home_score=state.home_score,
            away_score=state.away_score,
            possession_team_is_home=state.is_home_possession(),
            possession_pct_home=poss_pct_home,
        )
        smods = SCENARIO_MODS[scenario]

        # Compute weights.
        pos_team = state.possession_team()
        def_team = state.defending_team()
        style_muls = STYLE_MULTIPLIERS[pos_team.playing_style]
        att_right = state.attacking_right()
        zone = state.possession.current_third(attacking_right=att_right)

        weights: dict[str, float] = {}
        for key, base in BASE_WEIGHTS.items():
            w = base * style_muls.get(key, 1.0)

            # Apply scenario modifier.
            scenario_mul = getattr(smods, f"{key}_weight", 1.0)
            w *= scenario_mul

            # Zone adjustments.
            if key == "shot":
                w *= _ZONE_SHOT_MUL[zone]
            elif key == "cross":
                w *= _ZONE_CROSS_MUL[zone]

            weights[key] = max(w, 0.01)

        # Weighted random choice.
        actions = list(weights.keys())
        total = sum(weights.values())
        r = self.rng.uniform(0, total)
        cum = 0.0
        chosen = actions[0]
        for a in actions:
            cum += weights[a]
            if r <= cum:
                chosen = a
                break

        # Dispatch.
        if chosen == "pass":
            self._do_pass(state)
        elif chosen == "carry":
            self._do_carry(state)
        elif chosen == "pressure":
            self._do_pressure(state, smods)
        elif chosen == "shot":
            self._do_shot(state)
        elif chosen == "cross":
            self._do_cross(state)
        elif chosen == "tackle":
            self._do_tackle(state)
        elif chosen == "interception":
            self._do_interception(state)
        elif chosen == "foul":
            self._do_foul(state)

        state.events_in_current_sequence += 1

    # ---- action helpers ----

    def _pick_player(self, team: Team, *, exclude: str | None = None, prefer_outfield: bool = True) -> Player:
        pool = team.outfield_players() if prefer_outfield else team.players
        if exclude:
            pool = [p for p in pool if p.player_id != exclude]
        if not pool:
            pool = team.players
        return self.rng.choice(pool)

    def _pick_attacker(self, team: Team) -> Player:
        attackers = [p for p in team.players if p.is_attacker]
        if attackers and self.rng.random() < 0.6:
            return self.rng.choice(attackers)
        mids = [p for p in team.players if p.is_midfielder]
        if mids and self.rng.random() < 0.5:
            return self.rng.choice(mids)
        return self._pick_player(team)

    def _current_player(self, state: MatchState) -> Player:
        team = state.possession_team()
        for p in team.players:
            if p.player_id == state.possession.player_id:
                return p
        return self._pick_player(team)

    def _pressure_level(self, state: MatchState) -> float:
        """Estimate ambient pressure on the ball-carrier."""
        def_style = state.defending_team().playing_style
        base = 0.2
        if def_style == PlayingStyle.HIGH_PRESS:
            base = 0.5
        elif def_style == PlayingStyle.DEFENSIVE:
            base = 0.15
        return round(min(1.0, base + self.rng.uniform(-0.1, 0.15)), 4)

    def _do_pass(self, state: MatchState) -> None:
        passer = self._current_player(state)
        receiver = self._pick_player(state.possession_team(), exclude=passer.player_id)
        pressure = self._pressure_level(state)

        pe = evt.make_pass(
            self.rng,
            match_id=self.match.match_id,
            ts=state.sim_time,
            minute=state.match_minute,
            passer=passer,
            receiver=receiver,
            start_x=state.possession.ball_x,
            start_y=state.possession.ball_y,
            attacking_right=state.attacking_right(),
            pressure_level=pressure,
            possession_seq_id=state.possession.sequence_id,
        )
        state.events.append(pe)

        # Stats
        is_home = state.is_home_possession()
        if is_home:
            state.home_passes += 1
            if pe.successful:
                state.home_pass_success += 1
            if pe.progressive:
                state.home_progressive_passes += 1
        else:
            state.away_passes += 1
            if pe.successful:
                state.away_pass_success += 1
            if pe.progressive:
                state.away_progressive_passes += 1

        state.total_pass_distance += pe.distance_meters
        state.total_passes += 1

        if pe.successful:
            state.possession.update_player(receiver.player_id)
            state.possession.update_ball(pe.end_x, pe.end_y)
        else:
            self._turnover(state, cause="failed_pass")

    def _do_carry(self, state: MatchState) -> None:
        player = self._current_player(state)
        ce = evt.make_carry(
            self.rng,
            match_id=self.match.match_id,
            ts=state.sim_time,
            minute=state.match_minute,
            player=player,
            start_x=state.possession.ball_x,
            start_y=state.possession.ball_y,
            attacking_right=state.attacking_right(),
            possession_seq_id=state.possession.sequence_id,
        )
        state.events.append(ce)

        if state.is_home_possession():
            state.home_carries += 1
        else:
            state.away_carries += 1

        if ce.successful:
            state.possession.update_ball(ce.end_x, ce.end_y)
        else:
            self._turnover(state, cause="failed_carry")

    def _do_pressure(self, state: MatchState, smods: ScenarioModifiers) -> None:
        def_team = state.defending_team()
        presser = self._pick_player(def_team)
        target = self._current_player(state)

        pe = evt.make_pressure(
            self.rng,
            match_id=self.match.match_id,
            ts=state.sim_time,
            minute=state.match_minute,
            pressing_player=presser,
            target_player=target,
            ball_x=state.possession.ball_x,
            ball_y=state.possession.ball_y,
            possession_seq_id=state.possession.sequence_id,
        )
        state.events.append(pe)

        if def_team.team_id == state.home_team.team_id:
            state.home_pressure += 1
        else:
            state.away_pressure += 1

        # Successful pressure may trigger turnover.
        if pe.successful:
            turnover_p = BASE_TURNOVER_PROB * smods.turnover_chance * 2.5
            if state.events_in_current_sequence >= MIN_EVENTS_BEFORE_TURNOVER and self.rng.random() < turnover_p:
                self._turnover(state, cause="pressure")

    def _do_shot(self, state: MatchState) -> None:
        shooter = self._pick_attacker(state.possession_team())
        att_right = state.attacking_right()

        # Move ball to a realistic shot position if not already attacking-third.
        sx, sy = shot_position(self.rng, attacking_right=att_right)
        state.possession.update_ball(sx, sy)

        under_pressure = self.rng.random() < self._pressure_level(state) + 0.15
        se = evt.make_shot(
            self.rng,
            match_id=self.match.match_id,
            ts=state.sim_time,
            minute=state.match_minute,
            shooter=shooter,
            start_x=sx,
            start_y=sy,
            attacking_right=att_right,
            under_pressure=under_pressure,
            possession_seq_id=state.possession.sequence_id,
        )
        state.events.append(se)

        is_home = state.is_home_possession()
        if is_home:
            state.home_shots += 1
        else:
            state.away_shots += 1

        if se.outcome in (ShotOutcome.GOAL, ShotOutcome.SAVED):
            if is_home:
                state.home_shots_on_target += 1
            else:
                state.away_shots_on_target += 1

        if se.outcome == ShotOutcome.GOAL:
            if is_home:
                state.home_score += 1
            else:
                state.away_score += 1

            ge = evt.make_goal(
                self.rng,
                match_id=self.match.match_id,
                ts=state.sim_time,
                minute=state.match_minute,
                scorer=shooter,
                home_score=state.home_score,
                away_score=state.away_score,
                ball_x=sx,
                ball_y=sy,
                possession_seq_id=state.possession.sequence_id,
            )
            state.events.append(ge)

            # Restart from centre.
            self._restart_from_centre(state, conceding_team=state.defending_team())
        else:
            # After a non-goal shot, defending team typically gets the ball.
            self._turnover(state, cause="shot_ended")

    def _do_cross(self, state: MatchState) -> None:
        # Crossers tend to be wide players.
        pos_team = state.possession_team()
        wide = [p for p in pos_team.players if p.position in {Position.LW, Position.RW, Position.LB, Position.RB, Position.LM, Position.RM}]
        crosser = self.rng.choice(wide) if wide else self._pick_player(pos_team)

        ce = evt.make_cross(
            self.rng,
            match_id=self.match.match_id,
            ts=state.sim_time,
            minute=state.match_minute,
            crosser=crosser,
            start_x=state.possession.ball_x,
            start_y=state.possession.ball_y,
            attacking_right=state.attacking_right(),
            possession_seq_id=state.possession.sequence_id,
        )
        state.events.append(ce)

        if state.is_home_possession():
            state.home_crosses += 1
        else:
            state.away_crosses += 1

        if ce.successful:
            state.possession.update_ball(ce.end_x, ce.end_y)
            # Successful cross often leads to a header/shot attempt next tick.
        else:
            self._turnover(state, cause="failed_cross")

    def _do_tackle(self, state: MatchState) -> None:
        def_team = state.defending_team()
        tackler = self._pick_player(def_team)
        target = self._current_player(state)

        te = evt.make_tackle(
            self.rng,
            match_id=self.match.match_id,
            ts=state.sim_time,
            minute=state.match_minute,
            tackler=tackler,
            target_player=target,
            ball_x=state.possession.ball_x,
            ball_y=state.possession.ball_y,
            possession_seq_id=state.possession.sequence_id,
        )
        state.events.append(te)

        if def_team.team_id == state.home_team.team_id:
            state.home_tackles += 1
        else:
            state.away_tackles += 1

        if te.successful and state.events_in_current_sequence >= MIN_EVENTS_BEFORE_TURNOVER:
            self._turnover(state, cause="tackle")

    def _do_interception(self, state: MatchState) -> None:
        def_team = state.defending_team()
        interceptor = self._pick_player(def_team)

        ie = evt.make_interception(
            self.rng,
            match_id=self.match.match_id,
            ts=state.sim_time,
            minute=state.match_minute,
            interceptor=interceptor,
            ball_x=state.possession.ball_x,
            ball_y=state.possession.ball_y,
            possession_seq_id=state.possession.sequence_id,
        )
        state.events.append(ie)

        if def_team.team_id == state.home_team.team_id:
            state.home_interceptions += 1
        else:
            state.away_interceptions += 1

        if ie.successful and state.events_in_current_sequence >= MIN_EVENTS_BEFORE_TURNOVER:
            self._turnover(state, cause="interception")

    def _do_foul(self, state: MatchState) -> None:
        def_team = state.defending_team()
        pos_team = state.possession_team()
        fouler = self._pick_player(def_team)
        fouled = self._current_player(state)

        fe = evt.make_foul(
            self.rng,
            match_id=self.match.match_id,
            ts=state.sim_time,
            minute=state.match_minute,
            fouler=fouler,
            fouled_player=fouled,
            ball_x=state.possession.ball_x,
            ball_y=state.possession.ball_y,
            possession_seq_id=state.possession.sequence_id,
        )
        state.events.append(fe)

        if def_team.team_id == state.home_team.team_id:
            state.home_fouls += 1
        else:
            state.away_fouls += 1

        # After a foul, the fouled team retains/gains possession.
        if state.possession.team_id != pos_team.team_id:
            self._switch_possession(state, to_team=pos_team, cause="foul")

    # ---- possession management ----

    def _turnover(self, state: MatchState, *, cause: str) -> None:
        """Switch possession to the other team."""
        old_team = state.possession_team()
        new_team = state.defending_team()

        # Record possession duration for the just-ended sequence.
        seq_dur = state.possession.sequence_duration(state.sim_time)
        state.total_possession_duration += seq_dur

        pce = evt.make_possession_change(
            self.rng,
            match_id=self.match.match_id,
            ts=state.sim_time,
            minute=state.match_minute,
            from_team_id=old_team.team_id,
            to_team_id=new_team.team_id,
            cause=cause,
            ball_x=state.possession.ball_x,
            ball_y=state.possession.ball_y,
            possession_seq_id=state.possession.sequence_id,
        )
        state.events.append(pce)

        # Recovery event for new possessing team.
        recoverer = self._pick_player(new_team)
        re = evt.make_recovery(
            self.rng,
            match_id=self.match.match_id,
            ts=state.sim_time,
            minute=state.match_minute,
            player=recoverer,
            ball_x=state.possession.ball_x,
            ball_y=state.possession.ball_y,
            possession_seq_id="",  # will be set below
        )

        new_seq = state.possession.start_new_sequence(
            new_team.team_id, recoverer.player_id, state.sim_time, self.rng,
        )
        re.possession_sequence_id = new_seq
        state.events.append(re)
        state.events_in_current_sequence = 0
        state.total_possession_sequences += 1

    def _switch_possession(self, state: MatchState, *, to_team: Team, cause: str) -> None:
        """Like ``_turnover`` but explicitly named target team."""
        seq_dur = state.possession.sequence_duration(state.sim_time)
        state.total_possession_duration += seq_dur

        player = self._pick_player(to_team)
        new_seq = state.possession.start_new_sequence(
            to_team.team_id, player.player_id, state.sim_time, self.rng,
        )
        state.events_in_current_sequence = 0
        state.total_possession_sequences += 1

    def _restart_from_centre(self, state: MatchState, *, conceding_team: Team) -> None:
        """Restart from the centre circle after a goal."""
        seq_dur = state.possession.sequence_duration(state.sim_time)
        state.total_possession_duration += seq_dur

        player = self._pick_player(conceding_team)
        state.possession.start_new_sequence(
            conceding_team.team_id, player.player_id, state.sim_time, self.rng,
        )
        state.possession.update_ball(PITCH_LENGTH_M / 2, PITCH_WIDTH_M / 2)
        state.events_in_current_sequence = 0
        state.total_possession_sequences += 1
