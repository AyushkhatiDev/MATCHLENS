"""Bounded real timings around existing Agent Framework operations."""
import time
import hashlib
from simulator.realtime.logging import log
from datetime import datetime,timezone
from collections import deque
from simulator.agentic.runtime.runner import MatchAgentRunner

class Observability:
    def __init__(self):
        self.traces=deque(maxlen=2000)
        self.api_errors=0
    def record(self,state,stage,started,clock,*,status='OK',execution=None):
        self.traces.append(dict(trace_id=state.trace_id,match_id=state.match_id,
            event_id=state.triggered_moment.source_event_ids[-1] if state.triggered_moment.source_event_ids else None,
            moment_id=state.triggered_moment.moment_id,simulation_timestamp=state.current_time,agent=stage,
            started_at=started.isoformat(),completed_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=(time.perf_counter()-clock)*1000,status=status,
            tool_calls=[dict(tool=call.tool,status=call.status,evidence_ids=call.evidence_ids,error_code=call.error_code)
                for call in execution.tool_calls] if execution else [],
            evidence_ids=execution.evidence_ids if execution else state.explanation_context.evidence_ids,
            errors=[e.code for e in state.errors]))
        digest=lambda value:hashlib.sha256(str(value).encode()).hexdigest()[:16]
        log('AGENT_STAGE_COMPLETE',trace_id=digest(state.trace_id),match_id=digest(state.match_id),
            event_id=digest(self.traces[-1]['event_id']),moment_id=digest(state.triggered_moment.moment_id),
            agent=stage,status=status,duration_ms=self.traces[-1]['duration_ms'])
    def runner(self,store,config):return ObservedRunner(store,config,observer=self)

def distribution(values):
    values=sorted(values)
    if not values:return {'count':0,'mean_ms':None,'p50_ms':None,'p95_ms':None,'max_ms':None}
    return dict(count=len(values),mean_ms=sum(values)/len(values),p50_ms=values[int((len(values)-1)*.5)],
        p95_ms=values[int((len(values)-1)*.95)],max_ms=values[-1])

class ObservedRunner(MatchAgentRunner):
    def __init__(self,*args,observer,**kwargs):
        super().__init__(*args,**kwargs);self.observer=observer
    async def _stage(self,stage,state,scoped):
        started=datetime.now(timezone.utc);clock=time.perf_counter()
        try:await super()._stage(stage,state,scoped)
        finally:self.observer.record(state,stage,started,clock,status=state.trace_steps[-1].status if state.trace_steps else 'ERROR')
    async def _invoke(self,role,state,scoped,validate=True):
        started=datetime.now(timezone.utc);clock=time.perf_counter()
        result=await super()._invoke(role,state,scoped,validate)
        self.observer.record(state,role,started,clock,status=result[1].status,execution=result[1])
        return result

def metrics(service):
    processor=service.processor
    records=list(processor.records)
    workflows=list(processor.workflow_records)
    return dict(environment=service.config.environment,event_source=service.config.event_source,agent_runtime=service.config.agent.mode,
        pipeline=processor.metrics.model_dump(mode='json'),sse_subscribers=len(service.subscribers),api_errors=service.observer.api_errors,
        agent_success=sum(r['status']=='COMPLETE' for r in workflows),agent_partial=sum(r['status']=='PARTIAL' for r in workflows),
        explanation_validation_failures=sum(e['agent']=='Validation' and e['status']=='ERROR' for e in service.observer.traces),
        latency_samples={field:distribution([getattr(r,field) for r in records if getattr(r,field)>0])
            for field in ('processing_latency_ms','moment_detection_latency_ms','agent_trigger_latency_ms','end_to_end_latency_ms','queue_wait_latency_ms')},
        agent_latency=distribution([r['duration_ms'] for r in service.observer.traces if r['agent'] in ('tactical','player','context','explanation','narrative')]),
        sample_scope='bounded retained records, local unless environment explicitly states otherwise')

def health(service):
    pipeline='failed' if service.status=='error' or service.processor.failed_matches else service.status
    workflows=list(service.processor.workflow_records)
    failed=any(r['status'] in ('FAILED','PARTIAL') for r in workflows[-5:]) or (service.processor.metrics.agent_workflows_failed>0 and not workflows)
    cloud_wait=(service.config.agent.mode=='foundry' and not any(r['status']=='COMPLETE' for r in workflows)) or (service.config.event_source=='eventhub' and service.processor.metrics.events_processed==0)
    status='not_ready' if pipeline=='failed' else 'degraded' if failed or cloud_wait else 'healthy'
    return dict(status=status,application='running',event_pipeline=pipeline,
        agent_runtime={'mode':service.config.agent.mode,'status':'degraded' if failed else 'unverified' if cloud_wait else 'available'},
        event_source=service.config.event_source,database='not_used',stream={'status':'available','subscribers':len(service.subscribers)},
        cloud_probe='no outbound probe performed by health endpoint')
