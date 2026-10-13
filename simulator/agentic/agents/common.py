"""Shared helpers for the five deterministic development agents."""
from ..models import MatchAgentState
from ..grounding import render_statements, cited_evidence
from ..tools import ToolSession

def finding_data(state: MatchAgentState, session: ToolSession, ids: list[str]) -> dict:
    evidence_ids = cited_evidence(state,ids)
    session.call('evidence.get_bundle',evidence_ids=evidence_ids)
    return dict(finding_type=state.explanation_context.explanation_type.value,
        finding=render_statements(state,ids), statement_ids=ids, evidence_ids=evidence_ids,
        confidence=state.confidence_cap,caveats=state.required_caveats.copy(), timestamp=state.current_time)
