"""Verify fresh genuine hero runs through the deployed HTTPS API and SSE."""
import argparse
import asyncio
from datetime import datetime,timezone
import json
from pathlib import Path
import statistics
import time
from urllib.parse import urlsplit
import httpx


def public_url(value):
    url=urlsplit(value)
    if url.scheme!='https' or not url.hostname or url.hostname in ('localhost','127.0.0.1') or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
        raise argparse.ArgumentTypeError('Use a public HTTPS origin without credentials or a path.')
    return value.rstrip('/')


async def verify_run(api,number):
    started=time.perf_counter();session=None;stream_task=None;messages=[]
    async with httpx.AsyncClient(timeout=30,follow_redirects=False) as client:
        try:
            response=await client.get(api+'/health');response.raise_for_status();health=response.json()
            if not health.get('production_demo'):raise ValueError('Not a deployed public demo')
            origin=health['frontend_origin']
            response=await client.post(api+'/api/sessions',headers={'Origin':origin});response.raise_for_status()
            session=response.json()['session_id'];base=api+'/sessions/'+session
            response=await client.get(base+'/api/matches',headers={'Origin':origin});response.raise_for_status()
            match=response.json()[0];mid=match['match_id']
            if match['runtime_mode']!='foundry':raise ValueError('Foundry required')
            ready=asyncio.Event()
            async def observe():
                async with client.stream('GET',base+f'/api/matches/{mid}/stream',timeout=210) as stream:
                    stream.raise_for_status()
                    async for line in stream.aiter_lines():
                        if line.startswith('data: '):
                            message=json.loads(line[6:]);messages.append(message)
                            if len(messages)>3000:messages.pop(0)
                            ready.set()
            stream_task=asyncio.create_task(observe())
            await asyncio.wait_for(ready.wait(),20)
            response=await client.post(base+f'/api/matches/{mid}/guide/0',headers={'Origin':origin});response.raise_for_status()
            for _ in range(40):
                state=(await client.get(base+f'/api/matches/{mid}/state')).json()
                if state['status']=='paused':break
                await asyncio.sleep(.1)
            response=await client.post(base+f'/api/matches/{mid}/guide/3',headers={'Origin':origin});response.raise_for_status()
            hero=None
            while time.perf_counter()-started<240:
                response=await client.get(base+f'/api/matches/{mid}/moments');response.raise_for_status()
                hero=next((i for i in response.json() if i['moment']['moment_type']=='GOAL' and abs(i['moment']['timestamp']-1274.43)<.001),None)
                if hero and hero['execution']['state'] not in ('PENDING','NOT REQUESTED'):break
                if stream_task.done():await stream_task;raise ValueError('SSE ended before hero')
                await asyncio.sleep(.5)
            if hero is None:raise ValueError('Hero missing')
            execution=hero.get('execution') or {};truth=hero.get('truth') or {}
            stages={s['stage']:s['status'] for s in execution.get('steps',[])}
            specialists={s['agent']:s for s in truth.get('specialists',[])}
            sources={e['event_id']:e for e in hero['source_events']}
            canonical='c266df64-a8f2-4daf-ad4b-36174aca114b'
            claims=truth.get('claims',[]);evidence={e['evidence_id']:e for e in hero['evidence']}
            # Allow the final SSE delivery to arrive independently of REST polling.
            for _ in range(20):
                if any(m['type']=='NARRATIVE_READY' and m.get('payload',{}).get('execution',{}).get('run_id')==execution.get('run_id') and m['payload']['execution']['state']=='COMPLETE' for m in messages):break
                await asyncio.sleep(.1)
            checks={
                'Public API reachability':True,'Health endpoint':health['application']=='running',
                'Match replay':any(m['type']=='EVENT_RECEIVED' for m in messages),
                'Hero source event':canonical in sources and sources[canonical]['event_type']=='GOAL' and sources[canonical]['timestamp']==1274.43,
                'Genuine Foundry project/model connectivity':execution.get('mode')=='foundry' and stages.get('Explanation')=='OK',
                **{f'{role.title()} specialist':specialists.get(role,{}).get('validation_state')=='SUPPORTED' and bool(specialists.get(role,{}).get('tools')) for role in ('tactical','player','context')},
                'Explanation':bool(hero.get('agent_explanation')) and stages.get('Explanation')=='OK',
                'Grounding':truth.get('status')=='SUPPORTED' and stages.get('Grounding validation')=='OK',
                'Arbitration':stages.get('Arbitration')=='OK',
                'Claim provenance':bool(claims) and all(c['evidence_ids'] and set(c['evidence_ids'])<=evidence.keys() for c in claims),
                'Final hero result':hero['workflow_status']=='COMPLETE' and execution.get('status')=='FOUNDRY LIVE' and bool(hero.get('narrative')),
                'No fallback':execution.get('fallback') is False,
                'Fresh execution ID':bool(execution.get('run_id')),
                'SSE hero delivery':any(m['type']=='NARRATIVE_READY' and m.get('payload',{}).get('execution',{}).get('run_id')==execution.get('run_id') and m['payload']['execution']['state']=='COMPLETE' for m in messages),
            }
            return {'run':number,'success':all(checks.values()),'total_latency_seconds':round(time.perf_counter()-started,3),
                    'foundry_status':execution.get('status'),'specialists':{r:s.get('validation_state') for r,s in specialists.items()},
                    'grounding':stages.get('Grounding validation'),'arbitration':stages.get('Arbitration'),
                    'fallback_count':int(execution.get('fallback') is not False),'run_id':execution.get('run_id'),
                    'checks':checks,'steps':execution.get('steps',[]),'connection_check_method':'Successful genuine project inference on the deployed server identity.'}
        except Exception as error:
            return {'run':number,'success':False,'total_latency_seconds':round(time.perf_counter()-started,3),
                    'foundry_status':'FOUNDRY UNAVAILABLE','fallback_count':0,
                    'error_type':type(error).__name__,
                    'http_status':error.response.status_code if isinstance(error,httpx.HTTPStatusError) else None,
                    'error':'Production verification failed; raw exception and authentication inputs withheld.'}
        finally:
            if stream_task:
                stream_task.cancel();await asyncio.gather(stream_task,return_exceptions=True)
            if session:
                try:await client.delete(api+'/api/sessions/'+session)
                except Exception:pass


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api',required=True,type=public_url)
    parser.add_argument('--runs',type=int,default=1,choices=range(1,11))
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    async def runs():return [await verify_run(args.api,i) for i in range(1,args.runs+1)]
    results=asyncio.run(runs())
    identifiers=[r.get('run_id') for r in results if r.get('run_id')]
    fresh=len(identifiers)==len(results) and len(set(identifiers))==len(results)
    latency=[r['total_latency_seconds'] for r in results]
    report={'verified_at':datetime.now(timezone.utc).isoformat(),'api':args.api,'runs':results,'fresh_execution_ids':fresh,
            'successes':sum(r['success'] for r in results),'failures':sum(not r['success'] for r in results),
            'fallback_count':sum(r['fallback_count'] for r in results),
            'latency_seconds':{'min':min(latency),'median':statistics.median(latency),'max':max(latency)}}
    for r in results:
        print(f"Run {r['run']}: {'PASS' if r['success'] else 'FAIL'} | {r['foundry_status']} | {r['total_latency_seconds']}s | fallback={r['fallback_count']}")
        if not r['success']:print('  Failed checks: '+', '.join(k for k,v in r.get('checks',{}).items() if not v))
    print(f"Successes={report['successes']}/{len(results)} fresh={fresh} fallback={report['fallback_count']} latency={report['latency_seconds']}")
    if args.report:args.report.write_text(json.dumps(report,indent=2)+'\n')
    return 0 if all(r['success'] for r in results) and fresh else 1

if __name__=='__main__':raise SystemExit(main())
