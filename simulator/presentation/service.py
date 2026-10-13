import asyncio
from collections import deque
import time
from simulator.live import load_match
from simulator.realtime.models import EventEnvelope
from simulator.realtime.sources import LocalReplaySource
from simulator.realtime.processor import EventProcessor
from simulator.realtime.pipeline import run_pipeline
from simulator.realtime.intelligence import moment_key
from simulator.intelligence.explanation import ExplanationEngine
from simulator.intelligence.explanation.models import TraceEvidence
from simulator.production.config import AppConfig
from simulator.production.observability import Observability
from .execution import Execution, pending, projection
from .models import Club, MatchInfo, State, Snapshot, Insight, Update, EventMessage, InsightMessage

from .temporal import Temporal, temporal_projection, change_projection, truth_projection, downstream_verification

class SessionClosedError(RuntimeError):
    """An in-flight control reached a child revoked by expiry/shutdown."""

class ProductProcessor(EventProcessor):
    def __init__(self, service):
        self.service = service
        super().__init__([service.match], agent_config=service.config.agent, runner_factory=service.observer.runner, result_callback=service.result)

    async def _workflows(self, envelope, record, snapshot, moments, adapter):
        events = [e for e in adapter.events if e.timestamp <= snapshot.summary.match_duration_s]
        report = ExplanationEngine(source_events=events).explain(snapshot)
        for moment in moments:
            identifier = moment_key(moment)
            exp = next((e for e in report.explanations if e.moment_id == moment.moment_id), None)
            ids = set(exp.evidence_ids if exp else moment.evidence_ids)
            evs = [e for e in report.evidence if e.evidence_id in ids]
            source_ids = set(exp.source_event_ids if exp else moment.source_event_ids)
            for evidence in evs: source_ids.update(evidence.source_event_ids)
            insight = Insight(insight_id=identifier, moment=moment, explanation=exp, evidence=evs,
                source_events=[e.model_dump(mode='json') for e in events if e.event_id in source_ids],
                workflow_status='PROCESSING' if exp else 'NO_EXPLANATION',
                change=change_projection(exp,snapshot.tactical_states,moment.timestamp),
                execution=pending(self.service.config.agent.mode) if exp else None)
            self.service.workflow_evidence[identifier] = [TraceEvidence(**e.model_dump(),source_paths=[f'evidence/{i}'])
                for i,e in enumerate(snapshot.evidence) if e.timestamp<=moment.timestamp]
            self.service.insights[identifier] = insight
            self.service.emit_insight('MOMENT_DETECTED', insight, envelope.correlation_id)
            if exp: self.service.emit_insight('EXPLANATION_READY', insight, envelope.correlation_id)
        if self.service.config.foundry_hero_only:
            selected=[m for m in moments if self.service.hero_event_id in m.source_event_ids and m.moment_type.value=='GOAL']
            for moment in moments:
                insight=self.service.insights[moment_key(moment)]
                if moment not in selected:
                    insight.workflow_status='NO_EXPLANATION'
                    insight.execution=Execution(mode='deterministic',state='NOT REQUESTED')
                    self.service.workflow_evidence.pop(moment_key(moment),None)
                    self.service.emit_insight('NARRATIVE_READY',insight,envelope.correlation_id)
            if selected and self.service.preparer and self.service.preparer.task:
                await self.service.preparer.publish()
            elif selected:
                task=asyncio.create_task(self._finish(envelope,record,snapshot,selected,adapter))
                self.service.workflow_tasks.add(task)
                task.add_done_callback(self.service.workflow_tasks.discard)
            return
        await self._finish(envelope,record,snapshot,moments,adapter)

    async def _finish(self,envelope,record,snapshot,moments,adapter):
        await super()._workflows(envelope, record, snapshot, moments, adapter)
        for moment in moments:
            self.service.workflow_evidence.pop(moment_key(moment),None)
            insight = self.service.insights[moment_key(moment)]
            if insight.workflow_status == 'PROCESSING':
                insight.workflow_status = 'FAILED'
                insight.execution=Execution(mode=self.service.config.agent.mode,status='FOUNDRY UNAVAILABLE' if self.service.config.agent.mode=='foundry' else 'MOCK',state='UNAVAILABLE')
                self.service.emit_insight('NARRATIVE_READY', insight, envelope.correlation_id)

class MatchService:
    def __init__(self, path, config=None):
        self.config = config or AppConfig()
        self.observer = Observability()
        self.path = path
        self.match = load_match(path)
        self.info = MatchInfo(match_id=self.match.match_id,
            home=Club(**self.match.home_team.model_dump()), away=Club(**self.match.away_team.model_dump()),
            competition=self.match.competition_name, venue=self.match.venue, runtime_mode=self.config.agent.mode, event_source=self.config.event_source, demo_controls=self.config.demo_controls,foundry_hero_only=self.config.foundry_hero_only)
        self.status = 'waiting'
        self.speed = 100
        self.clock = 0
        self.score = (0,0)
        self.possession_player = None
        self.events = deque(maxlen=200)
        self.insights = {}
        self.cursor = 0
        self.journal = deque(maxlen=2048)
        self.subscribers = set()
        self.task = None
        self.wake = asyncio.Event()
        self.preparer=None
        self.workflow_tasks=set()
        self.hero_event_id=None
        if self.config.foundry_hero_only:
            from .temporal import guide
            self.hero_event_id=next(g['event_id'] for g in guide(str(path)) if g['stage']==3)
        self.processor = ProductProcessor(self)
        self.source = None
        self.temporal = Temporal()
        self.temporal_key = None
        self.downstream = downstream_verification()
        self.received_at = None
        self.received_times = deque(maxlen=2000)
        self.pause_at = None
        self.pause_event_id = None
        self.pause_checkpoint = False
        self.sensitivity_cache = {}
        self.workflow_evidence = {}
        self.control_lock = asyncio.Lock()
        self.guide_request = 0
        self.closed = False

    def state(self):
        adapter = self.processor.matches[self.match.match_id]
        snapshot = adapter.latest_snapshot
        key = (id(snapshot),len(adapter.windows))
        if key != self.temporal_key:
            self.temporal = temporal_projection(adapter.windows,snapshot.tactical_states if snapshot else [],adapter.events)
            self.temporal_key = key
        return State(temporal=self.temporal,status=self.status, simulation_time=self.clock, speed=self.speed,
            home_score=self.score[0], away_score=self.score[1], total_events=len(adapter.events),
            home_team=adapter.teams[adapter.engine.home_id], away_team=adapter.teams[adapter.engine.away_id],
            players=sorted(adapter.players.values(),key=lambda p:(p.team_id,p.player_id)),
            windows=adapter.windows,
            tactical_state=snapshot.tactical_states[-1] if snapshot and snapshot.tactical_states else None,
            possession_player_id=self.possession_player)

    def snapshot(self):
        return Snapshot(match=self.info,state=self.state(),events=list(self.events),
                        insights=list(self.insights.values()),stream_cursor=self.cursor)

    def emit(self, cls, **kwargs):
        self.cursor += 1
        message = cls(match_id=self.match.match_id, timestamp=kwargs.pop('timestamp',self.clock),
                      sequence_number=self.cursor, **kwargs)
        self.journal.append(message)
        for queue in tuple(self.subscribers):
            if queue.full():
                while not queue.empty(): queue.get_nowait()
                queue.put_nowait(Update(type='RESET', match_id=self.match.match_id, timestamp=self.clock,
                    sequence_number=self.cursor,payload=self.snapshot()))
            else: queue.put_nowait(message)
        return message

    def emit_insight(self, kind, insight, trace_id=None):
        # Freeze payload now: future workflow mutation must not rewrite journal.
        self.emit(InsightMessage,type=kind,payload=insight.model_copy(deep=True),trace_id=trace_id,timestamp=insight.moment.timestamp)

    async def result(self,result):
        if self.closed:return
        key = moment_key(result.state.triggered_moment)
        insight = self.insights[key]
        insight.agent_explanation = result.explanation
        insight.narrative = result.narrative
        insight.workflow_status = result.workflow_status
        insight.trace_id = result.trace_id
        insight.truth = truth_projection(result)
        insight.execution = projection(result,self.observer.traces)
        # Retain actual retrieved citations so the inspector can resolve every ID.
        known={e.evidence_id for e in insight.evidence}
        insight.evidence.extend(e for e in result.state.evidence if e.evidence_id not in known)
        retrieved={eid for ex in result.state.agent_executions for t in ex.tool_calls for eid in t.evidence_ids}
        known={e.evidence_id for e in insight.evidence}
        insight.evidence.extend(e for e in self.workflow_evidence.get(key,[]) if e.evidence_id in retrieved-known)
        source_ids={sid for e in insight.evidence for sid in e.source_event_ids}
        known_events={e['event_id'] for e in insight.source_events}
        insight.source_events.extend(e.model_dump(mode='json') for e in self.processor.matches[self.match.match_id].events
            if e.event_id in source_ids and e.event_id not in known_events)
        self.emit_insight('NARRATIVE_READY',insight,result.trace_id)

    async def on_event(self,envelope,status):
        if status != 'PROCESSED' or not isinstance(envelope,EventEnvelope): return
        from datetime import datetime, timezone
        self.received_at = datetime.now(timezone.utc).isoformat()
        self.received_times.append(time.monotonic())
        self.clock = envelope.simulation_timestamp
        event = envelope.payload
        if event.event_type.value == 'GOAL': self.score = (event.home_score,event.away_score)
        # These are last observed event locations, not tracking positions.
        if event.player_id and event.team_id == self.processor.matches[self.match.match_id].possession_team_id:
            self.possession_player = event.player_id
        if event.event_type.value == 'POSSESSION_CHANGE': self.possession_player = None
        adapter = self.processor.matches[self.match.match_id]
        snapshot = adapter.latest_snapshot
        if snapshot:
            for moment in snapshot.detected_moments:
                key = moment_key(moment)
                if key in self.insights: continue
                evidence = [TraceEvidence(**item.model_dump(), source_paths=[f"evidence/{index}"])
                    for index, item in enumerate(snapshot.evidence) if item.evidence_id in moment.evidence_ids]
                source_ids = set(moment.source_event_ids)
                for item in evidence: source_ids.update(item.source_event_ids)
                insight = Insight(insight_id=key, moment=moment, evidence=evidence,
                    source_events=[item.model_dump(mode='json') for item in adapter.events if item.event_id in source_ids],
                    workflow_status='NO_EXPLANATION',change=change_projection(None,snapshot.tactical_states,moment.timestamp))
                self.insights[key] = insight
                self.emit_insight('MOMENT_DETECTED', insight, envelope.correlation_id)
        serialized = event.model_dump(mode='json')
        self.events.append(serialized)
        self.emit(EventMessage,payload=serialized,trace_id=envelope.correlation_id)
        checkpoint_ready = not self.pause_checkpoint or (snapshot and snapshot.tactical_states and snapshot.tactical_states[-1].timestamp >= self.pause_at)
        event_ready = self.pause_event_id is None or event.event_id == self.pause_event_id
        if self.pause_at is not None and self.clock >= self.pause_at and checkpoint_ready and event_ready:
            self.pause_at = None
            self.pause_event_id = None
            self.pause_checkpoint = False
            self.status = 'paused'
            self.emit(Update,type='MATCH_STATE_UPDATED',payload=self.state())
            while self.status == 'paused':
                self.wake.clear()
                await self.wake.wait()
        self.emit(Update,type='MATCH_STATE_UPDATED',payload=self.state())

    async def delay(self,simulation_gap):
        remaining = simulation_gap
        while remaining > 0:
            self.wake.clear()
            if self.status == 'paused':
                await self.wake.wait()
                continue
            started = time.monotonic()
            speed = self.speed
            try: await asyncio.wait_for(self.wake.wait(),min(.1,remaining/speed))
            except TimeoutError: pass
            remaining -= (time.monotonic()-started)*speed

    async def replay(self):
        if self.config.event_source == 'eventhub':
            from simulator.realtime.azure import AzureEventHubSource, AzureEventHubConsumer
            self.source = AzureEventHubSource(AzureEventHubConsumer(self.config.azure))
        else:
            self.source = LocalReplaySource(self.path,1,sleep=self.delay)
        try:
            await run_pipeline(self.source,self.processor,event_callback=self.on_event,queue_size=self.config.queue_size)
            self.status = 'complete'
        except asyncio.CancelledError: raise
        except Exception: self.status = 'error'
        finally:
            self.emit(Update,type='MATCH_STATE_UPDATED',payload=self.state())

    async def control(self,action,speed):
        async with self.control_lock:
            return await self._control(action,speed)

    async def _control(self,action,speed):
        # Caller holds control_lock, including guided-stage target changes.
        if self.closed:raise SessionClosedError()
        if action == 'start':
            if self.task and not self.task.done(): return self.state()
            await self.close_workflows()
            if self.closed:raise SessionClosedError()
            self.observer.traces.clear()
            self.events.clear();self.insights.clear();self.score=(0,0);self.clock=0;self.possession_player=None
            self.processor = ProductProcessor(self)
            self.temporal = Temporal();self.temporal_key=None;self.sensitivity_cache.clear();self.workflow_evidence.clear()
            self.received_times.clear();self.received_at=None
            self.speed=speed;self.status='running'
            self.emit(Update,type='RESET',payload=self.snapshot())
            self.processor.runner_factory=self.observer.runner
            if self.preparer:self.preparer.start()
            self.task=asyncio.create_task(self.replay())
        elif action == 'pause' and self.status=='running': self.status='paused'
        elif action == 'resume' and self.status=='paused': self.status='running'
        elif action == 'speed': self.speed=speed
        self.wake.set()
        self.emit(Update,type='MATCH_STATE_UPDATED',payload=self.state())
        return self.state()

    def subscribe(self,last_id=None):
        queue=asyncio.Queue(maxsize=64)
        self.subscribers.add(queue)
        if last_id is None or not self.journal or last_id < self.journal[0].sequence_number-1 or last_id>self.cursor:
            initial=[Update(type='RESET',match_id=self.match.match_id,timestamp=self.clock,
                sequence_number=self.cursor,payload=self.snapshot())]
        else: initial=[m for m in self.journal if m.sequence_number>last_id]
        return queue,initial

    async def close_workflows(self):
        if self.preparer:await self.preparer.cancel()
        tasks=list(self.workflow_tasks)
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks,return_exceptions=True)

    async def close(self):
        self.closed=True
        await self.close_workflows()
        if self.task and not self.task.done():
            self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
