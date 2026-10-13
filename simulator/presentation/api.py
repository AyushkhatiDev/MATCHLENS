import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import os
import time
import logging
from simulator.production.config import AppConfig
from simulator.production.observability import health,metrics
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse,JSONResponse
from .models import MatchInfo, State, Snapshot, Insight, Control
from .service import MatchService,SessionClosedError
from simulator.intelligence.models import MatchIntelligence, PlayerIntelligence
from simulator.intelligence.explanation.models import TraceEvidence

DEFAULT_PATH=Path(__file__).resolve().parents[2]/'data/generated/phase5/seed99/match.jsonl'

def sse(message):
    return f'id: {message.sequence_number}\ndata: {message.model_dump_json()}\n\n'

def create_app(path=DEFAULT_PATH,config=None):
    config=config or AppConfig.from_env()
    try:service=MatchService(path,config)
    except Exception:
        raise ValueError('Invalid match input. Check MATCHLENS_DEMO_PATH and its synthetic metadata; input values withheld.') from None
    logger=logging.getLogger("matchlens.realtime")
    logger.setLevel(config.log_level)
    if not logger.handlers:
        handler=logging.StreamHandler();handler.setFormatter(logging.Formatter('%(message)s'));logger.addHandler(handler)
    logger.propagate=False
    @asynccontextmanager
    async def lifespan(app):
        if config.autostart:await service.control('start',100)
        try:yield
        finally:await service.close()
    app=FastAPI(title='MatchLens presentation API',lifespan=lifespan)
    app.state.service=service
    @app.exception_handler(SessionClosedError)
    async def closed_session(_request,_error):
        return JSONResponse({'detail':'Replay session closed; reconnect to start a fresh session'},status_code=409)
    app.add_middleware(CORSMiddleware,allow_origins=config.frontend_origins,
        allow_methods=['GET','POST'],allow_headers=['Content-Type','Last-Event-ID'])
    @app.middleware('http')
    async def audit_request(request,call_next):
        response=await call_next(request)
        if response.status_code>=400:service.observer.api_errors+=1
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        return response
    @app.get('/health')
    async def liveness():return health(service)
    @app.get('/ready')
    async def readiness():
        from fastapi.responses import JSONResponse
        report=health(service)
        return JSONResponse(report,status_code=503 if report['status']=='not_ready' else 200)
    @app.get('/version')
    async def version():return {'application':'MatchLens','version':config.release,'schema_version':'1.0','environment':config.environment}
    @app.get('/metrics')
    async def observations():
        if not config.diagnostics:raise HTTPException(404,'Diagnostics disabled')
        return metrics(service)
    @app.get('/traces')
    async def traces():
        if not config.diagnostics:raise HTTPException(404,'Diagnostics disabled')
        return list(service.observer.traces)[-100:]
    def match(mid):
        if mid!=service.match.match_id:raise HTTPException(404,'Match not found')
        return service
    def insight(identifier):
        result=service.insights.get(identifier)
        if result is None:raise HTTPException(404,'Moment not found')
        return result
    from .temporal import comparison, sensitivity, sequence_choices, guide
    @app.get('/api/comparison')
    async def compare():return await asyncio.to_thread(comparison)
    @app.get('/api/data-plane')
    async def data_plane():
        now=time.monotonic()
        return {'input':config.event_source,'agent_runtime':config.agent.mode,'application_status':service.status,
            'foundry_status':next((i.execution.status for i in reversed(list(service.insights.values())) if i.execution and i.execution.mode==config.agent.mode), 'MOCK' if config.agent.mode=='mock' else 'FOUNDRY UNAVAILABLE'),
            'foundry_hero_only':config.foundry_hero_only,
            'processed_events':len(service.processor.matches[service.match.match_id].events),
            'processed_rate':sum(now-t<=10 for t in service.received_times)/10,
            'rate_window_seconds':10,'processed_rate_lower_bound':len(service.received_times)==service.received_times.maxlen and bool(service.received_times) and service.received_times[0]>now-10,'last_processed_at':service.received_at,
            'namespace':config.azure.namespace if config.azure else None,'hub':config.azure.eventhub_name if config.azure else None,
            'published_events':None,'eventstream_current_traffic':None,'downstream':service.downstream}
    @app.get('/api/matches/{mid}/sequences')
    async def sequences(mid:str,timestamp:float|None=Query(None,ge=0,allow_inf_nan=False)):
        s=match(mid);cutoff=s.clock if timestamp is None else timestamp
        if cutoff>s.clock:raise HTTPException(400,'Future events cannot be inspected')
        return {'timestamp':cutoff,'sequences':sequence_choices(s.processor.matches[mid].events,cutoff)}
    @app.get('/api/matches/{mid}/sequences/{sequence_id}/events')
    async def sequence_events(mid:str,sequence_id:str,timestamp:float=Query(ge=0,allow_inf_nan=False),
                              offset:int=Query(0,ge=0),limit:int=Query(200,ge=1,le=200)):
        s=match(mid)
        if timestamp>s.clock:raise HTTPException(400,'Future events cannot be inspected')
        observed=s.processor.matches[mid].events
        if not any(c['sequence_id']==sequence_id for c in sequence_choices(observed,timestamp)):
            raise HTTPException(404,'Sequence unavailable in this window')
        # Exactly the observed prefix records omitted by sensitivity; bounded pages.
        records=[e for e in observed if e.timestamp<=timestamp and getattr(e,'possession_sequence_id',None)==sequence_id]
        page=records[offset:offset+limit];next_offset=offset+len(page)
        return {'sequence_id':sequence_id,'timestamp':timestamp,'total':len(records),
                'next_offset':next_offset if next_offset<len(records) else None,
                'events':[e.model_dump(mode='json') for e in page]}
    @app.get('/api/matches/{mid}/sensitivity')
    async def model_sensitivity(mid:str,sequence_id:str=Query(min_length=1,max_length=128),timestamp:float=Query(ge=0,allow_inf_nan=False)):
        s=match(mid)
        if timestamp>s.clock:raise HTTPException(400,'Future events cannot be inspected')
        key=(sequence_id,timestamp)
        if key not in s.sensitivity_cache:
            processor=s.processor
            try:result=await asyncio.to_thread(sensitivity,s.match,list(processor.matches[mid].events),timestamp,sequence_id)
            except ValueError:raise HTTPException(404,'Sequence unavailable in this window') from None
            if s.processor is not processor:raise HTTPException(409,'Replay reset; inspect the new checkpoint')
            if len(s.sensitivity_cache)>=32:s.sensitivity_cache.pop(next(iter(s.sensitivity_cache)))
            s.sensitivity_cache[key]=result
        return s.sensitivity_cache[key]
    @app.get('/api/matches/{mid}/guide')
    async def demo_guide(mid:str):return await asyncio.to_thread(guide,str(match(mid).path))
    @app.post('/api/matches/{mid}/guide/{stage}',response_model=State)
    async def guided_stage(mid:str,stage:int,request:Request):
        s=match(mid)
        if not config.demo_controls or config.event_source!='local':raise HTTPException(403,'Guided replay is local only')
        if request.headers.get('origin') not in (None,*config.frontend_origins):raise HTTPException(403,'Demo controls are local only')
        processor=s.processor
        s.guide_request+=1;request_id=s.guide_request
        stages=await asyncio.to_thread(guide,str(s.path))
        selected_stage=next((g for g in stages if g['stage']==stage),None)
        target=selected_stage['timestamp'] if selected_stage else None
        if target is None:raise HTTPException(404,'Stage unavailable')
        async with s.control_lock:
            if s.processor is not processor or s.guide_request!=request_id:
                raise HTTPException(409,'Guide request superseded or replay reset')
            if stage==0:
                if s.task and not s.task.done():
                    s.task.cancel();await asyncio.gather(s.task,return_exceptions=True)
                s.pause_at=0;s.pause_event_id=None;s.pause_checkpoint=False
                return await s._control('start',100)
            if s.clock>=target:raise HTTPException(409,'Stage already passed; restart the guided replay')
            s.pause_at=target
            s.pause_event_id=selected_stage.get('event_id')
            s.pause_checkpoint=stage in (1,2,4)
            return await s._control('resume' if s.status=='paused' else 'start' if s.status in ('waiting','complete') else 'speed',100)
    @app.get('/api/matches',response_model=list[MatchInfo])
    async def matches():return [service.info]
    @app.get('/api/matches/{mid}',response_model=Snapshot)
    async def detail(mid:str):return match(mid).snapshot()
    @app.get('/api/matches/{mid}/events',response_model=list[dict])
    async def events(mid:str,offset:int=Query(0,ge=0),limit:int=Query(200,ge=1,le=200)):
        return [event.model_dump(mode='json') for event in match(mid).processor.matches[mid].events[offset:offset+limit]]
    @app.get('/api/matches/{mid}/state',response_model=State)
    async def state(mid:str):return match(mid).state()
    @app.get('/api/matches/{mid}/intelligence',response_model=MatchIntelligence)
    async def intelligence(mid:str):
        s=match(mid)
        return s.processor.matches[mid].snapshot()
    @app.get('/api/matches/{mid}/moments',response_model=list[Insight])
    @app.get('/api/matches/{mid}/timeline',response_model=list[Insight])
    async def moments(mid:str):return sorted(match(mid).insights.values(),key=lambda i:i.moment.timestamp)
    @app.get('/api/moments/{identifier}',response_model=Insight)
    async def moment(identifier:str):return insight(identifier)
    @app.get('/api/evidence/{eid}',response_model=TraceEvidence)
    async def evidence(eid:str,insight_id:str):
        result=next((e for e in insight(insight_id).evidence if e.evidence_id==eid),None)
        if result is None:raise HTTPException(404,'Evidence not found in this moment snapshot')
        return result
    @app.get('/api/players/{pid}',response_model=PlayerIntelligence)
    @app.get('/api/players/{pid}/intelligence',response_model=PlayerIntelligence)
    async def player(pid:str):
        result=service.processor.matches[service.match.match_id].players.get(pid)
        if result is None:raise HTTPException(404,'Player not found')
        return result
    @app.post('/api/matches/{mid}/demo',response_model=State)
    async def control(mid:str,body:Control,request:Request):
        # Local demo only; reject cross-origin control and remote access.
        if not config.demo_controls:raise HTTPException(403,'Replay controls disabled in this environment')
        if request.headers.get('origin') not in (None,*config.frontend_origins):
            raise HTTPException(403,'Demo controls are local only')
        s=match(mid)
        async with s.control_lock:
            if body.action=='start' and (not s.task or s.task.done()):
                s.pause_at=None;s.pause_event_id=None;s.pause_checkpoint=False
            return await s._control(body.action,body.speed)
    @app.get('/api/matches/{mid}/stream')
    async def stream(mid:str,request:Request):
        s=match(mid)
        raw=request.headers.get('last-event-id') or request.query_params.get('cursor')
        try: last_id=int(raw) if raw else None
        except ValueError:raise HTTPException(400,'Invalid stream cursor') from None
        if len(s.subscribers)>=config.max_subscribers:raise HTTPException(503,'Stream capacity reached')
        # Reserve before yielding control to response startup, so simultaneous
        # connections cannot all pass the capacity check before subscribing.
        queue,initial=s.subscribe(last_id)
        async def messages():
            started=time.monotonic()
            try:
                for message in initial:yield sse(message)
                while time.monotonic()-started<config.stream_seconds and not await request.is_disconnected():
                    try:yield sse(await asyncio.wait_for(queue.get(),15))
                    except TimeoutError:yield ': heartbeat\n\n'
            finally:s.subscribers.discard(queue)
        class MatchStreamResponse(StreamingResponse):
            async def __call__(self,scope,receive,send):
                try:await super().__call__(scope,receive,send)
                finally:s.subscribers.discard(queue)
        return MatchStreamResponse(messages(),media_type='text/event-stream',
            headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
    return app

app=create_app(os.getenv('MATCHLENS_DEMO_PATH',str(DEFAULT_PATH))) if os.getenv('APP_PUBLIC_DEMO','false').lower()!='true' else FastAPI()
