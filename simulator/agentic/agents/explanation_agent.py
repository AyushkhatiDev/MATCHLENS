from ..models import MatchAgentState, ExplanationResult
from ..tools import ToolSession
from ..grounding import render_statements, cited_evidence

class ExplanationAgent:
    async def run(self, state: MatchAgentState, tools: ToolSession) -> ExplanationResult:
        # Findings are explicitly handed off. Their claim IDs influence the
        # supporting observations; failed specialists supply no invented output.
        support = []
        for finding in [*state.tactical_findings,*state.player_findings,*state.context_findings]:
            support.extend(sid for sid in finding.statement_ids if sid not in ('trigger','change','consequence'))
        why_ids = ['consequence'] + list(dict.fromkeys(support))
        if any(s.statement_id=='contradiction' for s in state.statements) and 'contradiction' not in why_ids:
            why_ids.append('contradiction')
        ids = ['trigger','change'] + why_ids
        evidence_ids = cited_evidence(state,ids)
        tools.call('evidence.get_bundle',evidence_ids=evidence_ids)
        base = state.explanation_context
        return ExplanationResult(explanation_id='agent_'+base.explanation_id,moment_id=base.moment_id,
            what_happened=base.what_happened,what_changed=base.what_changed,
            why_it_matters=render_statements(state,why_ids),statement_ids=ids,evidence_ids=evidence_ids,
            confidence=state.confidence_cap,caveats=state.required_caveats.copy())
