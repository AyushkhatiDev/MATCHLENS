"""Actual local pipeline timing. Instant input isolates throughput, not broadcast pacing."""
import asyncio,json,time
from pathlib import Path
from simulator.production.observability import metrics
from simulator.presentation.api import DEFAULT_PATH
from simulator.presentation.service import MatchService
from simulator.realtime.pipeline import run_pipeline
from simulator.realtime.sources import LocalReplaySource
async def run():
    service=MatchService(DEFAULT_PATH)
    started=time.perf_counter()
    await run_pipeline(LocalReplaySource(DEFAULT_PATH,0),service.processor,event_callback=service.on_event)
    wall=time.perf_counter()-started
    report=metrics(service)
    report.update(wall_seconds=wall,events_per_second=service.processor.metrics.events_processed/wall,
        moment_count=len(service.insights),input='actual seed99 JSONL, instant source pacing',
        deployment='host Python local mock-agent pipeline; not cloud or production SLA')
    Path('docs/submission/performance-local.json').write_text(json.dumps(report,indent=2)+'\n')
    Path('docs/submission/agent-trace-goal.json').write_text(json.dumps([t for t in service.observer.traces if any(i.moment.moment_id==t['moment_id'] and i.moment.timestamp==t['simulation_timestamp'] and i.moment.moment_type.value=='GOAL' for i in service.insights.values())],indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('latency_samples','agent_latency')}))
asyncio.run(run())
