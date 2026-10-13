"""Bounded, isolated public recorded-demo sessions over the existing presentation API."""
import asyncio
import json
import hashlib
import hmac
import secrets
from contextlib import asynccontextmanager
from collections import deque
import os
import time
from urllib.parse import urlsplit
import uuid
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from simulator.production.config import AppConfig
from simulator.presentation.api import create_app, DEFAULT_PATH
from simulator.presentation.preparation import HeroPreparation
from simulator.presentation.temporal import downstream_verification
from simulator.presentation.models import Control


def create_public_app(env=None):
    env=dict(os.environ if env is None else env)
    origins=env.get('APP_FRONTEND_ORIGINS','').split(',')
    local=env.get('APP_ENV')=='local'
    url=urlsplit(origins[0])
    local_origin=local and url.scheme=='http' and url.hostname in ('127.0.0.1','localhost')
    if len(origins)!=1 or (url.scheme!='https' and not local_origin) or not url.hostname:
        raise ValueError('Public demo requires exactly one deployed HTTPS frontend origin; values withheld.')
    config=AppConfig.from_env({**env,'APP_ENV':'demo','EVENT_SOURCE':'local','APP_DEMO_CONTROLS':'true',
                             'APP_DIAGNOSTICS':'false','APP_AUTOSTART':'false',
                             'APP_FOUNDRY_HERO_ONLY':str(env.get('AGENTIC_MODE','mock')=='foundry').lower()})
    # Mock mode is an explicit no-cost public profile; /health and every execution record report MOCK, never live.
    sessions={};owners={};requests=deque(maxlen=500);jobs=asyncio.Semaphore(2)
    max_sessions=8;ttl=600

    async def expire():
        while True:
            await asyncio.sleep(15)
            now=time.monotonic()
            for sid,(app,touched) in list(sessions.items()):
                if now-touched>ttl:
                    sessions.pop(sid,None)
                    owners.pop(sid,None)
                    await app.state.service.close()

    @asynccontextmanager
    async def lifespan(_app):
        cleanup=asyncio.create_task(expire())
        try:yield
        finally:
            cleanup.cancel();await asyncio.gather(cleanup,return_exceptions=True)
            await asyncio.gather(*(app.state.service.close() for app,_ in sessions.values()))

    app=FastAPI(title='MatchLens public recorded demo',lifespan=lifespan)
    app.add_middleware(CORSMiddleware,allow_origins=origins,allow_methods=['GET','POST','DELETE'],
                       allow_headers=['Content-Type','Last-Event-ID'],allow_credentials=True)

    def origin(request):
        if request.headers.get('origin') not in (None,*origins):raise HTTPException(403,'Frontend origin not allowed')
        if not request.headers.get('origin') and request.headers.get('sec-fetch-site')=='cross-site':
            raise HTTPException(403,'Frontend origin required')

    def cookie_name(sid):return 'matchlens_owner_'+sid

    async def owned(sid,request):
        origin(request)
        entry=sessions.get(sid)
        if entry and time.monotonic()-entry[1]>ttl:
            sessions.pop(sid,None);owners.pop(sid,None)
            await entry[0].state.service.close()
            entry=None
        if not entry:raise HTTPException(404,'Demo session expired; reload to start a fresh session')
        secret=request.cookies.get(cookie_name(sid),'')
        expected=owners.get(sid)
        if not expected or not hmac.compare_digest(hashlib.sha256(secret.encode()).digest(),expected):
            raise HTTPException(403,'Demo session ownership required; reconnect with the original browser')
        request.state.owner_cookie=(sid,secret)
        return entry[0]

    @app.middleware('http')
    async def headers(request,call_next):
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Cache-Control']='no-store'
        owner=getattr(request.state,'owner_cookie',None)
        if owner:
            # A separate cookie per child avoids rotating another tab's owner
            # during concurrent allocation. Never put the secret in JSON/URLs.
            sid,secret=owner
            secure=request.url.scheme=='https'
            response.set_cookie(cookie_name(sid),secret,max_age=ttl,httponly=True,
                                secure=secure,samesite='none' if secure else 'lax',path='/')
        return response

    @app.get('/health')
    @app.get('/ready')
    async def health():
        latest=next((i.execution for child,_ in reversed(list(sessions.values())) for i in reversed(list(child.state.service.insights.values()))
                     if i.execution and i.execution.mode=='foundry'),None)
        return {'status':'healthy','application':'running','production_demo':True,'database':'not_used',
                'foundry':{'status':latest.status if latest else 'MOCK' if config.agent.mode=='mock' else 'FOUNDRY UNAVAILABLE','verified':bool(latest and latest.status=='FOUNDRY LIVE')},
                'event_hubs':{'status':'HISTORICAL VERIFICATION','current_demo_input':'recorded local replay',
                              'continuous_connection':False},
                'data_plane':{'fabric':downstream_verification(),'current_input':'recorded local replay'},
                'frontend_origin':origins[0],'active_sessions':len(sessions),'session_capacity':max_sessions,
                'stream':{'status':'available','connection_seconds':config.stream_seconds},
                'cloud_probe':'No outbound inference or data-plane probe performed by this endpoint.'}

    @app.post('/api/sessions',status_code=201)
    async def new_session(request:Request):
        origin(request)
        now=time.monotonic()
        # Global budget avoids dependence on spoofable forwarded-IP headers.
        while requests and now-requests[0]>3600:requests.popleft()
        if len(requests)>=30:raise HTTPException(429,'Demo request budget reached; try later')
        if len(sessions)>=max_sessions:raise HTTPException(503,'Demo capacity reached; try later')
        requests.append(now)
        sid=uuid.uuid4().hex
        child=create_app(DEFAULT_PATH,config)
        if config.agent.mode=='foundry':HeroPreparation(child.state.service,jobs)
        sessions[sid]=(child,now)
        secret=secrets.token_urlsafe(32)
        owners[sid]=hashlib.sha256(secret.encode()).digest()
        request.state.owner_cookie=(sid,secret)
        return {'session_id':sid,'api_path':f'/sessions/{sid}','expires_after_seconds':ttl,'runtime':config.agent.mode}

    @app.delete('/api/sessions/{sid}')
    async def delete_session(sid:str,request:Request):
        child=await owned(sid,request)
        entry=sessions.pop(sid,None)
        owners.pop(sid,None)
        request.state.owner_cookie=None
        if entry:await entry[0].state.service.close()
        response=JSONResponse({'closed':True})
        response.delete_cookie(cookie_name(sid),path='/')
        return response

    @app.api_route('/sessions/{sid}/{path:path}',methods=['GET','POST'])
    async def dispatch(sid:str,path:str,request:Request):
        child=await owned(sid,request)
        body=None
        if request.method=='POST':
            origin(request)
            service=child.state.service
            match_path=f'api/matches/{service.match.match_id}'
            restarting=path==match_path+'/guide/0'
            if path==match_path+'/demo':
                body=await request.body()
                try:restarting=Control.model_validate_json(body).action=='start'
                except ValueError:pass
            # Body reads can yield while expiry/delete revokes this child.
            # Never reinsert a revoked session when an old POST resumes.
            if sid not in sessions or sessions[sid][0] is not child:
                request.state.owner_cookie=None
                raise HTTPException(404,'Demo session expired; reload to start a fresh session')
            # Reset budget is independent of session allocation: no unlimited inference resets.
            if restarting:
                last=getattr(service,'public_last_start',None)
                if last and time.monotonic()-last<60:raise HTTPException(429,'Wait before restarting this demo')
                service.public_last_start=time.monotonic()
        sessions[sid]=(child,time.monotonic())
        scope=dict(request.scope)
        scope.update(path='/'+path,raw_path=('/'+path).encode(),root_path='')
        # Dispatch through ASGI directly to retain real StreamingResponse/SSE delivery.
        from starlette.responses import Response
        class SessionResponse(Response):
            async def __call__(self,_scope,receive,send):
                sent=False
                async def replay_body():
                    nonlocal sent
                    if not sent:
                        sent=True
                        return {'type':'http.request','body':body,'more_body':False}
                    return await receive()
                await child(scope,replay_body if body is not None else receive,send)
        return SessionResponse()

    app.state.sessions=sessions
    return app


# The original local/transport profiles remain the default unless explicitly enabled.
app=create_public_app() if os.getenv('APP_PUBLIC_DEMO','false').lower()=='true' else create_app()
