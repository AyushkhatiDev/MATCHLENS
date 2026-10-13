"""Offline Phase 4 contracts, actual framework graph, grounding and MCP protocol."""
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch
import pytest
from pydantic import ValidationError

pytest.importorskip('agent_framework')
pytest.importorskip('mcp')
from simulator.generation.generator import generate_match
from simulator.engine.match_engine import MatchEngine
from simulator.intelligence.engine import IntelligenceEngine
from simulator.intelligence.explanation import ExplanationEngine
from simulator.agentic import (AgenticConfig, IntelligenceStore, MatchLensTools, MatchAgentRunner,
                               AudienceProfile, WorkflowResult)
from simulator.agentic.grounding import EvidenceGroundingValidator, GroundingError
from simulator.agentic.models import ExplanationResult, TacticalFinding, NarrativeResult
from simulator.agentic.runtime import MockAgentRuntime, FoundryAgentRuntime
from simulator.agentic.tools import ToolSession, TOOL_METHODS, ROLE_TOOLS, ToolError
from simulator.agentic.mcp.server import create_server
from simulator.agentic.workflow.router import route_moment

@pytest.fixture(scope='module')
def store():
    match=generate_match(seed=99)
    events=MatchEngine(match).simulate()
    intel=IntelligenceEngine(match,events).analyze()
    report=ExplanationEngine(source_events=events).explain(intel)
    return IntelligenceStore(intel,report,events)

@pytest.fixture(scope='module')
def results(store):
    async def run():
        runner=MatchAgentRunner(store,AgenticConfig())
        values={}
        for kind in ['GOAL','PRESSURE_SHIFT','FIELD_TILT_SHIFT','STATE_CHANGE','SHOT_THREAT']:
            moment=next(e for e in store.report.explanations if e.explanation_type.value==kind)
            values[kind]=await runner.process_moment(moment.moment_id)
        await runner.aclose()
        return values
    return asyncio.run(run())

@pytest.mark.parametrize('kind', ['GOAL','PRESSURE_SHIFT','FIELD_TILT_SHIFT','STATE_CHANGE','SHOT_THREAT'])
def test_end_to_end_complete(results,kind):
    result=results[kind]
    assert result.workflow_status=='COMPLETE'
    assert result.explanation and result.narrative
    assert result.state.explanation_validated
    assert not result.errors
    assert all(record.status=='OK' and record.tool_calls and record.evidence_ids
               for record in result.state.agent_executions)
    assert result.final_explanation_id==result.explanation.explanation_id
    assert WorkflowResult.model_validate_json(result.model_dump_json())==result
    v=EvidenceGroundingValidator()
    v.validate(result.explanation,result.state)
    v.validate(result.narrative,result.state)

@pytest.mark.parametrize('kind,roles', [
    ('GOAL',['tactical','player','context']), ('PRESSURE_SHIFT',['tactical','context']),
    ('FIELD_TILT_SHIFT',['tactical','context']), ('STATE_CHANGE',['tactical','context']),
    ('SHOT_THREAT',['player','context']), ('PLAYER_ACTIVITY_SHIFT',['player','context'])])
def test_routing(store,kind,roles):
    base=store.report.explanations[0].model_copy(deep=True)
    from simulator.intelligence.explanation.models import ExplanationType
    base.explanation_type=ExplanationType(kind)
    base.affected_player_ids=[store.intelligence.players[0].player_id]
    assert route_moment(base)==roles
    assert route_moment(base)==route_moment(base)

def test_trace_stage_order(results):
    result=results['GOAL']
    assert [t.stage for t in result.state.trace_steps]==[
        'Router','Specialists','EvidenceConsolidation','ExplanationAgent','Validation','NarrativeAgent','FinalResult']
    assert [a.agent for a in result.state.agent_executions]==['tactical','player','context','explanation','narrative']
    assert result.trace_id.startswith('trace_')
    assert result.match_id and result.moment_id

def test_same_input_identical_mock_result(store,results):
    result=asyncio.run(MatchAgentRunner(store,AgenticConfig()).process_moment(results['GOAL'].moment_id))
    assert result.model_dump_json()==results['GOAL'].model_dump_json()

def test_original_sources_immutable(store,results):
    before=store.report.model_dump_json()
    asyncio.run(MatchAgentRunner(store,AgenticConfig()).process_moment(results['GOAL'].moment_id))
    assert store.report.model_dump_json()==before

def test_agents_do_not_recalculate_metrics(store,results):
    with patch.object(IntelligenceEngine,'analyze',side_effect=AssertionError('Do not recalculate')):
        t=MatchLensTools(store)
        assert t.get_match_summary()['summary']==store.intelligence.summary.model_dump(mode='json')
        assert t.get_team_summary(store.intelligence.home_team.team_id)==store.intelligence.home_team.model_dump(mode='json')
        assert t.get_player_summary(store.intelligence.players[0].player_id)==store.intelligence.players[0].model_dump(mode='json')
        r=asyncio.run(MatchAgentRunner(store,AgenticConfig()).process_moment(results['PRESSURE_SHIFT'].moment_id))
        assert r.workflow_status=='COMPLETE'

@pytest.mark.parametrize('method,metric', [('get_pressure_metrics','pressure_index'),
    ('get_field_tilt','field_tilt'), ('get_progression_metrics','progressive_actions')])
def test_actual_rolling_values(store,method,metric):
    t=MatchLensTools(store.scope(900))
    r=getattr(t,method)(store.intelligence.home_team.team_id,300)
    w=max((w for w in store.intelligence.rolling_windows if w.timestamp<=900 and w.window_seconds==300),
          key=lambda w:w.timestamp)
    assert r['timestamp']==w.timestamp
    assert r['metrics'][metric]==getattr(w,'home_'+metric)

@pytest.mark.parametrize('name,args', [
    ('team.get_pressure',{'team_id':'team_00','window':299}),
    ('team.get_field_tilt',{'team_id':1}), ('evidence.get_bundle',{'evidence_ids':[]}),
    ('events.get_recent',{'start_time':-1,'end_time':100}),
    ('timeline.get',{'start_time':200,'end_time':100}),
    ('events.get_recent',{'start_time':0,'end_time':100,'limit':201}),
    ('match.get_summary',{'unexpected':True})])
def test_tool_argument_validation(store,name,args):
    s=ToolSession(MatchLensTools(store))
    with pytest.raises(ToolError):
        s.call(name,**args)
    assert s.calls[-1].status=='ERROR' and s.calls[-1].arguments=={}

@pytest.mark.parametrize('method,args', [
    ('get_team_summary',('ghost',)), ('get_player_summary',('ghost',)),
    ('get_evidence',('ghost',)), ('get_moment',('ghost',)),
    ('get_possession_sequence',('ghost',)), ('get_shot_context',('ghost',))])
def test_unknown_tool_ids(store,method,args):
    with pytest.raises(ToolError):
        getattr(MatchLensTools(store),method)(*args)

@pytest.mark.parametrize('method,args', [('get_match_summary',()),
    ('get_team_summary',('team_00',)), ('get_player_summary',('team_00_p00',))])
def test_future_aggregates_withheld(store,method,args):
    with pytest.raises(ToolError,match='later snapshot'):
        getattr(MatchLensTools(store.scope(600)),method)(*args)

def test_recent_events_pagination_and_future_guard(store):
    tools=MatchLensTools(store.scope(600))
    first=tools.get_recent_events(0,600,limit=2)
    assert len(first['events'])==2 and first['next_offset']==2
    nextpage=tools.get_recent_events(0,600,limit=2,offset=2)
    assert nextpage['events'][0]['event_id']!=first['events'][0]['event_id']
    with pytest.raises(ToolError,match='cutoff'):
        tools.get_recent_events(0,601)
    with pytest.raises(ToolError):
        store.scope(store.current_time+1)

def test_timeline_future_state_after_redacted(store):
    result=MatchLensTools(store.scope(900)).get_timeline(0,900)
    assert all(entry['timestamp']<=900 and entry['state_after'] is None for entry in result['timeline'])

def test_sequence_and_shot_tools(store,results):
    goal=results['GOAL'].state.explanation_context.goal_context
    tools=MatchLensTools(store.scope(goal.goal_time))
    sequence=tools.get_possession_sequence(goal.preceding_sequence_id)
    assert sequence['duration_s']==goal.sequence_duration
    shot=next(e for e in store.events if e.event_id in goal.direct_event_ids and e.event_type.value=='SHOT')
    assert tools.get_shot_context(shot.event_id)==shot.model_dump(mode='json')
    recent=tools.get_recent_possession_sequences(goal.scoring_team,300)
    assert any(s['sequence_id']==goal.preceding_sequence_id for s in recent['sequences'])

def test_player_activity_does_not_fabricate_rolling_metrics(store,results):
    goal=results['GOAL'].state.explanation_context.goal_context
    r=MatchLensTools(store.scope(goal.goal_time)).get_player_activity(goal.scorer)
    assert r['rolling_player_metrics'] is None
    assert r['events'] and all(e['timestamp']<=goal.goal_time for e in r['events'])

def test_bundle_context_and_moment_data(store,results):
    r=results['GOAL']
    t=MatchLensTools(store.scope(r.state.current_time))
    assert t.get_moment(r.moment_id)==r.state.triggered_moment.model_dump(mode='json')
    assert t.get_explanation_context(r.moment_id)==r.state.explanation_context.model_dump(mode='json')
    bundle=t.get_evidence_bundle(r.evidence_ids)
    assert [e['evidence_id'] for e in bundle['evidence']]==r.evidence_ids

@pytest.mark.parametrize('field,value', [
    ('evidence_ids',['ghost']), ('why_it_matters','Pressure caused a goal.'),
    ('what_happened','Player Messi scored.'), ('what_changed','Pressure increased by 12345.'),
    ('moment_id','unknown_moment'), ('statement_ids',['metric:unknown']),
    ('confidence',1), ('caveats',[])])
def test_explanation_hallucinations_rejected(results,field,value):
    result=results['STATE_CHANGE']
    output=result.explanation.model_copy(update={field:value})
    with pytest.raises(GroundingError):
        EvidenceGroundingValidator().validate(output,result.state)

@pytest.mark.parametrize('field,value', [('team_id','ghost'),('player_id','ghost'),
    ('event_ids',['ghost']),('timestamp',99999),('finding_type','unsupported_metric'),
    ('finding','team_00 pressure_index was 9876.'),('contradictions',[])])
def test_specialist_unknown_claims_rejected(results,field,value):
    result=results['STATE_CHANGE']
    output=result.state.tactical_findings[0].model_copy(update={field:value})
    with pytest.raises(GroundingError):
        EvidenceGroundingValidator().validate(output,result.state)

def test_numbers_with_wrong_metric_or_team_rejected(results):
    r=results['PRESSURE_SHIFT']
    text=r.explanation.what_changed.replace('pressure_index','field_tilt')
    output=r.explanation.model_copy(update={'what_changed':text})
    with pytest.raises(GroundingError):
        EvidenceGroundingValidator().validate(output,r.state)

@pytest.mark.parametrize('field,value', [('summary','A dominant team caused this goal.'),
    ('headline','Premier League champion'),('evidence_ids',['fake']),
    ('statement_ids',['metric:unvalidated']),('audience','analyst')])
def test_narrative_hallucination_rejected(results,field,value):
    r=results['GOAL']
    with pytest.raises(GroundingError):
        EvidenceGroundingValidator().validate(r.narrative.model_copy(update={field:value}),r.state)

def test_narrative_cannot_run_before_validation(results):
    r=results['GOAL']
    state=r.state.model_copy(update={'explanation_validated':False})
    with pytest.raises(GroundingError):
        EvidenceGroundingValidator().validate(r.narrative,state)

class FailingRuntime(MockAgentRuntime):
    def __init__(self,failed):
        self.failed=set(failed)
    async def run(self,role,state,tools):
        if role in self.failed:
            raise RuntimeError('TOP_SECRET_TOKEN must not be logged')
        return await super().run(role,state,tools)

@pytest.mark.parametrize('failed,status', [(['player'],'PARTIAL'), (['tactical'],'PARTIAL'),
    (['context'],'PARTIAL'), (['tactical','player','context'],'FAILED'),
    (['explanation'],'FAILED'), (['narrative'],'PARTIAL')])
def test_failure_recovery_and_redaction(store,results,failed,status):
    runner=MatchAgentRunner(store,AgenticConfig(),FailingRuntime(failed))
    output=asyncio.run(runner.process_moment(results['GOAL'].moment_id))
    assert output.workflow_status==status
    assert output.missing_agents==[role for role in ['tactical','player','context','explanation','narrative'] if role in failed]
    assert 'TOP_SECRET_TOKEN' not in output.model_dump_json()
    assert output.errors
    if status=='PARTIAL':
        assert output.explanation
        assert output.explanation.confidence<results['GOAL'].explanation.confidence
        assert all(any(role+' agent unavailable' in c for c in output.explanation.caveats) for role in failed)
    else:
        assert output.narrative is None

def test_specialist_schema_failure_not_silently_accepted(store,results):
    class Invalid(MockAgentRuntime):
        async def run(self,role,state,tools):
            if role=='player':
                return {'finding':'not a valid model'}
            return await super().run(role,state,tools)
    output=asyncio.run(MatchAgentRunner(store,AgenticConfig(),Invalid()).process_moment(results['GOAL'].moment_id))
    assert output.workflow_status=='PARTIAL' and 'player' in output.missing_agents

def test_invalid_explanation_blocks_narrative(store,results):
    class Malicious(MockAgentRuntime):
        async def run(self,role,state,tools):
            value=await super().run(role,state,tools)
            return value.model_copy(update={'why_it_matters':'Pressure caused 999 goals.'}) if role=='explanation' else value
    output=asyncio.run(MatchAgentRunner(store,AgenticConfig(),Malicious()).process_moment(results['GOAL'].moment_id))
    assert output.workflow_status=='FAILED'
    assert output.explanation is None and output.narrative is None
    assert not any(a.agent=='narrative' for a in output.state.agent_executions)
    assert any(t.stage=='Validation' and t.status=='ERROR' for t in output.state.trace_steps)

def test_timeout_recovery(store,results):
    class Slow(MockAgentRuntime):
        async def run(self,role,state,tools):
            if role=='player':
                await asyncio.Event().wait()
            return await super().run(role,state,tools)
    output=asyncio.run(MatchAgentRunner(store,AgenticConfig(timeout_seconds=.1),Slow()).process_moment(results['GOAL'].moment_id))
    assert output.workflow_status=='PARTIAL'
    assert any(e.code=='TIMEOUT' for e in output.errors)

def test_independent_specialists_are_concurrent(store,results):
    class Concurrent(MockAgentRuntime):
        def __init__(self):
            self.started=set()
            self.barrier=asyncio.Event()
        async def run(self,role,state,tools):
            if role in ('tactical','player','context'):
                self.started.add(role)
                if len(self.started)==3:
                    self.barrier.set()
                await asyncio.wait_for(self.barrier.wait(),1)
            return await super().run(role,state,tools)
    output=asyncio.run(MatchAgentRunner(store,AgenticConfig(),Concurrent()).process_moment(results['GOAL'].moment_id))
    assert output.workflow_status=='COMPLETE'

def test_narrative_receives_validated_handoff(store,results):
    class Observe(MockAgentRuntime):
        async def run(self,role,state,tools):
            if role=='narrative':
                assert state.explanation_validated and state.explanation
            return await super().run(role,state,tools)
    output=asyncio.run(MatchAgentRunner(store,AgenticConfig(),Observe()).process_moment(results['GOAL'].moment_id))
    assert output.workflow_status=='COMPLETE'

def test_audience_presentation_does_not_change_analysis(store,results):
    r=asyncio.run(MatchAgentRunner(store,AgenticConfig()).process_moment(results['GOAL'].moment_id,
        AudienceProfile(audience='analyst',duration_hint='standard')))
    original=results['GOAL']
    assert r.explanation==original.explanation
    assert r.state.evidence==original.state.evidence
    assert r.state.tactical_findings==original.state.tactical_findings
    assert len(r.narrative.summary)>len(original.narrative.summary)

def test_contradictions_preserved(results):
    r=results['STATE_CHANGE']
    assert r.state.explanation_context.context['confidence_factors']['conflicting_directions']
    assert 'contradiction' in r.explanation.statement_ids
    assert 'contradiction' in r.narrative.statement_ids
    assert r.explanation.confidence<=r.state.explanation_context.confidence
    assert 'different directions' in r.narrative.summary

def test_model_bounds(results):
    data=results['GOAL'].explanation.model_dump()
    with pytest.raises(ValidationError):
        ExplanationResult.model_validate({**data,'confidence':1.1})
    with pytest.raises(ValidationError):
        ExplanationResult.model_validate({**data,'evidence_ids':[]})

@pytest.mark.parametrize('env', [
    {'AGENTIC_MODE':'foundry'}, {'AGENTIC_MODE':'wrong'},
    {'AGENTIC_MODE':'foundry','FOUNDRY_PROJECT_ENDPOINT':'http://x','FOUNDRY_MODEL':'deployment'},
    {'AGENTIC_MODE':'foundry','FOUNDRY_PROJECT_ENDPOINT':'https://x?token=secret','FOUNDRY_MODEL':'deployment'},
    {'AGENTIC_MODE':'foundry','FOUNDRY_PROJECT_ENDPOINT':'https://x','FOUNDRY_MODEL':'deployment','FOUNDRY_AGENT_ID':'managed'},
])
def test_config_errors(env):
    with pytest.raises(ValidationError):
        AgenticConfig.from_env(env)

def test_default_and_legacy_config():
    assert AgenticConfig.from_env({}).mode=='mock'
    c=AgenticConfig.from_env({'AGENTIC_MODE':'foundry','FOUNDY_PROJECT_ENDPOINT':'https://example.invalid',
        'FOUNDRY_MODEL':'test-deployment','AZURE_CLIENT_ID':'private-identity'})
    assert c.project_endpoint=='https://example.invalid'
    assert 'private-identity' not in str(c) and 'private-identity' not in c.model_dump_json()

def test_unknown_moment_error(store):
    with pytest.raises(ToolError):
        asyncio.run(MatchAgentRunner(store,AgenticConfig()).process_moment('missing'))

def test_secrets_not_in_tools_or_traces(store,results,monkeypatch):
    monkeypatch.setenv('AZURE_CLIENT_SECRET','TOP_SECRET_CLIENT_SECRET')
    monkeypatch.setenv('FOUNDRY_API_KEY','TOP_SECRET_API_KEY')
    tools=MatchLensTools(store)
    assert 'TOP_SECRET' not in json.dumps(tools.get_match_summary())
    result=asyncio.run(MatchAgentRunner(store,AgenticConfig()).process_moment(results['GOAL'].moment_id))
    assert 'TOP_SECRET' not in result.model_dump_json()
    s=ToolSession(tools)
    with pytest.raises(ToolError):
        s.call('player.get_summary',player_id='TOP_SECRET_USER_INPUT')
    assert 'TOP_SECRET' not in json.dumps([c.model_dump() for c in s.calls])

def test_scoped_tool_permissions(store):
    s=ToolSession(MatchLensTools(store),ROLE_TOOLS['narrative'])
    with pytest.raises(ToolError,match='available'):
        s.call('team.get_pressure',team_id='team_00')

def test_mcp_protocol_schema_and_structured_output(store):
    from mcp.client import Client
    from jsonschema import Draft202012Validator
    async def run():
        async with Client(create_server(store)) as client:
            listed=await client.list_tools()
            assert {t.name for t in listed.tools}==set(TOOL_METHODS)
            for t in listed.tools:
                Draft202012Validator.check_schema(t.input_schema)
                Draft202012Validator.check_schema(t.output_schema)
                assert t.description and t.annotations.read_only_hint
            r=await client.call_tool('team.get_pressure',{'team_id':'team_00','window':300})
            assert not r.is_error and r.structured_content['metrics']['pressure_index']==MatchLensTools(store).get_pressure_metrics('team_00')['metrics']['pressure_index']
            error=await client.call_tool('evidence.get',{'evidence_id':'unknown'})
            assert error.is_error and 'NOT_FOUND' in str(error.content)
            invalid=await client.call_tool('team.get_pressure',{'team_id':'team_00','window':42})
            assert invalid.is_error
    asyncio.run(run())

def test_framework_tool_invokes_same_registry(store):
    async def run():
        session=ToolSession(MatchLensTools(store),ROLE_TOOLS['tactical'])
        tool=next(t for t in session.framework_tools() if t.name=='team_get_pressure')
        result=await tool.invoke(arguments={'team_id':'team_00','window':300},skip_parsing=True)
        assert result==MatchLensTools(store).get_pressure_metrics('team_00',300)
        assert session.calls[-1].tool=='team.get_pressure'
    asyncio.run(run())

def test_cli_real_mock_trace(tmp_path,capsys,monkeypatch):
    monkeypatch.setenv('AGENTIC_MODE','mock')
    from simulator.run import main
    main(['--seed','99','--output',str(tmp_path/'match.jsonl'),'--agents'])
    data=json.loads((tmp_path/'agent_results.json').read_text())
    assert len(data['results'])==3
    assert all(r['workflow_status']=='COMPLETE' for r in data['results'])
    output=capsys.readouterr().out
    assert 'MATCHLENS AGENTIC PIPELINE' in output
    assert 'tool: team.get_pressure' in output
    assert 'Validation: OK' in output

def test_foundry_sdk_constructs_without_request_or_fake_credentials():
    # Real installed Azure/Foundry constructors; no token acquisition or model request.
    config=AgenticConfig(mode='foundry',project_endpoint='https://example.services.ai.azure.com/api/projects/offline',
                         model='offline-contract-deployment')
    async def run():
        runtime=FoundryAgentRuntime(config)
        with patch('socket.socket.connect',side_effect=AssertionError('Network forbidden in offline contract test')):
            await runtime.prepare()
            from agent_framework.foundry import FoundryChatClient
            assert isinstance(runtime.client,FoundryChatClient)
            assert runtime.client.model==config.model
            await runtime.aclose()
        assert runtime.client is None
    asyncio.run(run())

def test_foundry_adapter_structured_output_with_offline_sdk_transport(store,results):
    # This is a clearly labelled offline transport double, never a live Foundry result.
    from agent_framework import ChatResponse, Message, Content
    class OfflineSDKTransport:
        async def get_response(self,messages,*,stream=False,options=None,**kwargs):
            assert issubclass(options['response_format'],ExplanationResult)
            payload=json.loads(next(message.text for message in messages if message.role=='user'))
            assert payload['validated_explanation'] is not None
            tool=next(t for t in options['tools'] if t.name=='evidence_get_bundle')
            await tool.invoke(arguments={'evidence_ids':payload['evidence_ids']},skip_parsing=True)
            output=results['GOAL'].explanation
            return ChatResponse(messages=Message('assistant',[Content.from_text(output.model_dump_json())]),
                value=output,response_format=ExplanationResult)
    async def run():
        state=results['GOAL'].state
        config=AgenticConfig(mode='foundry',project_endpoint='https://example.invalid',model='offline-contract-deployment')
        runtime=FoundryAgentRuntime(config)
        runtime.client=OfflineSDKTransport()
        session=ToolSession(MatchLensTools(store.scope(state.current_time)),ROLE_TOOLS['explanation'])
        output=await runtime.run('explanation',state,session)
        assert output==results['GOAL'].explanation
        assert session.calls[-1].tool=='evidence.get_bundle'
        EvidenceGroundingValidator().validate(output,state)
    asyncio.run(run())

def test_mcp_stdio_server_real_protocol(store,tmp_path):
    from mcp.client import Client
    from mcp.client.stdio import StdioServerParameters
    import sys
    async def run():
        root=Path(__file__).resolve().parents[1]
        directory=tmp_path
        (directory/'match_intelligence.json').write_text(store.intelligence.model_dump_json())
        (directory/'explanations.json').write_text(store.report.model_dump_json())
        (directory/'match.jsonl').write_text(''.join(e.model_dump_json()+'\n' for e in store.events))
        parameters=StdioServerParameters(command=sys.executable,cwd=str(root),
            args=['-m','simulator.agentic.mcp.server','--intelligence',str(directory/'match_intelligence.json'),
                  '--explanations',str(directory/'explanations.json'),'--events',str(directory/'match.jsonl')])
        async with Client(parameters,read_timeout_seconds=10) as client:
            listed=await client.list_tools()
            assert len(listed.tools)==17
            result=await client.call_tool('moment.get',{'moment_id':store.report.explanations[0].moment_id})
            assert not result.is_error and result.structured_content['moment_id']==store.report.explanations[0].moment_id
    asyncio.run(run())

def test_unsupported_caveat_claim_rejected(results):
    r=results['GOAL']
    output=r.explanation.model_copy(update={'caveats':r.explanation.caveats+['Player Messi caused 999 goals.']})
    with pytest.raises(GroundingError):
        EvidenceGroundingValidator().validate(output,r.state)

def test_narrative_cannot_change_evidence_confidence(results):
    r=results['GOAL']
    output=r.narrative.model_copy(update={'confidence':0.1})
    with pytest.raises(GroundingError):
        EvidenceGroundingValidator().validate(output,r.state)

def test_snapshot_horizon_validates(store):
    with pytest.raises(ValueError,match='horizon'):
        IntelligenceStore(store.intelligence,store.report,store.events,store.current_time+1)

def test_cli_missing_foundry_config_clear_error(capsys,monkeypatch):
    from simulator.run import main
    monkeypatch.setenv('AGENTIC_MODE','foundry')
    for key in ('FOUNDRY_PROJECT_ENDPOINT','FOUNDY_PROJECT_ENDPOINT','FOUNDRY_MODEL'):
        monkeypatch.delenv(key,raising=False)
    with pytest.raises(SystemExit) as e:
        main(['--agents'])
    assert e.value.code==2
    assert 'FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL' in capsys.readouterr().err
