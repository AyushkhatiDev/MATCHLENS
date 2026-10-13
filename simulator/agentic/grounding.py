"""Claim-level grounding: cited statement identity, not a loose numeric whitelist.

Model reasoning selects/orderings of approved factual statements. All football
claims remain deterministically renderable; arbitrary prose is rejected. This
is stricter than token-matching and intentionally limits editorial freedom.
"""
from simulator.intelligence.explanation.models import MomentExplanation, TraceEvidence
from .models import (EvidenceStatement, MatchAgentState, Finding, TacticalFinding,
                     ExplanationResult, NarrativeResult, PlayerFinding)

class GroundingError(ValueError):
    pass

def _number(value: float) -> str:
    return f'{value:.4f}'.rstrip('0').rstrip('.')

def statement_catalog(base: MomentExplanation, evidence: list[TraceEvidence]) -> list[EvidenceStatement]:
    statements = [
        EvidenceStatement(statement_id='trigger',text=base.what_happened,
                          evidence_ids=base.evidence_ids,category='trigger'),
        EvidenceStatement(statement_id='change',text=base.what_changed,
                          evidence_ids=base.evidence_ids,category='change'),
        EvidenceStatement(statement_id='why',text=base.why_it_matters,
                          evidence_ids=base.evidence_ids,category='interpretation'),
        EvidenceStatement(statement_id='consequence',
            text=base.context['trigger_context_change_consequence']['consequence'],
            evidence_ids=base.evidence_ids,category='interpretation'),
    ]
    for e in evidence:
        metric = base.supporting_metrics[e.evidence_id]
        who = e.player_id or e.team_id or 'Match'
        if e.metric_name == 'possession_sequence':
            text = (f"The linked sequence lasted {_number(e.value)} seconds, with {e.facts['passes']} passes, "
                    f"{e.facts['progressive_actions']} progressive actions and {e.facts['pressure_received']} pressure events.")
            category = 'context'
        elif e.baseline_value is not None:
            text = (f"{who} {e.metric_name} moved from {_number(e.baseline_value)} to {_number(e.value)}, "
                    f"an absolute change of {_number(e.delta)} {metric.unit}.")
            category = 'metric'
        else:
            text = f'{who} {e.metric_name} was {_number(e.value)}.'
            category = 'metric'
        statements.append(EvidenceStatement(statement_id='metric:'+e.evidence_id,text=text,
            evidence_ids=[e.evidence_id],category=category))
    for pid in base.affected_player_ids:
        statements.append(EvidenceStatement(statement_id='player:'+pid,
            text=f'Player {pid} is linked to this moment.',evidence_ids=base.evidence_ids,category='player'))
    if base.context['confidence_factors']['conflicting_directions']:
        statements.append(EvidenceStatement(statement_id='contradiction',
            text='The metrics move in different directions; the evidence does not support a single-team control conclusion.',
            evidence_ids=base.evidence_ids,category='contradiction'))
    return statements

def render_statements(state: MatchAgentState, ids: list[str]) -> str:
    catalog = {s.statement_id:s for s in state.statements}
    if len(set(ids)) != len(ids) or not set(ids)<=catalog.keys():
        raise GroundingError('Unknown or duplicate statement IDs.')
    return ' '.join(catalog[sid].text for sid in ids)

def cited_evidence(state: MatchAgentState, ids: list[str]) -> list[str]:
    catalog = {s.statement_id:s for s in state.statements}
    if not set(ids)<=catalog.keys():
        raise GroundingError('Unknown statement IDs.')
    return sorted({eid for sid in ids for eid in catalog[sid].evidence_ids})

class EvidenceGroundingValidator:
    def validate(self, output: Finding | ExplanationResult | NarrativeResult,
                 state: MatchAgentState) -> None:
        ledger = {e.evidence_id:e for e in state.evidence}
        if len(set(output.evidence_ids))!=len(output.evidence_ids) or not set(output.evidence_ids)<=ledger.keys():
            raise GroundingError('Unknown, future or duplicate evidence IDs.')
        required = cited_evidence(state,output.statement_ids)
        if not set(required)<=set(output.evidence_ids):
            raise GroundingError('Selected claims are missing their evidence citations.')
        if output.confidence > state.confidence_cap+.000001:
            raise GroundingError('Confidence exceeds the deterministic evidence cap.')
        if set(state.required_caveats)!=set(output.caveats) or len(set(output.caveats))!=len(output.caveats):
            raise GroundingError('Deterministic caveats were dropped or unsupported caveat claims were added.')
        base = state.explanation_context
        if isinstance(output,Finding):
            if output.finding != render_statements(state,output.statement_ids):
                raise GroundingError('Finding text is not traceable to the selected claims.')
            if output.timestamp != state.current_time or output.finding_type!=base.explanation_type.value:
                raise GroundingError('Fabricated timestamp or finding type.')
            teams = {ledger[eid].team_id for eid in output.evidence_ids if ledger[eid].team_id}
            if output.team_id and output.team_id not in teams:
                raise GroundingError('Unknown or unsupported team ID.')
            if output.player_id and output.player_id not in base.affected_player_ids:
                raise GroundingError('Unknown or unsupported player ID.')
            if not set(output.event_ids)<=set(base.source_event_ids):
                raise GroundingError('Unknown or unsupported event ID.')
            if isinstance(output,PlayerFinding):
                if output.player_id not in base.affected_player_ids or 'player:'+output.player_id not in output.statement_ids:
                    raise GroundingError('Player finding requires the linked player and its cited statement.')
            if isinstance(output,TacticalFinding):
                expected = ['contradiction'] if any(s.statement_id=='contradiction' for s in state.statements) else []
                if output.contradictions!=expected:
                    raise GroundingError('Contradictory metrics must remain explicit.')
        elif isinstance(output,ExplanationResult):
            if output.explanation_id!='agent_'+base.explanation_id or output.moment_id!=base.moment_id:
                raise GroundingError('Explanation identity does not match the triggered moment.')
            if output.what_happened!=base.what_happened or output.what_changed!=base.what_changed:
                raise GroundingError('Trigger facts or metric values were altered.')
            if not {'trigger','change'}<=set(output.statement_ids):
                raise GroundingError('Trigger and change claims require citations.')
            if not ({'why','consequence'} & set(output.statement_ids)):
                raise GroundingError('Explanation is missing its supported interpretation.')
            why_ids=[sid for sid in output.statement_ids if sid not in ('trigger','change')]
            if output.why_it_matters!=render_statements(state,why_ids):
                raise GroundingError('Explanation contains unsupported numerical or football claims.')
        else:
            if not state.explanation_validated or state.explanation is None:
                raise GroundingError('Narrative requires a validated explanation.')
            if output.confidence != state.explanation.confidence:
                raise GroundingError('Narrative cannot change confidence in the underlying explanation.')
            if output.headline not in (base.title,base.explanation_type.value.replace('_',' ').title()):
                raise GroundingError('Unsupported narrative headline.')
            if output.audience!=state.audience_profile.audience or output.duration_hint!=state.audience_profile.duration_hint:
                raise GroundingError('Narrative presentation profile mismatch.')
            if not set(output.statement_ids)<=set(state.explanation.statement_ids):
                raise GroundingError('Narrative attempted new analysis beyond the validated explanation.')
            if output.summary!=render_statements(state,output.statement_ids):
                raise GroundingError('Narrative contains unsupported claims.')
        # Preserve contradictions in every downstream interpretation. The Phase 3
        # confidence already penalizes mixed metrics; agents cannot raise it.
        if any(s.statement_id=='contradiction' for s in state.statements):
            if isinstance(output,(ExplanationResult,NarrativeResult,TacticalFinding)) and 'contradiction' not in output.statement_ids:
                raise GroundingError('Conflicting evidence was omitted.')
