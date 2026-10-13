from ..models import MatchAgentState, ContextFinding
from ..tools import ToolSession
from .common import finding_data

class ContextAgent:
    async def run(self, state: MatchAgentState, tools: ToolSession) -> ContextFinding:
        tools.call('match.get_state')
        tools.call('moment.get',moment_id=state.triggered_moment.moment_id)
        tools.call('moment.get_explanation_context',moment_id=state.triggered_moment.moment_id)
        ids = ['trigger','consequence']
        for e in state.evidence:
            if e.metric_name=='possession_sequence':
                tools.call('possession.get_sequence',sequence_id=e.facts['sequence_id'])
                ids.append('metric:'+e.evidence_id)
        return ContextFinding(**finding_data(state,tools,ids))
