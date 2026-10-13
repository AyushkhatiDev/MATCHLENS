"""Fresh, private, recorded-demo preparation using the unchanged product workflow.

No future evidence is published. A result is attached only after the visible
replay produces the identical hero packet. This is not a persisted result cache.
"""
import asyncio
import json
import uuid
from pathlib import Path
from simulator.realtime.models import EventEnvelope, parse_event
from simulator.realtime.intelligence import moment_key
from simulator.production.observability import Observability
from .execution import Execution, pending, observed_steps


def packet(insight):
    ids=set(insight.explanation.evidence_ids) if insight.explanation else set()
    return (insight.moment.model_dump(), insight.explanation.model_dump() if insight.explanation else None,
            {e.evidence_id:e.model_dump() for e in insight.evidence if e.evidence_id in ids})


class PreparationObserver(Observability):
    def __init__(self,preparation):
        super().__init__();self.preparation=preparation
    def record(self,state,*args,**kwargs):
        super().record(state,*args,**kwargs)
        self.preparation.progress(state.trace_id)


class HeroPreparation:
    def __init__(self,service,semaphore=None):
        self.service=service;self.task=None;self.private=None;self.result=None
        self.published=False;self.failed=False;self.run_id=None;self.semaphore=semaphore or asyncio.Semaphore(1)
        service.preparer=self
        service.observer=PreparationObserver(self)
        service.processor.runner_factory=service.observer.runner

    def start(self):
        self.result=None;self.private=None;self.published=False;self.failed=False;self.run_id=uuid.uuid4().hex
        self.task=asyncio.create_task(self.prepare())

    async def cancel(self):
        if self.task and not self.task.done():
            self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
        self.task=None

    def hero(self,service):
        return next((i for i in service.insights.values() if i.moment.moment_type.value=='GOAL'
                     and service.hero_event_id in i.moment.source_event_ids),None)

    def progress(self,trace_id):
        insight=self.hero(self.service)
        if insight is None or self.published:return
        insight.execution=pending('foundry')
        insight.execution.trace_id=trace_id;insight.execution.run_id=self.run_id
        insight.execution.steps=observed_steps(trace_id,self.service.observer.traces)
        self.service.emit_insight('NARRATIVE_READY',insight,trace_id)

    async def publish(self):
        visible=self.hero(self.service)
        prepared=self.hero(self.private) if self.private else None
        if visible is None or self.published:return
        if self.failed:
            visible.workflow_status='FAILED'
            visible.execution=Execution(mode='foundry',status='FOUNDRY UNAVAILABLE',state='UNAVAILABLE',run_id=self.run_id)
            self.published=True
            self.service.emit_insight('NARRATIVE_READY',visible)
            return
        if prepared is None:return
        visible.execution.run_id=self.run_id
        if prepared.workflow_status=='PROCESSING':return
        self.published=True
        if packet(visible)!=packet(prepared):
            visible.workflow_status='FAILED'
            visible.execution=Execution(mode='foundry',status='FOUNDRY RESULT REJECTED',state='REJECTED',run_id=self.run_id)
        elif self.result is not None:
            await self.service.result(self.result)
            visible.execution.run_id=self.run_id
        else:
            visible.workflow_status='FAILED'
            visible.execution=Execution(mode='foundry',status='FOUNDRY UNAVAILABLE',state='UNAVAILABLE',run_id=self.run_id)
        self.service.workflow_evidence.pop(moment_key(visible.moment),None)
        self.service.emit_insight('NARRATIVE_READY',visible,visible.trace_id)

    async def prepare(self):
        from .service import MatchService
        private=None
        try:
            async with self.semaphore:
                private=MatchService(self.service.path,self.service.config)
                self.private=private
                private.observer=self.service.observer
                private.processor.runner_factory=private.observer.runner
                async def capture(result):
                    await private.result(result)
                    self.result=result
                private.processor.result_callback=capture
                for index,line in enumerate(Path(private.path).read_text().splitlines()):
                    event=parse_event(json.loads(line));envelope=EventEnvelope.wrap(event,index)
                    status=await private.processor.process(envelope)
                    await private.on_event(envelope,status)
                    if event.event_id==private.hero_event_id:break
                tasks=list(private.workflow_tasks)
                if tasks:await asyncio.gather(*tasks)
                await self.publish()
        except asyncio.CancelledError:raise
        except Exception:
            # Failure never invokes a mock runtime or exposes SDK error inputs.
            self.failed=True
            await self.publish()
        finally:
            if private:await private.close()
