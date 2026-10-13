"""Public deterministic rules; consumes Phase 2 values without recomputation."""
from simulator.intelligence.models import SignificantMoment, MomentType, RollingWindowMetrics, Evidence, StateTransition
from simulator.models.events import ShotEvent
from .models import ExplanationType, SignificanceBreakdown

TYPE_MAP = {
    MomentType.GOAL: ExplanationType.GOAL,
    MomentType.SHOT: ExplanationType.SHOT_THREAT,
    MomentType.SHOT_ON_TARGET: ExplanationType.SHOT_THREAT,
    MomentType.PRESSURE_SHIFT: ExplanationType.PRESSURE_SHIFT,
    MomentType.FIELD_TILT_SHIFT: ExplanationType.FIELD_TILT_SHIFT,
    MomentType.STATE_CHANGE: ExplanationType.STATE_CHANGE,
    MomentType.POSSESSION_SHIFT: ExplanationType.POSSESSION_SWING,
    MomentType.PLAYER_SPIKE: ExplanationType.PLAYER_ACTIVITY_SHIFT,
    MomentType.TACTICAL_SHIFT: ExplanationType.MAJOR_MOMENT,
}

def significance(moment: SignificantMoment, primary: Evidence,
                 transition: StateTransition | None = None,
                 shot: ShotEvent | None = None) -> SignificanceBreakdown:
    thresholds = {}
    reconstructed = None
    formula = "Phase 2 supplied score; no additional scoring factors inferred."
    if moment.moment_type == MomentType.GOAL:
        formula, reconstructed = "goal = 100", 100
    elif moment.moment_type == MomentType.SHOT_ON_TARGET and shot is not None:
        formula = "min(100, 50 + shot_quality * 30), rounded to 2 decimals"
        reconstructed = round(min(100, 50 + shot.shot_quality * 30), 2)
    elif moment.moment_type == MomentType.STATE_CHANGE and transition is not None:
        formula = "min(100, 35 + transition.magnitude * 50), rounded to 2 decimals"
        reconstructed = round(min(100, 35 + transition.magnitude * 50), 2)
    elif moment.moment_type == MomentType.PRESSURE_SHIFT and primary.delta is not None:
        formula = "min(100, 30 + abs(pressure delta)), rounded to 2 decimals"
        thresholds = {"absolute_pressure_delta_at_least": 15}
        reconstructed = round(min(100, 30 + abs(primary.delta)), 2)
    elif moment.moment_type == MomentType.FIELD_TILT_SHIFT and primary.delta is not None:
        formula = "min(100, 25 + abs(field tilt delta) * 1.5), rounded to 2 decimals"
        thresholds = {"absolute_field_tilt_delta_at_least": 12}
        reconstructed = round(min(100, 25 + abs(primary.delta) * 1.5), 2)
    if reconstructed is not None and abs(reconstructed - moment.significance_score) > .011:
        raise ValueError(f"Significance score disagrees with Phase 2 formula: {moment.moment_id}")
    return SignificanceBreakdown(score=moment.significance_score,
        contributing_factors=moment.reason_codes, thresholds_triggered=thresholds,
        evidence_ids=moment.evidence_ids, formula=formula, reconstructed_score=reconstructed)

def rhythm(w: RollingWindowMetrics) -> tuple[str, float, str | None]:
    if w.event_density < 5:
        return "LOW_EVENT", 70, None
    if w.turnovers >= 8 and w.event_density > 25:
        return "CHAOTIC", 85, None
    if w.turnovers >= 6:
        return "TRANSITION_HEAVY", 75, None
    for side in ("home", "away"):
        if (getattr(w, side + "_possession_pct") >= 60 and getattr(w, side + "_field_tilt") >= 60
            and getattr(w, side + "_passes") >= 5
            and getattr(w, side + "_pass_success") / getattr(w, side + "_passes") >= .8
            and w.turnovers <= 3):
            return "CONTROLLED", 80, side
    if w.event_density > 25:
        return "HIGH_TEMPO", 70, None
    if w.turnovers >= 4:
        return "CONTESTED", 60, None
    return "BALANCED", 50, None
