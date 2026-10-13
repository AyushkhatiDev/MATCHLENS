from ..models import MatchAgentState, NarrativeResult
from ..grounding import GroundingError, render_statements, cited_evidence
from ..tools import ToolSession

class NarrativeAgent:
    async def run(self, state: MatchAgentState, tools: ToolSession) -> NarrativeResult:
        if not state.explanation_validated or state.explanation is None:
            raise GroundingError('Narrative handoff requires a validated explanation.')
        ids = ['trigger'] if state.explanation.what_happened==state.explanation.what_changed else ['trigger','change']
        if state.audience_profile.audience=='analyst' or state.audience_profile.duration_hint=='standard':
            ids += [sid for sid in state.explanation.statement_ids if sid not in ids]
        elif 'consequence' in state.explanation.statement_ids:
            ids.append('consequence')
        if 'contradiction' in state.explanation.statement_ids and 'contradiction' not in ids:
            ids.append('contradiction')
        evidence_ids = cited_evidence(state,ids)
        tools.call('evidence.get_bundle',evidence_ids=evidence_ids)
        return NarrativeResult(headline=state.explanation_context.explanation_type.value.replace('_',' ').title(),
            summary=render_statements(state,ids),audience=state.audience_profile.audience,
            duration_hint=state.audience_profile.duration_hint,statement_ids=ids,evidence_ids=evidence_ids,
            confidence=state.explanation.confidence,caveats=state.required_caveats.copy())
