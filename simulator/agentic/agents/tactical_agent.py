from ..models import MatchAgentState, TacticalFinding
from ..tools import ToolSession
from .common import finding_data

class TacticalAgent:
    async def run(self, state: MatchAgentState, tools: ToolSession) -> TacticalFinding:
        team = next((e.team_id for e in state.evidence if e.team_id), None)
        if team:
            tools.call('team.get_pressure',team_id=team,window=300)
            tools.call('team.get_field_tilt',team_id=team,window=300)
            tools.call('team.get_progression',team_id=team,window=300)
        metrics = [s.statement_id for s in state.statements if s.category=='metric'
                   and any(e.evidence_id in s.evidence_ids and e.metric_name in
                           ('pressure_index','field_tilt','possession_pct','progressive_actions','pressure_count')
                           for e in state.evidence)]
        ids = metrics[:3] or ['consequence']
        contradictory = any(s.statement_id=='contradiction' for s in state.statements)
        if contradictory:
            ids.append('contradiction')
        return TacticalFinding(**finding_data(state,tools,ids),team_id=team,
            contradictions=['contradiction'] if contradictory else [])
