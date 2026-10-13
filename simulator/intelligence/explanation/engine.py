"""Deterministic Phase 3 orchestration; no IO, cloud, or text generation service."""
from __future__ import annotations
from simulator.intelligence.models import MatchIntelligence, SignificantMoment, SignificanceTier, MomentType, RollingWindowMetrics
from simulator.models.events import MatchEvent, ShotEvent, GoalEvent, PressureEvent
from .models import (ExplanationConfig, ExplanationReport, MomentExplanation, MetricChange,
                     EvidenceChain, GoalContext, GameRhythmAssessment, ExplanationType, TraceEvidence)
from .evidence_selector import EvidenceSelector
from .confidence import confidence
from .rules import TYPE_MAP, significance, rhythm
from .templates import TEMPLATES, CONSEQUENCES
from .validator import validate_input, validate_text_fields


def number(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")

class ExplanationEngine:
    def __init__(self, config: ExplanationConfig | None = None,
                 source_events: list[MatchEvent] | None = None):
        self.config = config or ExplanationConfig()
        self.has_events = source_events is not None
        self.events = sorted(source_events or [], key=lambda e: (e.timestamp, e.event_id))

    def _eligible(self, m: SignificantMoment) -> bool:
        if m.tier in (SignificanceTier.CRITICAL, SignificanceTier.HIGH):
            return True
        return (m.tier == SignificanceTier.NOTABLE and self.config.explain_notable
                and m.significance_score >= self.config.minimum_score)

    def explain(self, intelligence: MatchIntelligence) -> ExplanationReport:
        validate_input(intelligence, self.events, self.has_events)
        selector = EvidenceSelector(intelligence, self.events, self.config.max_evidence)
        assessments = self._rhythms(intelligence, selector)
        explanations = []
        for m in sorted(intelligence.detected_moments, key=lambda m: (m.timestamp, m.moment_id)):
            if self._eligible(m):
                exp = self._build(m, intelligence, selector)
                # NOTABLE requires a delta, transition, or additional contextual evidence.
                if m.tier != SignificanceTier.NOTABLE or (
                    len(exp.evidence_ids) > 1 or any(v.delta is not None for v in exp.supporting_metrics.values())):
                    explanations.append(exp)
        used = {eid for e in explanations for eid in e.evidence_ids}
        used.update(eid for a in assessments for eid in a.evidence_ids)
        return ExplanationReport(match_id=intelligence.match_id, explanations=explanations,
            evidence=[selector.ledger[eid] for eid in sorted(used)], rhythm_assessments=assessments)

    def explain_moment(self, moment: SignificantMoment,
                       intelligence: MatchIntelligence) -> MomentExplanation | None:
        validate_input(intelligence, self.events, self.has_events)
        canonical = next((m for m in intelligence.detected_moments if m.moment_id == moment.moment_id), None)
        if canonical is None or canonical != moment:
            raise ValueError("Moment does not match intelligence source")
        if not self._eligible(moment):
            return None
        return self._build(moment, intelligence,
            EvidenceSelector(intelligence, self.events, self.config.max_evidence))

    def _windows(self, intel: MatchIntelligence, timestamp: float) -> tuple[
        tuple[int, RollingWindowMetrics] | None, tuple[int, RollingWindowMetrics] | None]:
        windows = sorted([(i, w) for i, w in enumerate(intel.rolling_windows)
                          if w.window_seconds == 300 and w.timestamp <= timestamp],
                         key=lambda pair: pair[1].timestamp)
        if not windows:
            return None, None
        current = windows[-1]
        previous = windows[-2] if len(windows) > 1 else None
        return current, previous

    def _window_evidence(self, selector: EvidenceSelector,
                         current: tuple[int, RollingWindowMetrics] | None,
                         previous: tuple[int, RollingWindowMetrics] | None,
                         side: str, metrics: list[str], key: str) -> list[TraceEvidence]:
        candidates = []
        if current is None:
            return candidates
        ci, cw = current
        for metric in metrics:
            attr = metric if metric in ("turnovers", "event_density") else side + "_" + metric
            paths = [f"rolling_windows/{ci}/{attr}"]
            baseline = None
            if previous:
                pi, pw = previous
                baseline = getattr(pw, attr)
                paths.append(f"rolling_windows/{pi}/{attr}")
            team = None if metric in ("turnovers", "event_density") else (
                selector.intel.home_team.team_id if side == "home" else selector.intel.away_team.team_id)
            candidates.append(selector.project(f"{key}_{metric}", metric, getattr(cw, attr), cw.timestamp,
                paths, baseline=baseline, team=team, window="5m",
                facts={"baseline_timestamp": previous[1].timestamp if previous else None,
                       "window_seconds": 300}))
        return candidates

    def _build(self, m: SignificantMoment, intel: MatchIntelligence,
               selector: EvidenceSelector) -> MomentExplanation:
        candidates, context, goal_context = [], {}, None
        event_map = {e.event_id: e for e in self.events if e.timestamp <= m.timestamp}
        direct = [event_map[eid] for eid in m.source_event_ids if eid in event_map]
        primary = selector.ledger[m.evidence_ids[0]]
        kind = TYPE_MAP[m.moment_type]
        # Evidence attribution takes precedence over the Phase 2 affected-team label.
        tid = primary.team_id or m.team_id
        side = "away" if tid == intel.away_team.team_id else "home"
        subject = tid or "Match"
        current, previous = self._windows(intel, m.timestamp)
        transition = next((t for t in intel.state_transitions
                           if t.timestamp == m.timestamp and set(t.evidence_ids) & set(m.evidence_ids)), None)
        if transition:
            state_team = next((selector.ledger[eid].team_id for eid in transition.evidence_ids
                               if selector.ledger[eid].team_id), None)
            tid = state_team or tid
            side = "away" if tid == intel.away_team.team_id else "home"
            subject = tid or "Match"
        if m.moment_type == MomentType.TACTICAL_SHIFT:
            if primary.metric_name == "progressive_actions" and primary.delta is not None:
                kind = ExplanationType.PROGRESSION_SURGE
            elif transition:
                kind = {"HOME_CONTROL": ExplanationType.CONTROL_SHIFT,
                        "AWAY_CONTROL": ExplanationType.CONTROL_SHIFT,
                        "HIGH_PRESSURE": ExplanationType.HIGH_PRESSURE_PHASE,
                        "TRANSITION_PHASE": ExplanationType.TRANSITION_PHASE,
                        "CHAOTIC_PHASE": ExplanationType.CHAOS_PHASE}.get(
                            transition.new_state.value, ExplanationType.MAJOR_MOMENT)
        if m.player_id and kind == ExplanationType.PLAYER_ACTIVITY_SHIFT:
            subject = m.player_id
        if m.moment_type == MomentType.STATE_CHANGE and transition is None:
            raise ValueError("State-change moment has no matching transition")
        shot = next((e for e in direct if isinstance(e, ShotEvent)), None)
        goal = next((e for e in direct if isinstance(e, GoalEvent)), None)
        if m.moment_type == MomentType.GOAL and self.has_events and goal is None:
            raise ValueError("Goal moment does not reference a goal event")
        if m.moment_type in (MomentType.SHOT, MomentType.SHOT_ON_TARGET) and self.has_events and shot is None:
            raise ValueError("Shot moment does not reference a shot event")
        if goal:
            preceding = [e for e in self.events if m.timestamp-self.config.goal_lookback_seconds <= e.timestamp <= m.timestamp]
            shots = [e for e in preceding if isinstance(e, ShotEvent) and e.team_id == goal.team_id
                     and e.outcome.value == "goal" and e.timestamp == goal.timestamp
                     and e.possession_sequence_id == goal.possession_sequence_id]
            shot = shots[-1] if shots else None
        if shot:
            facts = {k: getattr(shot, k) for k in
                     ("shot_distance_meters", "shot_quality", "under_pressure", "start_x", "start_y")}
            facts["outcome"] = shot.outcome.value
            candidates.append(selector.project(m.moment_id+"_shot", "shot_quality", shot.shot_quality,
                shot.timestamp, [f"events/{shot.event_id}"], team=shot.team_id, facts=facts,
                event_ids=[shot.event_id], origin="SOURCE_EVENT"))
            context["shot_context"] = facts
        sequence = None
        trigger_event = goal or shot
        if trigger_event and trigger_event.possession_sequence_id:
            sequence = next((s for s in intel.possession_sequences
                if s.sequence_id == trigger_event.possession_sequence_id and s.end_time <= m.timestamp
                and s.start_time >= m.timestamp-self.config.goal_lookback_seconds), None)
            if sequence:
                si = intel.possession_sequences.index(sequence)
                ids = [e.event_id for e in self.events if e.possession_sequence_id == sequence.sequence_id
                       and sequence.start_time <= e.timestamp <= m.timestamp]
                candidates.append(selector.project(m.moment_id+"_sequence", "possession_sequence",
                    sequence.duration_s, m.timestamp, [f"possession_sequences/{si}"], team=sequence.team_id,
                    facts=sequence.model_dump(mode="json"), event_ids=ids))
                context["preceding_sequence"] = sequence.model_dump(mode="json")
        if m.moment_type == MomentType.GOAL:
            goal_context = GoalContext(goal_time=m.timestamp, scoring_team=m.team_id or tid,
                scorer=m.player_id, preceding_sequence_id=sequence.sequence_id if sequence else None,
                sequence_duration=sequence.duration_s if sequence else None,
                passes=sequence.passes if sequence else None,
                progressive_actions=sequence.progressive_actions if sequence else None,
                pressure_received=sequence.pressure_received if sequence else None,
                shot_context=context.get("shot_context", {}),
                direct_event_ids=m.source_event_ids + ([shot.event_id] if shot else []),
                contextual_event_ids=[e.event_id for e in self.events if sequence and
                    e.possession_sequence_id == sequence.sequence_id and e.timestamp <= m.timestamp
                    and e.event_id not in m.source_event_ids and (shot is None or e.event_id != shot.event_id)])
        if transition:
            context["state_transition"] = transition.model_dump(mode="json")
            candidates.extend(selector.ledger[eid] for eid in transition.evidence_ids)
            state = transition.new_state.value
            context["phase_explanation_type"] = (
                "CONTROL_SHIFT" if state in ("HOME_CONTROL", "AWAY_CONTROL") else
                {"HIGH_PRESSURE": "HIGH_PRESSURE_PHASE", "CHAOTIC_PHASE": "CHAOS_PHASE",
                 "TRANSITION_PHASE": "TRANSITION_PHASE"}.get(state, "STATE_CHANGE"))
        if primary.baseline_value is not None and previous:
            context["baseline_timestamp"] = previous[1].timestamp
            context["window_overlap_seconds"] = max(0, 300 - (current[1].timestamp - previous[1].timestamp)) if current else None
            context["comparison_note"] = "Comparison uses trailing windows at consecutive Phase 2 checkpoints; overlap is recorded explicitly."
        metrics = (["pressure_count", "field_tilt", "turnovers", "progressive_actions"]
                   if kind == ExplanationType.PRESSURE_SHIFT else
                   ["possession_pct", "field_tilt", "pressure_index", "progressive_actions", "turnovers"])
        candidates.extend(self._window_evidence(selector, current, previous, side, metrics, m.moment_id))
        if kind == ExplanationType.PRESSURE_SHIFT and current and self.has_events:
            pressure_observations = [e for e in self.events if isinstance(e, PressureEvent)
                and e.pressing_team_id == tid
                and max(0, current[1].timestamp-300) <= e.timestamp <= current[1].timestamp]
            if pressure_observations:
                observation_evidence = selector.project(m.moment_id+"_pressure_observations", "pressure_count",
                    getattr(current[1], side + "_pressure_count"), current[1].timestamp,
                    [f"rolling_windows/{current[0]}/{side}_pressure_count"], team=tid, window="5m",
                    facts={"event_observations": [e.model_dump(mode="json") for e in pressure_observations]},
                    event_ids=[e.event_id for e in pressure_observations])
                candidates.insert(0, observation_evidence)
        selected = selector.select(m, candidates)
        changes = {}
        for e in selected:
            percentage = e.metric_name in ("field_tilt", "possession_pct")
            unit = "percentage points" if percentage else "index points" if e.metric_name == "pressure_index" else "value"
            changes[e.evidence_id] = MetricChange(metric=e.metric_name, current=e.value,
                baseline=e.baseline_value, delta=e.delta,
                percentage_delta=(round(e.delta / e.baseline_value * 100, 4)
                    if e.baseline_value and e.delta is not None and not percentage
                    and e.metric_name not in ("pressure_index", "state_transition", "state_classification") else None),
                unit=unit, window=e.window, current_timestamp=e.timestamp,
                baseline_timestamp=(e.facts.get("baseline_timestamp", context.get("baseline_timestamp"))
                                    if e.baseline_value is not None else None),
                evidence_id=e.evidence_id, team_id=e.team_id, player_id=e.player_id)
        if transition:
            trigger = f"The match state changed from {transition.previous_state.value} to {transition.new_state.value}."
            change = trigger
        elif kind == ExplanationType.GOAL:
            trigger = f"{subject} scored a goal."
            change = (f"The score is {goal.home_score} to {goal.away_score}." if goal else
                      "The recorded goal is a change in the scoreline; score totals are unavailable in Phase 2 evidence.")
        elif kind == ExplanationType.SHOT_THREAT:
            trigger = (f"{subject} recorded a shot with outcome {shot.outcome.value}." if shot else
                       f"{subject} recorded a shot on target." if m.moment_type == MomentType.SHOT_ON_TARGET else
                       f"{subject} recorded a shot.")
            change = (f"Shot quality is {number(shot.shot_quality)} and distance is {number(shot.shot_distance_meters)} metres."
                      if shot else "Shot distance and quality are unavailable in the supplied evidence.")
        else:
            trigger = f"{subject} recorded a change in {primary.metric_name}."
            change = self._metric_fact(primary)
        supporting = [e for e in selected[1:] if e.metric_name not in ("state_classification", "state_transition")]
        support = "Supporting observations: " + " ".join(self._metric_fact(e) for e in supporting) if supporting else "No independent contextual metric is available."
        if sequence:
            support += " The linked possession sequence is context; individual earlier actions are not assigned causal credit."
        full_baseline = bool(previous and previous[1].timestamp >= 300)
        directional = [e.delta for e in selected if e.delta is not None and e.team_id == tid
                       and e.metric_name in ("pressure_index", "possession_pct", "field_tilt")]
        conflict = any(d > 0 for d in directional) and any(d < 0 for d in directional)
        complete_sequence = bool(sequence and self.has_events and
            len([e for e in self.events if e.possession_sequence_id == sequence.sequence_id
                 and e.timestamp <= m.timestamp]) == sequence.num_events)
        conf, caveats = confidence(selected, complete_sequence=complete_sequence,
                                  full_baseline=full_baseline, state_clear=transition is not None,
                                  conflicts=conflict, sparse=bool(current and current[1].event_density < 5))
        if not self.has_events:
            caveats.append("Source-event IDs are preserved from Phase 2 but cannot be independently resolved without raw events.")
        if m.moment_type == MomentType.GOAL and not sequence:
            caveats.append("No completed goal possession fits the configured lookback; no buildup is inferred.")
        if tid != m.team_id:
            caveats.append("The primary metric describes a different team from the Phase 2 affected-team label.")
        if current and current[1].timestamp < m.timestamp:
            caveats.append("Rolling context is the latest earlier checkpoint, not a snapshot at the event time.")
        context["selected_evidence"] = [e.model_dump(mode="json") for e in selected]
        context["confidence_factors"] = {"complete_sequence": complete_sequence, "full_baseline": full_baseline,
            "clear_state_transition": transition is not None, "conflicting_directions": conflict,
            "sparse_activity": bool(current and current[1].event_density < 5)}
        context["phase2_explanation_context"] = next((c.model_dump(mode="json") for c in intel.explanation_contexts
                                                     if c.moment_id == m.moment_id), None)
        context["trigger_context_change_consequence"] = {"trigger_evidence_ids": m.evidence_ids,
            "context_evidence_ids": [e.evidence_id for e in supporting],
            "change_evidence_ids": [e.evidence_id for e in selected if e.delta is not None],
            "consequence": CONSEQUENCES[kind]}
        if kind == ExplanationType.PRESSURE_SHIFT:
            pressure_events = [e for e in self.events if isinstance(e, PressureEvent) and
                e.pressing_team_id == tid and current and max(0, current[1].timestamp-300) <= e.timestamp <= current[1].timestamp]
            context["tactical_pressure"] = {"pressure_change": changes[primary.evidence_id].model_dump(mode="json"),
                "pressure_volume": current[1].model_dump().get(side+"_pressure_count") if current else None,
                "event_observations": [{"event_id": e.event_id, "intensity": e.intensity,
                    "location": e.zone, "successful": e.successful} for e in pressure_events],
                "successful_pressures": sum(e.successful for e in pressure_events) if self.has_events else None,
                "recorded_attacking_zone_pressure_events": (
                    sum(e.zone.startswith("attacking_") for e in pressure_events) if self.has_events else None),
                "location_caveat": "Raw pressure zones use fixed pitch coordinates, not the pressing team's attacking direction.",
                "forced_turnovers": None,
                "forced_turnover_caveat": "Phase 2 does not publish a linked forced-turnover series; none is inferred.",
                "supporting_evidence": [e.evidence_id for e in selected]}
        if goal_context:
            goal_context.evidence_ids = [e.evidence_id for e in selected if e.metric_name in ("goal", "possession_sequence", "shot_quality")]
        if shot and kind == ExplanationType.SHOT_THREAT:
            consequence = ("The shot represents an attempt on target; shot quality is a MatchLens heuristic."
                           if shot.outcome.value in ("saved", "goal") else
                           "The shot represents an attacking attempt; shot quality is a MatchLens heuristic.")
        else:
            consequence = CONSEQUENCES[kind]
        context["trigger_context_change_consequence"]["consequence"] = consequence
        prose = TEMPLATES[kind].render(subject=subject, label=kind.value.replace("_", " "),
            trigger=trigger, change=change, consequence=consequence, support=support)
        validate_text_fields(prose)
        return MomentExplanation(explanation_id=f"exp_{m.moment_id}", moment_id=m.moment_id,
            timestamp=m.timestamp, explanation_type=kind, **prose,
            affected_team_id=m.team_id, affected_player_ids=[m.player_id] if m.player_id else [],
            significance_score=m.significance_score, significance_tier=m.tier, confidence=conf,
            evidence_ids=[e.evidence_id for e in selected],
            source_event_ids=sorted(set(m.source_event_ids + [eid for e in selected for eid in e.source_event_ids])),
            supporting_metrics=changes, context=context, caveats=caveats,
            evidence_chains=[EvidenceChain(chain_id=f"chain_{m.moment_id}",
                evidence_ids=[e.evidence_id for e in selected], logical_relationship="SUPPORTS", strength=conf)],
            significance_breakdown=significance(m, primary, transition, shot), goal_context=goal_context)

    @staticmethod
    def _metric_fact(e: TraceEvidence) -> str:
        identity = e.player_id or e.team_id
        team = f"{identity} " if identity else ""
        window = f" in the {e.window} window" if e.window else ""
        if e.metric_name == "possession_sequence":
            return (f"The linked sequence lasted {number(e.value)} seconds, with {e.facts['passes']} passes, "
                    f"{e.facts['progressive_actions']} progressive actions and {e.facts['pressure_received']} pressure events.")
        if e.baseline_value is not None:
            unit = " percentage points" if e.metric_name in ("field_tilt", "possession_pct") else ""
            return (f"{team}{e.metric_name} moved from {number(e.baseline_value)} to {number(e.value)}{window}, "
                    f"an absolute change of {number(e.delta)}{unit}.")
        return f"{team}{e.metric_name} was {number(e.value)}{window}."

    def _rhythms(self, intel: MatchIntelligence,
                 selector: EvidenceSelector) -> list[GameRhythmAssessment]:
        assessments = []
        windows = sorted([(i, w) for i, w in enumerate(intel.rolling_windows) if w.window_seconds == 300],
                         key=lambda pair: pair[1].timestamp)
        previous = None
        for current in windows:
            _, w = current
            label, score, side = rhythm(w)
            side = side or "home"
            evidence = self._window_evidence(selector, current, previous, side,
                ["event_density", "turnovers", "possession_pct", "field_tilt", "passes", "pass_success"],
                f"rhythm_{number(w.timestamp)}")
            prior = rhythm(previous[1])[0] if previous else None
            changes = {e.metric_name: e.delta for e in evidence if e.delta is not None}
            tid = (intel.home_team.team_id if side == "home" else intel.away_team.team_id) if label == "CONTROLLED" else None
            explanation = f"Rolling rhythm is {label}. " + " ".join(self._metric_fact(e) for e in evidence[:2])
            if prior and prior != label:
                explanation = f"Rolling rhythm changed from {prior} to {label}. " + explanation
            assessments.append(GameRhythmAssessment(rhythm_type=label, score=score,
                confidence=(.4 if w.event_density < 5 else .8 if previous and previous[1].timestamp >= 300 else .5),
                timestamp=w.timestamp, team_id=tid, evidence_ids=[e.evidence_id for e in evidence],
                contributing_metrics={e.metric_name: e.value for e in evidence}, previous_rhythm=prior,
                changes=changes, explanation=explanation))
            previous = current
        return assessments
