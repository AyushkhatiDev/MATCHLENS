"""Offline Foundry contracts: real Agent SDK with an explicitly fake transport."""
import asyncio
import json
import pytest
from pydantic import ValidationError
from agent_framework import Agent, ChatResponse, Message, Content
from simulator.agentic import AgenticConfig, MatchAgentRunner
from simulator.agentic.agents import OUTPUT_SCHEMAS
from simulator.agentic.models import ExplanationResult
from simulator.agentic.runtime import FoundryAgentRuntime
from simulator.agentic.runtime.diagnostics import failure_details
from simulator.agentic.grounding import EvidenceGroundingValidator, GroundingError, render_statements
from simulator.agentic.tools import ToolSession, MatchLensTools, ROLE_TOOLS
from tests.test_agentic import store, results

def config():
    return AgenticConfig(mode='foundry', project_endpoint='https://example.invalid', model='offline-test')

def response(output, schema):
    value = schema.model_validate(output.model_dump())
    return ChatResponse(messages=Message('assistant',[Content.from_text(value.model_dump_json())]),
                        value=value,response_format=schema)

@pytest.mark.parametrize('role',['tactical','player','context','explanation','narrative'])
def test_role_catalog_schema_and_tool_contract(store,results,role):
    state = results['GOAL'].state
    expected = ({'tactical':state.tactical_findings[0], 'player':state.player_findings[0],
                 'context':state.context_findings[0], 'explanation':state.explanation,
                 'narrative':state.narrative})[role]
    class OfflineTransport:
        async def get_response(self,messages,*,stream=False,options=None,**kwargs):
            payload=json.loads(next(m.text for m in messages if m.role=='user'))
            ids={s['statement_id'] for s in payload['statement_catalog']}
            assert set(expected.statement_ids)<=ids
            assert 'output_schema' not in payload
            wire=options['response_format']
            assert issubclass(wire,OUTPUT_SCHEMAS[role])
            with pytest.raises(ValidationError):
                wire.model_validate({**expected.model_dump(),'confidence':1})
            if role=='player':
                assert set(payload['mandatory_evidence_ids'])<=set(expected.evidence_ids)
                with pytest.raises(ValidationError):
                    wire.model_validate({**expected.model_dump(),'evidence_ids':expected.evidence_ids[:1]})
            if role=='explanation':
                assert 'why' not in ids
            if role in ('tactical','player','context'):
                assert 'why' not in ids and len(ids)<len(state.statements)
                for change in ({'event_ids':['fabricated']}, {'statement_ids':['unknown']},
                               {'evidence_ids':['unknown']}, {'timestamp':999999}, {'finding_type':'WRONG'}):
                    with pytest.raises(ValidationError):
                        wire.model_validate({**expected.model_dump(),**change})
            tool=next(t for t in options['tools'] if t.name=='evidence_get_bundle')
            await tool.invoke(arguments={'evidence_ids':expected.evidence_ids},skip_parsing=True)
            return response(expected,options['response_format'])
    async def run():
        runtime=FoundryAgentRuntime(config()); runtime.client=OfflineTransport()
        session=ToolSession(MatchLensTools(store.scope(state.current_time)),ROLE_TOOLS[role])
        output=await runtime.run(role,state,session)
        assert output==expected and type(output) is OUTPUT_SCHEMAS[role]
        EvidenceGroundingValidator().validate(output,state)
        assert session.calls and set(output.evidence_ids)<=set(session.calls[-1].evidence_ids)
    asyncio.run(run())

@pytest.mark.parametrize('with_tool,structured',[(False,False),(True,False),(False,True),(True,True)])
def test_sdk_smoke_tool_structured_matrix(store,results,with_tool,structured):
    output=results['GOAL'].explanation
    session=ToolSession(MatchLensTools(store),{'evidence.get_bundle'})
    class OfflineTransport:
        async def get_response(self,messages,*,stream=False,options=None,**kwargs):
            if with_tool:
                tool=options['tools'][0]
                await tool.invoke(arguments={'evidence_ids':output.evidence_ids},skip_parsing=True)
            if structured:
                return response(output,options['response_format'])
            return ChatResponse(messages=Message('assistant',[Content.from_text('CONNECTED')]))
    async def run():
        agent=Agent(client=OfflineTransport(),tools=session.framework_tools() if with_tool else [],
                    default_options={'response_format':ExplanationResult} if structured else {})
        value=await agent.run('Offline SDK contract probe')
        assert value.value==output if structured else value.text=='CONNECTED'
        assert bool(session.calls)==with_tool
    asyncio.run(run())

@pytest.mark.parametrize('repair',[True,False])
def test_rejected_copy_gets_one_bounded_correction(store,results,repair):
    state=results['GOAL'].state
    valid=state.tactical_findings[0]
    rejected=valid.model_copy(update={'finding':'A dominant team caused this goal.'})
    class OfflineTransport:
        count=0
        async def get_response(self,messages,*,stream=False,options=None,**kwargs):
            self.count+=1
            if self.count==2:
                assert 'REJECTED' in messages[-1].text
            tool=next(t for t in options['tools'] if t.name=='evidence_get_bundle')
            await tool.invoke(arguments={'evidence_ids':valid.evidence_ids},skip_parsing=True)
            return response(valid if repair and self.count==2 else rejected,options['response_format'])
    async def run():
        runtime=FoundryAgentRuntime(config()); transport=OfflineTransport(); runtime.client=transport
        session=ToolSession(MatchLensTools(store.scope(state.current_time)),ROLE_TOOLS['tactical'])
        if repair:
            assert await runtime.run('tactical',state,session)==valid
        else:
            with pytest.raises(GroundingError,match='traceable'):
                await runtime.run('tactical',state,session)
        assert transport.count==2
    asyncio.run(run())

def test_live_adapter_rejects_unretrieved_citations(store,results):
    state=results['GOAL'].state
    class OfflineTransport:
        count=0
        async def get_response(self,messages,*,stream=False,options=None,**kwargs):
            self.count+=1
            return response(state.tactical_findings[0],options['response_format'])
    async def run():
        runtime=FoundryAgentRuntime(config()); runtime.client=OfflineTransport()
        session=ToolSession(MatchLensTools(store.scope(state.current_time)),ROLE_TOOLS['tactical'])
        with pytest.raises(GroundingError,match='retrieve'):
            await runtime.run('tactical',state,session)
        assert not session.calls
    asyncio.run(run())

def test_overlapping_statements_cannot_be_summarized(results):
    state=results['GOAL'].state
    ids=['consequence','why']
    output=state.context_findings[0].model_copy(update={'statement_ids':ids,
        'finding':render_statements(state,ids).replace('The goal changed the recorded scoreline. ', '',1)})
    with pytest.raises(GroundingError,match='traceable'):
        EvidenceGroundingValidator().validate(output,state)

@pytest.mark.parametrize('error',[RuntimeError('TOP_SECRET_TOKEN'),GroundingError('TOP_SECRET_TOKEN')])
def test_unknown_exception_text_not_logged(error):
    assert 'TOP_SECRET' not in json.dumps(failure_details(error))

def test_known_grounding_reason_is_observable():
    assert failure_details(GroundingError('Unknown or unsupported event ID.'))['message']=='Unknown or unsupported event ID.'
