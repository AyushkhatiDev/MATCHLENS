"""Offline public gateway and orchestration contracts; no Azure requests."""
import asyncio
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from simulator.production.public_api import create_public_app
from simulator.presentation.preparation import HeroPreparation,packet
from simulator.presentation.api import DEFAULT_PATH
from simulator.presentation.service import MatchService
from simulator.production.config import AppConfig
from simulator.agentic.config import AgenticConfig
from simulator.realtime.models import EventEnvelope,parse_event

ENV={'APP_FRONTEND_ORIGINS':'https://matchlens.example','AGENTIC_MODE':'foundry',
     'FOUNDRY_PROJECT_ENDPOINT':'https://example.services.ai.azure.com/api/projects/existing','FOUNDRY_MODEL':'existing'}


def test_gateway_cors_isolation_and_closed_sessions():
    with TestClient(create_public_app(ENV)) as client:
        assert client.get('/health').json()['foundry']['verified'] is False
        assert client.post('/api/sessions',headers={'Origin':'https://evil.example'}).status_code==403
        first=client.post('/api/sessions',headers={'Origin':ENV['APP_FRONTEND_ORIGINS']}).json()
        second=client.post('/api/sessions').json()
        assert first['session_id']!=second['session_id']
        path=first['api_path']+'/api/matches'
        response=client.get(path,headers={'Origin':ENV['APP_FRONTEND_ORIGINS']})
        assert response.status_code==200 and response.headers['access-control-allow-origin']==ENV['APP_FRONTEND_ORIGINS']
        assert 'access-control-allow-origin' not in client.get(path,headers={'Origin':'http://localhost:3000'}).headers
        assert response.json()[0]['runtime_mode']=='foundry'
        assert client.get(first['api_path']+'/traces').status_code==404
        client.delete('/api/sessions/'+first['session_id'])
        assert client.get(path).status_code==404
        assert client.get(second['api_path']+'/api/matches').status_code==200


def test_public_origins_fail_closed():
    with pytest.raises(ValueError):create_public_app({**ENV,'APP_FRONTEND_ORIGINS':'http://localhost:3000'})


def test_restart_budget_preserves_pause_resume_and_speed():
    app=create_public_app(ENV)
    with TestClient(app) as client:
        session=client.post('/api/sessions').json()
        service=app.state.sessions[session['session_id']][0].state.service
        import time
        service.public_last_start=time.monotonic()
        mid=client.get(session['api_path']+'/api/matches').json()[0]['match_id']
        base=session['api_path']+'/api/matches/'+mid
        service.status='running'
        assert client.post(base+'/demo',json={'action':'pause','speed':1}).json()['status']=='paused'
        assert client.post(base+'/demo',json={'action':'speed','speed':20}).json()['speed']==20
        assert client.post(base+'/demo',json={'action':'resume','speed':20}).json()['status']=='running'
        assert client.post(base+'/demo',json={'action':'start','speed':20}).status_code==429
        assert client.post(base+'/guide/0').status_code==429


def test_fresh_preparation_is_hidden_until_identical_visible_packet():
    # Mock is explicitly injected for this offline orchestration test only.
    from simulator.agentic.runtime.runner import MatchAgentRunner
    from simulator.agentic.runtime.mock import MockAgentRuntime
    from simulator.production.observability import ObservedRunner
    config=AppConfig(environment='demo',agent=AgenticConfig(mode='foundry',project_endpoint=ENV['FOUNDRY_PROJECT_ENDPOINT'],model='existing'),foundry_hero_only=True)
    async def run():
        service=MatchService(DEFAULT_PATH,config);preparation=HeroPreparation(service)
        original=ObservedRunner.__init__
        def offline(self,store,config,observer):
            original(self,store,AgenticConfig(),observer=observer)
        try:
            with patch.object(ObservedRunner,'__init__',offline):
                preparation.start();await preparation.task
                assert preparation.result and preparation.result.state.explanation_validated
                assert not service.insights and service.clock==0
                for index,line in enumerate(DEFAULT_PATH.read_text().splitlines()):
                    event=parse_event(json.loads(line));envelope=EventEnvelope.wrap(event,index)
                    status=await service.processor.process(envelope);await service.on_event(envelope,status)
                    if event.event_id==service.hero_event_id:break
                hero=preparation.hero(service)
                assert hero.workflow_status=='COMPLETE' and hero.execution.status=='MOCK'
                assert hero.execution.run_id==preparation.run_id
                assert packet(hero)==packet(preparation.hero(preparation.private))
                assert service.processor.metrics.agent_workflows_triggered==0
                prior=preparation.run_id
                await preparation.cancel();preparation.start();assert preparation.run_id!=prior
        finally:await service.close()
    asyncio.run(run())


def test_early_preparation_failure_is_explicit_at_visible_goal():
    config=AppConfig(environment='demo',agent=AgenticConfig(mode='foundry',project_endpoint=ENV['FOUNDRY_PROJECT_ENDPOINT'],model='existing'),foundry_hero_only=True)
    async def run():
        service=MatchService(DEFAULT_PATH,config);preparation=HeroPreparation(service)
        preparation.failed=True;preparation.run_id='failure';preparation.task=asyncio.create_task(asyncio.sleep(0))
        try:
            for index,line in enumerate(DEFAULT_PATH.read_text().splitlines()):
                event=parse_event(json.loads(line));envelope=EventEnvelope.wrap(event,index)
                status=await service.processor.process(envelope);await service.on_event(envelope,status)
                if event.event_id==service.hero_event_id:break
            hero=preparation.hero(service)
            assert hero.execution.status=='FOUNDRY UNAVAILABLE' and hero.execution.fallback is False
            assert hero.agent_explanation is None and hero.explanation
            assert service.processor.metrics.agent_workflows_triggered==0
        finally:await service.close()
    asyncio.run(run())
