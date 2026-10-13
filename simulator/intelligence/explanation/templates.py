"""Closed templates: no free-form or randomly assembled football claims."""
from string import Formatter
from .models import Model, ExplanationType

class ExplanationTemplate(Model):
    explanation_type: ExplanationType
    title_template: str
    what_happened_template: str
    what_changed_template: str
    why_it_matters_template: str

    def render(self, **values: str) -> dict[str, str]:
        result = {}
        for field in ("title", "what_happened", "what_changed", "why_it_matters"):
            template = getattr(self, field + "_template")
            for _, key, spec, conversion in Formatter().parse(template):
                if key and (not key.isidentifier() or spec or conversion or key not in values):
                    raise ValueError("Unsafe or missing template variable")
            result[field] = template.format_map(values)
        return result

TEMPLATES = {
    kind: ExplanationTemplate(
        explanation_type=kind,
        title_template="{subject}: {label}",
        what_happened_template="{trigger}",
        what_changed_template="{change}",
        why_it_matters_template="{consequence} {support}",
    ) for kind in ExplanationType
}

CONSEQUENCES = {
    ExplanationType.GOAL: "The goal changed the recorded scoreline.",
    ExplanationType.SHOT_THREAT: "The shot represents an attacking attempt; shot quality is a MatchLens heuristic.",
    ExplanationType.PRESSURE_SHIFT: "The change describes a different level of measured pressing activity. This interpretation does not assign causal credit.",
    ExplanationType.FIELD_TILT_SHIFT: "The change indicates a redistribution of attacking-third actions, rather than a change in time-based possession.",
    ExplanationType.STATE_CHANGE: "The transition marks a change in the Phase 2 tactical classification.",
    ExplanationType.POSSESSION_SWING: "The change describes the share of team-attributed events in the rolling window.",
    ExplanationType.PLAYER_ACTIVITY_SHIFT: "The change identifies a different level of recorded player involvement.",
    ExplanationType.PROGRESSION_SURGE: "The change identifies a different level of forward actions.",
    ExplanationType.HIGH_PRESSURE_PHASE: "The transition identifies a phase with unequal measured pressure.",
    ExplanationType.TRANSITION_PHASE: "The transition identifies a phase with frequent possession changes.",
    ExplanationType.CONTROL_SHIFT: "The transition identifies a change in measured territorial and possession control.",
    ExplanationType.CHAOS_PHASE: "The transition identifies frequent possession changes alongside high event density.",
    ExplanationType.MAJOR_MOMENT: "The Phase 2 evidence identifies a significant change in match context.",
}
