"""Measured closed-catalogue grounding evaluation; not a semantic accuracy score."""
import asyncio
from collections import Counter
from simulator.generation.generator import generate_match
from simulator.engine.match_engine import MatchEngine
from simulator.intelligence.engine import IntelligenceEngine
from simulator.intelligence.explanation import ExplanationEngine
from simulator.agentic import AgenticConfig,IntelligenceStore,MatchAgentRunner
from simulator.agentic.grounding import EvidenceGroundingValidator,GroundingError
from simulator.agentic.runtime.mock import MockAgentRuntime
from simulator.agentic.models import ExplanationResult,NarrativeResult
from .observability import Observability

class FailedSpecialist(MockAgentRuntime):
    async def run(self,role,state,tools):
        if role=='tactical':raise TimeoutError('Injected specialist timeout')
        return await super().run(role,state,tools)

def samples(seeds=(99,42,17,123,7)):
    for seed in seeds:
        match=generate_match(seed=seed);events=MatchEngine(match).simulate()
        intelligence=IntelligenceEngine(match,events).analyze()
        report=ExplanationEngine(source_events=events).explain(intelligence)
        yield seed,IntelligenceStore(intelligence,report,events),report

async def evaluate(limit=50):
    observer=Observability();cases=[];selected=[]
    for seed,store,report in samples():
        runner=observer.runner(store,AgenticConfig(mode='mock'))
        try:
            for explanation in report.explanations:
                result=await runner.process_moment(explanation.moment_id)
                valid=result.explanation is not None and result.narrative is not None and result.state.explanation_validated
                if valid:
                    EvidenceGroundingValidator().validate(result.explanation,result.state)
                    EvidenceGroundingValidator().validate(result.narrative,result.state)
                    ExplanationResult.model_validate(result.explanation.model_dump())
                    NarrativeResult.model_validate(result.narrative.model_dump())
                cases.append(dict(seed=seed,moment_id=result.moment_id,type=explanation.explanation_type.value,
                    status=result.workflow_status,grounded=valid,schema_valid=valid,trace_id=result.trace_id))
                selected.append((store,result))
                if len(cases)>=limit:break
        finally:await runner.aclose()
        if len(cases)>=limit:break
    store,result=next((s,r) for s,r in selected if r.state.player_findings and 'tactical' in r.selected_agents)
    validator=EvidenceGroundingValidator();negative=[]
    def reject(name,output,state):
        try:validator.validate(output,state)
        except GroundingError:negative.append({'test':name,'rejected':True});return
        negative.append({'test':name,'rejected':False})
    for name,field,value in [('unsupported_number','why_it_matters','A new metric is 999999.'),
                             ('unsupported_causality','why_it_matters','The earlier pass caused the goal.'),
                             ('confidence_inflation','confidence',1.0),('invalid_evidence','evidence_ids',['missing'])]:
        output=result.explanation.model_copy(deep=True);setattr(output,field,value);reject(name,output,result.state)
    finding=result.state.player_findings[0]
    for name,field,value in [('unknown_player','player_id','unknown'),('unknown_team','team_id','unknown'),('unknown_event','event_ids',['unknown'])]:
        output=finding.model_copy(deep=True);setattr(output,field,value);reject(name,output,result.state)
    output=result.narrative.model_copy(deep=True);output.summary='A different ungrounded story.';reject('narrative_inconsistency',output,result.state)
    contradiction=next((r for _,r in selected if any(s.statement_id=='contradiction' for s in r.state.statements)),None)
    if contradiction:
        output=contradiction.narrative.model_copy(deep=True);output.statement_ids.remove('contradiction')
        from simulator.agentic.grounding import render_statements
        output.summary=render_statements(contradiction.state,output.statement_ids)
        reject('omitted_contradiction',output,contradiction.state)
    try:ExplanationResult.model_validate({'what_happened':'incomplete'})
    except ValueError:negative.append({'test':'invalid_structure','rejected':True})
    runner=MatchAgentRunner(store,AgenticConfig(mode='mock'),runtime=FailedSpecialist())
    try:partial=await runner.process_moment(result.moment_id)
    finally:await runner.aclose()
    failure={'injected':True,'type':'specialist_timeout','status':partial.workflow_status,
        'missing_agents':partial.missing_agents,'confidence_reduced':partial.confidence<result.confidence,
        'validated_explanation':partial.state.explanation_validated}
    return dict(environment='local',runtime='mock',requested_cases=limit,cases=len(cases),
        grounded=sum(c['grounded'] for c in cases),schema_valid=sum(c['schema_valid'] for c in cases),
        statuses=dict(Counter(c['status'] for c in cases)),negative_controls=negative,specialist_failure=failure,
        results=cases,traces=list(observer.traces),scope='Exact approved-statement grounding, reference validation and confidence/caveat preservation. No independent football semantic accuracy or model quality is measured.')
