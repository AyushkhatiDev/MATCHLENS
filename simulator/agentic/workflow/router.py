"""Routing is a deterministic product policy, never delegated to a model."""
from ..models import Role
from simulator.intelligence.explanation.models import MomentExplanation

def route_moment(explanation: MomentExplanation) -> list[Role]:
    kind = explanation.explanation_type.value
    roles: list[Role] = []
    if kind in ('GOAL','PRESSURE_SHIFT','FIELD_TILT_SHIFT','STATE_CHANGE','POSSESSION_SWING',
                'PROGRESSION_SURGE','HIGH_PRESSURE_PHASE','TRANSITION_PHASE','CONTROL_SHIFT','CHAOS_PHASE','MAJOR_MOMENT'):
        roles.append('tactical')
    if kind in ('GOAL','SHOT_THREAT','PLAYER_ACTIVITY_SHIFT') and explanation.affected_player_ids:
        roles.append('player')
    roles.append('context')
    return roles
