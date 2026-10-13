import asyncio
import json
import pytest
from fastapi.testclient import TestClient
from simulator.presentation.api import create_app,DEFAULT_PATH,sse
from simulator.presentation.service import MatchService
from simulator.realtime.models import EventEnvelope,parse_event
from simulator.intelligence.engine import IntelligenceEngine
from simulator.production.config import AppConfig

@pytest.fixture
def app():
    # These are offline product contracts. Live-test environment exports must
    # not turn their twelve synthetic replay workflows into Azure requests.
    return create_app(DEFAULT_PATH,AppConfig())

def test_read_schemas_and_missing_ids(app):
    with TestClient(app) as c:
        matches=c.get('/api/matches').json();mid=matches[0]['match_id']
        initial=c.get('/api/matches/'+mid).json()
        assert initial['state']['total_events']==0
        assert initial['match']['runtime_mode']=='mock'
        assert initial['state']['home_score']==0
        assert c.get('/api/matches/bad').status_code==404
        assert c.get('/api/moments/bad').status_code==404
        assert c.get('/api/players/bad').status_code==404
        assert c.get('/api/matches/'+mid+'/intelligence').status_code==200
        assert c.get('/api/matches/'+mid+'/timeline').json()==[]
        assert c.get('/api/matches/'+mid+'/events').json()==[]
        assert c.get('/api/matches/'+mid+'/events?limit=201').status_code==422
        assert c.get('/api/matches/'+mid+'/stream?cursor=bad').status_code==400
        assert c.post('/api/matches/'+mid+'/demo',json={'action':'speed','speed':50}).status_code==422
        assert c.post('/api/matches/'+mid+'/demo',json={'action':'start','speed':100},headers={'Origin':'https://untrusted.example'}).status_code==403
        body=json.dumps(initial)
        assert 'connection_string' not in body and 'credential' not in body

def test_real_pipeline_insights_evidence_and_lookup(app):
    service=app.state.service
    async def exercise():
        source=[parse_event(json.loads(line)) for line in DEFAULT_PATH.read_text().splitlines()]
        for i,event in enumerate(source):
            env=EventEnvelope.wrap(event,i)
            status=await service.processor.process(env)
            await service.on_event(env,status)
        assert len(service.events)==200
        assert service.state().total_events==len(source)
        assert {'GOAL','PRESSURE_SHIFT'}<={i.moment.moment_type.value for i in service.insights.values()}
        assert len(service.insights)==56
        assert all(i.workflow_status=='COMPLETE' for i in service.insights.values() if i.moment.tier.value in {'HIGH','CRITICAL'})
        assert service.processor.metrics.agent_workflows_triggered==12
        snapshot=service.processor.final_intelligence(service.match.match_id).model_dump()
        batch=IntelligenceEngine(service.match,source).analyze().model_dump()
        snapshot.pop('generated_at');batch.pop('generated_at');assert snapshot==batch
    asyncio.run(exercise())
    with TestClient(app) as c:
        first=next(i for i in service.insights.values() if i.explanation);identifier=first.insight_id
        response=c.get('/api/moments/'+identifier).json()
        assert response['explanation']['evidence_ids']
        eid=first.evidence[0].evidence_id
        evidence=c.get('/api/evidence/'+eid,params={'insight_id':identifier})
        assert evidence.status_code==200 and evidence.json()['evidence_id']==eid
        assert c.get('/api/evidence/missing',params={'insight_id':identifier}).status_code==404
        pid=service.state().players[0].player_id
        assert c.get('/api/players/'+pid+'/intelligence').json()['player_id']==pid
        assert service.state().home_score==sum(e.event_type.value=='GOAL' and e.team_id==service.info.home.team_id for e in service.processor.matches[service.match.match_id].events)

@pytest.mark.parametrize('cursor',[None,-1,999999])
def test_sse_reset_serialization(cursor):
    service=MatchService(DEFAULT_PATH);queue,initial=service.subscribe(cursor)
    assert initial[0].type=='RESET'
    message=sse(initial[0]);assert message.startswith('id: 0\n')
    assert json.loads(message.split('data: ')[1])['payload']['state']['status']=='waiting'
    service.subscribers.discard(queue)
    assert not service.subscribers

def test_sse_resume_and_bounded_overflow():
    service=MatchService(DEFAULT_PATH)
    async def exercise():
        await service.control('speed',20)
        queue,initial=service.subscribe(0)
        assert len(initial)==1 and initial[0].type=='MATCH_STATE_UPDATED'
        for n in range(80):await service.control('speed',10)
        assert queue.qsize()<=64
        values=[]
        while not queue.empty():values.append(queue.get_nowait())
        assert any(m.type=='RESET' for m in values)
        assert all('credential' not in sse(m) for m in values)
        service.subscribers.discard(queue)
        await service.close()
    asyncio.run(exercise())

def test_demo_pause_speed_resume_lifecycle():
    async def exercise():
        service=MatchService(DEFAULT_PATH)
        await service.control('start',100)
        await asyncio.sleep(.1)
        await service.control('pause',100)
        await asyncio.sleep(.05)
        before=service.clock
        await asyncio.sleep(.05)
        assert service.clock==before
        assert service.status=='paused'
        await service.control('speed',20)
        await service.control('resume',20)
        assert service.status=='running' and service.speed==20
        await service.close()
        assert service.task.done()
        assert not service.source.running
    asyncio.run(exercise())
