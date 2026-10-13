from ..models import MatchAgentState, PlayerFinding
from ..tools import ToolSession
from .common import finding_data

class PlayerAgent:
    async def run(self, state: MatchAgentState, tools: ToolSession) -> PlayerFinding:
        player = state.explanation_context.affected_player_ids[0]
        tools.call('player.get_activity',player_id=player,window=300)
        ids = ['player:'+player]
        for e in state.evidence:
            if e.metric_name=='shot_quality' or e.player_id==player:
                ids.append('metric:'+e.evidence_id)
                if e.metric_name=='shot_quality' and e.source_event_ids:
                    tools.call('shot.get_context',event_id=e.source_event_ids[0])
        return PlayerFinding(**finding_data(state,tools,ids),player_id=player,
            event_ids=state.explanation_context.goal_context.direct_event_ids
                if state.explanation_context.goal_context else [])
