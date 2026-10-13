"""Opt-in genuine hero verification through the product's incremental replay path."""
import argparse
import asyncio
import json
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from simulator.agentic.config import AgenticConfig
from simulator.presentation.service import MatchService
from simulator.production.config import AppConfig
from simulator.realtime.models import EventEnvelope, parse_event

PATH=Path(__file__).resolve().parents[1]/'data/generated/phase5/seed99/match.jsonl'

async def verify(config):
    service=MatchService(PATH,AppConfig(environment='demo',agent=config,foundry_hero_only=True))
    start=time.perf_counter()
    try:
        for index,line in enumerate(PATH.read_text().splitlines()):
            event=parse_event(json.loads(line))
            envelope=EventEnvelope.wrap(event,index)
            status=await service.processor.process(envelope)
            await service.on_event(envelope,status)
            if event.event_id==service.hero_event_id:break
        # Replay truth has already reached the goal while Foundry runs independently.
        tasks=list(service.workflow_tasks)
        if tasks:await asyncio.wait_for(asyncio.gather(*tasks),config.timeout_seconds*4+15)
        hero=next(i for i in service.insights.values() if i.moment.moment_type.value=='GOAL' and service.hero_event_id in i.moment.source_event_ids)
        execution=hero.execution
        steps={s.stage:s.status for s in execution.steps} if execution else {}
        specialists=hero.truth.specialists if hero.truth else []
        connected=any(s.stage in ('Tactical specialist','Player specialist','Context specialist','Explanation') and s.status=='OK' for s in execution.steps) if execution else False
        checks={
            'Azure authentication':connected,
            'Project connection':connected,
            'Deployment/model availability':connected,
            'Specialists':len(specialists)==3 and all(s.validation_state=='SUPPORTED' and s.tools for s in specialists),
            'Explanation':steps.get('Explanation')=='OK' and hero.agent_explanation is not None,
            'Grounding validation':steps.get('Grounding validation')=='OK' and bool(hero.truth and hero.truth.status=='SUPPORTED'),
            'Arbitration':steps.get('Arbitration')=='OK',
            'Claim provenance':bool(hero.truth and hero.truth.claims) and all(c.evidence_ids and set(c.evidence_ids)<={e.evidence_id for e in hero.evidence} for c in hero.truth.claims) if hero.truth else False,
            'Complete hero scenario':bool(execution and execution.status=='FOUNDRY LIVE' and hero.workflow_status=='COMPLETE' and hero.narrative),
            'No mock fallback':bool(execution and execution.mode=='foundry' and not execution.fallback),
            'Only hero invokes agents':service.processor.metrics.agent_workflows_triggered==1,
        }
        by_role={s.agent:s for s in specialists}
        for role in ('tactical','player','context'):
            result=by_role.get(role)
            checks[role.title()]=bool(result and result.validation_state=='SUPPORTED' and result.tools
                                     and steps.get(role.title()+' specialist')=='OK')
        return {'timestamp':datetime.now(timezone.utc).isoformat(),'success':all(checks.values()),
            'status':execution.status if execution else 'FOUNDRY UNAVAILABLE','workflow_status':hero.workflow_status,
            'hero_time':hero.moment.timestamp,'source_event_id':service.hero_event_id,'trace_id':hero.trace_id,
            'latency_seconds':round(time.perf_counter()-start,3),'fallback':bool(execution and execution.fallback),
            'connection_check_method':'Successful genuine authenticated project inference; no separate management probe.',
            'checks':checks,'execution':execution.model_dump(mode='json') if execution else None,
            'specialist_failures':sum(not checks[role.title()] for role in ('tactical','player','context')),
            'grounding':steps.get('Grounding validation','NOT RUN'),'arbitration':steps.get('Arbitration','NOT RUN'),
            'failed_stages':[{'stage':t['agent'],'status':t['status'],'codes':t['errors']}
                             for t in service.observer.traces if t['status']=='ERROR']}
    except Exception:
        return {'timestamp':datetime.now(timezone.utc).isoformat(),'success':False,'status':'FOUNDRY UNAVAILABLE',
            'latency_seconds':round(time.perf_counter()-start,3),'fallback':False,'error':'Hero verification could not complete; credentials and exception inputs withheld.'}
    finally:await service.close()

def summarize(results):
    latencies=[r['latency_seconds'] for r in results]
    consecutive=longest=0
    for result in results:
        consecutive=consecutive+1 if result['success'] else 0
        longest=max(longest,consecutive)
    return {'attempts':len(results),'successes':sum(r['success'] for r in results),
        'failures':sum(not r['success'] for r in results),
        'rejections':sum(r['status']=='FOUNDRY RESULT REJECTED' for r in results),
        'specialist_failures':sum(r.get('specialist_failures',0) for r in results),
        'fallback_count':sum(r['fallback'] for r in results),
        'longest_success_streak':longest,'ending_success_streak':consecutive,
        'latency_seconds':{'min':min(latencies),'median':statistics.median(latencies),'max':max(latencies)},
        'scope':'Fresh real process_moment replay/inference per attempt; no cached health or model answer. Failures include rejections.'}

def preflight_checks(result):
    checks=result.get('checks',{})
    return {label:bool(checks.get(key,False)) for label,key in (
        ('Project','Project connection'),('Model','Deployment/model availability'),
        ('Tactical','Tactical'),('Player','Player'),('Context','Context'),('Explanation','Explanation'),
        ('Grounding','Grounding validation'),('Arbitration','Arbitration'),
        ('Hero scenario','Complete hero scenario'),('No fallback','No mock fallback'))}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs',type=int,default=1,choices=range(1,11))
    parser.add_argument('--preflight',action='store_true',help='Print per-stage local demo precheck; each attempt is real inference.')
    parser.add_argument('--demo-profile',action='store_true',help='Use checked-in existing hero project defaults, preserving environment overrides.')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    try:
        env=dict(os.environ)
        if args.demo_profile:
            profile=Path(__file__).resolve().parents[1]/'deploy/foundry-demo.env.example'
            defaults=dict(line.split('=',1) for line in profile.read_text().splitlines() if line and not line.startswith('#'))
            env={**defaults,**env}
        config=AgenticConfig.from_env(env)
        if config.mode!='foundry':raise ValueError()
    except Exception:
        if args.preflight:
            print('FOUNDRY DEMO PRECHECK')
            for name in preflight_checks({}):print(f'  FAIL {name}')
        print('FAIL: AGENTIC_MODE=foundry and existing project/model configuration required. Values withheld.')
        return 1
    async def repeat():return [await verify(config) for _ in range(args.runs)]
    results=asyncio.run(repeat())
    for index,result in enumerate(results,1):
        if args.preflight:
            print('FOUNDRY DEMO PRECHECK')
            for name,passed in preflight_checks(result).items():print(f"  {'PASS' if passed else 'FAIL'} {name}")
        print(f"Run {index}: {result['status']} | {'PASS' if result['success'] else 'FAIL'} | {result['latency_seconds']}s | fallback={result['fallback']}")
        for name,passed in result.get('checks',{}).items():print(f"  {'PASS' if passed else 'FAIL'} {name}")
    summary=summarize(results)
    print(json.dumps(summary))
    if args.report:args.report.write_text(json.dumps({'summary':summary,'runs':results},indent=2)+'\n')
    return 0 if all(r['success'] for r in results) else 1

if __name__=='__main__':raise SystemExit(main())
