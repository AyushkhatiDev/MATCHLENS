"""Adversarial boundary probes; offline unless a separately labelled live receipt says otherwise."""
import asyncio
from unittest.mock import patch
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from simulator.presentation.api import create_app,DEFAULT_PATH
from simulator.production.config import AppConfig
from simulator.production.public_api import create_public_app

PUBLIC={'APP_FRONTEND_ORIGINS':'https://matchlens.example','AGENTIC_MODE':'foundry','FOUNDRY_PROJECT_ENDPOINT':'https://example.services.ai.azure.com/api/projects/existing','FOUNDRY_MODEL':'existing'}

def test_invalid_or_unknown_reset_request_does_not_spend_valid_restart_budget():
 app=create_public_app(PUBLIC)
 with TestClient(app) as client:
  session=client.post('/api/sessions').json();service=app.state.sessions[session['session_id']][0].state.service
  base=session['api_path']+'/api/matches/'+service.match.match_id
  assert client.post(base+'/demo',json={'action':'start','speed':999}).status_code==422
  assert getattr(service,'public_last_start',None) is None
  assert client.post(session['api_path']+'/api/matches/foreign/guide/0').status_code==404
  assert getattr(service,'public_last_start',None) is None

def test_concurrent_stream_reservation_enforces_capacity_before_response_body():
 async def run():
  app=create_app(DEFAULT_PATH,AppConfig(max_subscribers=1));service=app.state.service
  endpoint=next(r.endpoint for r in app.routes if getattr(r,'path',None)=='/api/matches/{mid}/stream')
  scope={'type':'http','method':'GET','scheme':'http','path':'/','raw_path':b'/','query_string':b'','headers':[],'asgi':{'version':'3.0','spec_version':'2.0'}}
  async def receive():return {'type':'http.disconnect'}
  first=await endpoint(service.match.match_id,Request(scope,receive))
  try:
   with pytest.raises(HTTPException) as error:await endpoint(service.match.match_id,Request(scope,receive))
   assert error.value.status_code==503
  finally:
   async def send(_message):pass
   await first(scope,receive,send)
  assert not service.subscribers
 asyncio.run(run())

@pytest.mark.parametrize('suffix', ['', '/events', '/state', '/intelligence', '/moments', '/timeline', '/sequences', '/sequences/unknown/events?timestamp=0', '/sensitivity?timestamp=0&sequence_id=unknown', '/guide', '/stream'])
def test_match_endpoints_reject_foreign_identity(suffix):
 with TestClient(create_app(DEFAULT_PATH,AppConfig())) as client:
  response=client.get('/api/matches/foreign'+suffix)
  assert response.status_code==404
  assert 'Traceback' not in response.text and '/Users/' not in response.text

def test_sequence_source_boundary_and_empty_page():
 import json
 from simulator.realtime.models import parse_event
 from simulator.presentation.temporal import sequence_choices
 app=create_app(DEFAULT_PATH,AppConfig());service=app.state.service
 events=[parse_event(json.loads(line)) for line in DEFAULT_PATH.read_text().splitlines()]
 service.processor.matches[service.match.match_id].events.extend(e for e in events if e.timestamp<=1274.43)
 service.clock=1274.43
 sid=sequence_choices(events,1200)[0]['sequence_id']
 with TestClient(app) as client:
  path=f'/api/matches/{service.match.match_id}/sequences/{sid}/events?timestamp=1200'
  first=client.get(path+'&limit=200').json()
  assert first['events'] and first['next_offset'] is None
  for offset in [first['total'],first['total']+1]:
   last=client.get(path+f'&offset={offset}').json()
   assert last['total']==first['total'] and last['events']==[] and last['next_offset'] is None
  assert client.get(path+'&limit=0').status_code==422
  assert client.get(path+'&limit=201').status_code==422

def test_known_session_identifier_alone_no_longer_grants_access():
 app=create_public_app(PUBLIC)
 with TestClient(app) as owner,TestClient(app) as other:
  session=owner.post('/api/sessions').json()
  # No account authentication is implemented. The browser must also hold
  # its server-issued owner cookie; copying the public identifier is insufficient.
  assert other.get(session['api_path']+'/api/matches').status_code==403
  assert owner.get(session['api_path']+'/api/matches').status_code==200
  assert other.get('/sessions/'+'0'*32+'/api/matches').status_code==404
  assert owner.delete('/api/sessions/'+session['session_id']).status_code==200
