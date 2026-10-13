"""Offline execution contracts; fake failures never reach Azure or weaken grounding."""
import asyncio
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from simulator.agentic.config import AgenticConfig
from simulator.agentic.models import AgentError
from simulator.presentation.execution import pending, projection
from simulator.presentation.service import MatchService
from simulator.presentation.temporal import truth_projection
from simulator.production.config import AppConfig
from simulator.realtime.models import EventEnvelope,parse_event
from tests.test_agentic import store,results

PATH=Path('data/generated/phase5/seed99/match.jsonl')
FOUNDRY=AgenticConfig(mode='foundry',project_endpoint='https://example.services.ai.azure.com/api/projects/existing',model='existing')

def test_mock_cannot_be_live(results):
    execution=projection(results['GOAL'],[])
    assert execution.status=='MOCK' and not execution.fallback

def test_pending_is_not_live():
    execution=pending('foundry')
    assert execution.status=='FOUNDRY UNAVAILABLE' and execution.state=='PENDING'

@pytest.mark.parametrize('code,status',[('TIMEOUT','FOUNDRY UNAVAILABLE'),('INVALID_EVIDENCE','FOUNDRY RESULT REJECTED')])
def test_failure_status_and_no_fallback(results,code,status):
    result=results['GOAL'].model_copy(deep=True)
    result.runtime_mode='foundry';result.workflow_status='FAILED';result.state.explanation_validated=False;result.explanation=None
    result.errors=[AgentError(agent='explanation',stage='Validation',code=code,message='Withheld',recoverable=False)]
    execution=projection(result,[])
    assert execution.status==status and execution.mode=='foundry' and not execution.fallback

def test_observed_success_and_safe_projection(results):
    result=results['GOAL'].model_copy(deep=True);result.runtime_mode='foundry'
    # Explicit offline projection fixture, not a claimed live acceptance run.
    execution=projection(result,[{'trace_id':result.trace_id,'agent':'Validation','status':'OK','duration_ms':1.25,'arguments':{'private':'never public'}}])
    assert execution.status=='FOUNDRY LIVE'
    assert next(s for s in execution.steps if s.stage=='Grounding validation').duration_ms==1.25
    assert 'arguments' not in execution.model_dump_json()
    truth=truth_projection(result)
    assert all(s.validation_state=='SUPPORTED' for s in truth.specialists)
    assert all(set(c.evidence_ids)<={e.evidence_id for e in result.state.evidence} for c in truth.claims)

@pytest.mark.parametrize('kwargs',[{'agent':AgenticConfig()},{'environment':'azure','demo_controls':False,'frontend_origins':['https://example.com']}])
def test_hero_profile_is_controlled(kwargs):
    with pytest.raises(ValueError):AppConfig(foundry_hero_only=True,**kwargs)

def test_unavailable_hero_preserves_truth_and_does_not_invoke_mock():
    async def run():
        service=MatchService(PATH,AppConfig(environment='demo',agent=FOUNDRY,foundry_hero_only=True))
        called=[]
        def unavailable(store,config):
            called.append(config.mode)
            raise RuntimeError('Do not expose this input')
        service.processor.runner_factory=unavailable
        try:
            for index,line in enumerate(PATH.read_text().splitlines()):
                event=parse_event(json.loads(line));env=EventEnvelope.wrap(event,index)
                status=await service.processor.process(env);await service.on_event(env,status)
                if event.event_id==service.hero_event_id:break
            assert service.clock==1274.43 and service.score==(1,1)
            await asyncio.gather(*list(service.workflow_tasks))
            hero=next(i for i in service.insights.values() if i.moment.moment_type.value=='GOAL' and service.hero_event_id in i.moment.source_event_ids)
            assert called==['foundry']
            assert hero.execution.status=='FOUNDRY UNAVAILABLE' and not hero.execution.fallback
            assert hero.explanation and hero.evidence and hero.change and hero.source_events
            assert hero.agent_explanation is None and hero.narrative is None
            assert all(i.execution.mode=='deterministic' for i in service.insights.values() if i.explanation and i is not hero)
        finally:await service.close()
    asyncio.run(run())
