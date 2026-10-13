"""Offline browser-owner boundary probes; no Azure inference."""
import asyncio
import time
import pytest
from fastapi.testclient import TestClient
from simulator.production.public_api import create_public_app
from tests.test_public_demo import ENV

@pytest.mark.parametrize('suffix,method',[('/api/matches','get'),('/api/matches/{mid}/demo','post'),('/api/matches/{mid}/stream','get'),('/api/matches/{mid}/sequences/unknown/events?timestamp=0','get'),('/api/matches/{mid}/sensitivity?timestamp=0&sequence_id=unknown','get'),('/api/matches/{mid}/guide/0','post')])
def test_copied_session_id_cannot_cross_browser(suffix,method,monkeypatch):
 app=create_public_app(ENV)
 with TestClient(app,base_url='https://testserver') as a,TestClient(app,base_url='https://testserver') as b:
  first=a.post('/api/sessions').json();second=b.post('/api/sessions').json()
  service=app.state.sessions[first['session_id']][0].state.service
  async def idle():await asyncio.Event().wait()
  monkeypatch.setattr(service,'replay',idle)
  if service.preparer:monkeypatch.setattr(service.preparer,'start',lambda:None)
  path=first['api_path']+suffix.replace('{mid}',service.match.match_id)
  response=b.post(path,json={'action':'pause'}) if method=='post' else b.get(path)
  assert response.status_code==403
  assert a.get(first['api_path']+'/api/matches').status_code==200
  assert b.get(second['api_path']+'/api/matches').status_code==200
  assert service.status=='waiting'

def test_owner_cookie_is_not_public_session_identity():
 app=create_public_app(ENV)
 with TestClient(app,base_url='https://testserver') as a,TestClient(app,base_url='https://testserver') as b:
  response=a.post('/api/sessions');session=response.json()
  cookie=response.headers.get('set-cookie','')
  assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=none' in cookie
  assert not any(key in session for key in ('secret','owner','token'))
  assert b.delete('/api/sessions/'+session['session_id']).status_code==403
  assert a.get(session['api_path']+'/api/matches').status_code==200
  assert a.delete('/api/sessions/'+session['session_id']).status_code==200
  assert a.get(session['api_path']+'/api/matches').status_code==404

def test_expired_owner_cannot_touch_session_back_to_life():
 app=create_public_app(ENV)
 with TestClient(app,base_url='https://testserver') as a:
  session=a.post('/api/sessions').json();child,_=app.state.sessions[session['session_id']]
  app.state.sessions[session['session_id']]=(child,time.monotonic()-601)
  assert a.get(session['api_path']+'/api/matches').status_code==404
  assert session['session_id'] not in app.state.sessions

def test_cross_origin_read_and_mutation_are_denied_even_with_cookie():
 app=create_public_app(ENV)
 with TestClient(app,base_url='https://testserver') as a:
  session=a.post('/api/sessions').json();base=session['api_path']
  assert a.get(base+'/api/matches',headers={'Origin':'https://evil.example'}).status_code==403
  assert a.delete('/api/sessions/'+session['session_id'],headers={'Origin':'https://evil.example'}).status_code==403
  assert a.get(base+'/api/matches').status_code==200

def test_wrong_owner_cookie_and_revoked_cookie_never_regain_access():
 app=create_public_app(ENV)
 with TestClient(app,base_url='https://testserver') as a,TestClient(app,base_url='https://testserver') as b:
  first=a.post('/api/sessions').json();second=a.post('/api/sessions').json()
  # Simultaneous children in one browser retain independent owner cookies.
  assert a.get(first['api_path']+'/api/matches').status_code==200
  assert a.get(second['api_path']+'/api/matches').status_code==200
  b.cookies.set('matchlens_owner_'+first['session_id'],'invalid-owner',domain='testserver.local',path='/')
  assert b.get(first['api_path']+'/api/matches').status_code==403
  assert a.delete('/api/sessions/'+first['session_id']).status_code==200
  assert a.get(first['api_path']+'/api/matches').status_code==404
  assert a.get(second['api_path']+'/api/matches').status_code==200
  fresh=a.post('/api/sessions').json()
  assert fresh['session_id'] not in (first['session_id'],second['session_id'])

def test_delayed_post_cannot_reinsert_session_deleted_during_body_read():
 import httpx
 from starlette.requests import Request
 from fastapi import HTTPException
 async def run():
  app=create_public_app({**ENV,'APP_ENV':'local','APP_FRONTEND_ORIGINS':'http://127.0.0.1:3000','AGENTIC_MODE':'mock'})
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8000') as client:
   session=(await client.post('/api/sessions')).json();sid=session['session_id']
   child=app.state.sessions[sid][0];path='api/matches/'+child.state.service.match.match_id+'/demo'
   entered=asyncio.Event();release=asyncio.Event()
   async def receive():
    entered.set();await release.wait()
    return {'type':'http.request','body':b'{"action":"pause"}','more_body':False}
   cookie=client.build_request('POST',session['api_path']+'/'+path).headers['cookie']
   request=Request({'type':'http','method':'POST','scheme':'http','path':'/'+path,'headers':[(b'cookie',cookie.encode())]},receive)
   endpoint=next(r.endpoint for r in app.routes if getattr(r,'path',None)=='/sessions/{sid}/{path:path}')
   pending=asyncio.create_task(endpoint(sid,path,request));await entered.wait()
   assert (await client.delete('/api/sessions/'+sid)).status_code==200
   release.set()
   with pytest.raises(HTTPException) as error:await pending
   assert error.value.status_code==404 and sid not in app.state.sessions
 asyncio.run(run())

def test_concurrent_local_allocations_have_distinct_owners_and_hard_capacity():
 import httpx
 async def run():
  app=create_public_app({**ENV,'APP_ENV':'local','APP_FRONTEND_ORIGINS':'http://127.0.0.1:3000','AGENTIC_MODE':'mock'})
  async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8000') as client:
   responses=await asyncio.gather(*(client.post('/api/sessions') for _ in range(10)))
   sessions=[r.json() for r in responses if r.status_code==201]
   assert all(s['runtime']=='mock' for s in sessions)
   assert len(sessions)==len(set(s['session_id'] for s in sessions))==8
   assert sum(r.status_code==503 for r in responses)==2
   for s in sessions:assert (await client.get(s['api_path']+'/api/matches')).status_code==200
   await asyncio.gather(*(child.state.service.close() for child,_ in app.state.sessions.values()))
 asyncio.run(run())
