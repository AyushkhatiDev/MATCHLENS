"""Offline constraints/preflight regressions; no model request or cached live result."""
import asyncio
import json
import sys
from unittest.mock import patch
import pytest
from simulator.verify_foundry_demo import summarize,preflight_checks,main
from simulator.agentic.runtime.foundry import FoundryAgentRuntime
from simulator.agentic.grounding import EvidenceGroundingValidator,GroundingError,render_statements
from simulator.agentic.tools import ToolSession,MatchLensTools,ROLE_TOOLS
from tests.test_foundry_contract import config,response
from tests.test_agentic import store,results

def test_preflight_never_promotes_missing_or_partial_checks():
 assert not any(preflight_checks({}).values())
 checks={key:True for key in ('Project connection','Deployment/model availability','Tactical','Player','Context','Explanation','Grounding validation','Arbitration','Complete hero scenario','No mock fallback')}
 checks['Grounding validation']=False
 assert preflight_checks({'checks':checks})['Grounding'] is False
 assert preflight_checks({'checks':checks})['Project'] is True

def test_rehearsal_reports_rejection_failure_and_streak_honestly():
 runs=[{'success':True,'status':'FOUNDRY LIVE','fallback':False,'latency_seconds':20},
       {'success':False,'status':'FOUNDRY RESULT REJECTED','fallback':False,'latency_seconds':30,'specialist_failures':1},
       {'success':True,'status':'FOUNDRY LIVE','fallback':False,'latency_seconds':25}]
 result=summarize(runs)
 assert result['successes']==2 and result['failures']==result['rejections']==1
 assert result['specialist_failures']==1 and result['fallback_count']==0
 assert result['ending_success_streak']==result['longest_success_streak']==1
 assert result['latency_seconds']=={'min':20,'median':25,'max':30}

def test_failed_config_preflight_exits_nonzero_without_inference(monkeypatch,capsys):
 monkeypatch.setattr(sys,'argv',['verify','--preflight'])
 monkeypatch.setenv('AGENTIC_MODE','mock')
 assert main()==1
 out=capsys.readouterr().out
 assert 'FOUNDRY DEMO PRECHECK' in out and 'FAIL Project' in out and 'PASS' not in out

@pytest.mark.parametrize('repair',[False,True])
def test_explanation_copy_examples_and_rejection_remain_strict(store,results,repair):
 state=results['GOAL'].state;valid=state.explanation
 invalid=valid.model_copy(update={'why_it_matters':'Pressure caused 999 goals.'})
 class OfflineTransport:
  count=0
  async def get_response(self,messages,*,stream=False,options=None,**kwargs):
   self.count+=1
   payload=json.loads(next(m.text for m in messages if m.role=='user'))
   for example in payload['explanation_copy_examples']:
    assert example['why_it_matters']==render_statements(state,[sid for sid in example['statement_ids'] if sid not in ('trigger','change')])
    sample=valid.model_copy(update=example)
    EvidenceGroundingValidator().validate(sample,state)
   assert 'No new wording' in options['response_format'].model_fields['why_it_matters'].description
   tool=next(t for t in options['tools'] if t.name=='evidence_get_bundle')
   await tool.invoke(arguments={'evidence_ids':valid.evidence_ids},skip_parsing=True)
   return response(valid if repair and self.count==2 else invalid,options['response_format'])
 async def run():
  runtime=FoundryAgentRuntime(config());transport=OfflineTransport();runtime.client=transport
  session=ToolSession(MatchLensTools(store.scope(state.current_time)),ROLE_TOOLS['explanation'])
  if repair:assert await runtime.run('explanation',state,session)==valid
  else:
   with pytest.raises(GroundingError,match='unsupported numerical'):await runtime.run('explanation',state,session)
  assert transport.count==2
 asyncio.run(run())
