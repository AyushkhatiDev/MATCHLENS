"""Real-engine fixed-cutoff sensitivity, bounded sources, and snapshot integrity."""
import asyncio
import json
import threading
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from simulator.presentation.api import create_app, DEFAULT_PATH
from simulator.presentation.temporal import sensitivity, sequence_choices
from simulator.production.config import AppConfig
from simulator.realtime.models import EventEnvelope, parse_event

SID='37a664fc-86cf-4c2f-b07f-44f6afe57b27'

@pytest.fixture
def observed():
    app=create_app(config=AppConfig())
    service=app.state.service
    events=[parse_event(json.loads(line)) for line in DEFAULT_PATH.read_text().splitlines()]
    async def ingest():
        for i,event in enumerate(events):
            if event.timestamp>1274.43:break
            envelope=EventEnvelope.wrap(event,i)
            status=await service.processor.process(envelope)
            await service.on_event(envelope,status)
        await service.close_workflows()
    asyncio.run(ingest())
    with TestClient(app) as client:
        yield service,client,f'/api/matches/{service.match.match_id}',events

def test_fixed_historical_cutoff_and_real_flip_preserve_original(observed):
    service,client,path,events=observed
    before=service.snapshot().model_dump_json()
    choices=client.get(path+'/sequences?timestamp=1200').json()
    assert choices['timestamp']==1200
    choice=next(c for c in choices['sequences'] if c['sequence_id']==SID)
    assert (choice['start'],choice['end'],choice['events'])==(1011.31,1055.08,10)
    assert client.get(path+'/sequences').json()['timestamp']==1274.43
    result=client.get(path+f'/sensitivity?timestamp=1200&sequence_id={SID}').json()
    assert (result['observed_state'],result['altered_state'])==('HOME_CONTROL','HIGH_PRESSURE')
    assert [(d['home'],d['away']) for d in result['observed'][:3]]==[(61.5,38.5),(35,35),(91.7,8.3)]
    assert [(d['home'],d['away']) for d in result['without_sequence'][:3]]==[(64.2,35.8),(15.8,47.5),(91.4,8.6)]
    expected=sensitivity(service.match,events,1200,SID)
    assert result['removed_event_ids']==expected['removed_event_ids']
    assert service.snapshot().model_dump_json()==before
    assert service.score==(1,1) and service.state().temporal.current.timestamp==1200
    assert len(service.sensitivity_cache)==1
    assert client.get(path+f'/sensitivity?timestamp=1200&sequence_id={SID}').json()==result

@pytest.mark.parametrize('timestamp',['-1','nan','inf','-inf','garbage','1275'])
def test_all_inspection_paths_reject_invalid_or_future_time(observed,timestamp):
    _,client,path,_=observed
    for route in ['/sequences',f'/sequences/{SID}/events',f'/sensitivity?sequence_id={SID}']:
        separator='&' if '?' in route else '?'
        assert client.get(path+route+separator+'timestamp='+timestamp).status_code in (400,422)

def test_sources_paginate_exact_omitted_prefix_and_exclude_future(observed):
    service,client,path,events=observed
    pages=[client.get(path+f'/sequences/{SID}/events?timestamp=1200&limit=3&offset={offset}').json() for offset in (0,3,6,9)]
    records=[e for p in pages for e in p['events']]
    assert [p['next_offset'] for p in pages]==[3,6,9,None]
    assert all(p['total']==10 for p in pages)
    result=client.get(path+f'/sensitivity?timestamp=1200&sequence_id={SID}').json()
    assert [e['event_id'] for e in records]==result['removed_event_ids']
    assert all(e['timestamp']<=1200 and e['match_id']==service.match.match_id for e in records)
    assert records==[e.model_dump(mode='json') for e in events if e.event_id in result['removed_event_ids']]
    assert client.get(path+f'/sequences/{SID}/events?timestamp=1200&limit=201').status_code==422
    assert client.get(path+f'/sequences/{SID}/events?timestamp=1200&offset=-1').status_code==422
    assert client.get('/api/matches/wrong/sequences?timestamp=1200').status_code==404

def test_unavailable_sequence_and_unchanged_classification_are_honest(observed):
    service,client,path,events=observed
    for sid in ('invented',SID):
        cutoff=1200 if sid=='invented' else 600
        assert client.get(path+f'/sequences/{sid}/events?timestamp={cutoff}').status_code==404
        assert client.get(path+f'/sensitivity?timestamp={cutoff}&sequence_id={sid}').status_code==404
    other=next(c['sequence_id'] for c in sequence_choices(events,1200) if c['sequence_id']!=SID)
    result=client.get(path+f'/sensitivity?timestamp=1200&sequence_id={other}').json()
    assert result['observed_state']==result['altered_state']=='HOME_CONTROL'
    assert len(result['observed'])==len(result['without_sequence'])==8

def test_source_page_includes_earlier_sequence_segment_but_not_future(observed):
    service,client,path,events=observed
    crossing=next(c for c in sequence_choices(events,1200) if any(e.timestamp<900 and getattr(e,'possession_sequence_id',None)==c['sequence_id'] for e in events))
    sid=crossing['sequence_id']
    result=client.get(path+f'/sequences/{sid}/events?timestamp=1200').json()
    omitted=client.get(path+f'/sensitivity?timestamp=1200&sequence_id={sid}').json()
    assert any(e['timestamp']<900 for e in result['events'])
    assert [e['event_id'] for e in result['events']]==omitted['removed_event_ids']
    assert all(e['timestamp']<=1200 for e in result['events'])

def test_reset_during_calculation_cannot_repopulate_old_cache(observed):
    service,client,path,_=observed
    started=threading.Event();release=threading.Event()
    from simulator.intelligence.engine import IntelligenceEngine
    original=IntelligenceEngine._calc_window
    def delayed(self,*args):
        started.set();assert release.wait(5)
        return original(self,*args)
    response=[]
    with patch.object(IntelligenceEngine,'_calc_window',delayed):
        thread=threading.Thread(target=lambda:response.append(client.get(path+f'/sensitivity?timestamp=1200&sequence_id={SID}')))
        thread.start();assert started.wait(5)
        from simulator.presentation.service import ProductProcessor
        service.processor=ProductProcessor(service);service.clock=0;service.sensitivity_cache.clear()
        release.set();thread.join(5)
    assert response[0].status_code==409
    assert not service.sensitivity_cache

def test_public_session_dispatch_scopes_historical_sources_without_inference():
    from simulator.production.public_api import create_public_app
    env={'APP_FRONTEND_ORIGINS':'https://matchlens.example','AGENTIC_MODE':'foundry',
         'FOUNDRY_PROJECT_ENDPOINT':'https://example.services.ai.azure.com/api/projects/existing','FOUNDRY_MODEL':'existing'}
    app=create_public_app(env)
    with TestClient(app) as client:
        first=client.post('/api/sessions').json();second=client.post('/api/sessions').json()
        service=app.state.sessions[first['session_id']][0].state.service
        events=[parse_event(json.loads(line)) for line in DEFAULT_PATH.read_text().splitlines()]
        # Offline setup only: no replay start, no Foundry request, no fabricated UI receipt.
        service.processor.matches[service.match.match_id].events.extend(e for e in events if e.timestamp<=1274.43)
        service.clock=1274.43
        path=first['api_path']+f'/api/matches/{service.match.match_id}'
        assert client.get(path+'/sequences?timestamp=1200').json()['timestamp']==1200
        assert client.get(path+f'/sequences/{SID}/events?timestamp=1200').json()['total']==10
        other=second['api_path']+f'/api/matches/{service.match.match_id}'
        assert client.get(other+'/sequences?timestamp=1200').status_code==400
        assert client.get(other+f'/sequences/{SID}/events?timestamp=0').status_code==404
        assert not service.preparer.task
        client.delete('/api/sessions/'+first['session_id'])
        response=client.get(path+'/sequences?timestamp=1200')
        assert response.status_code==404 and response.json()['detail'].startswith('Demo session expired')
