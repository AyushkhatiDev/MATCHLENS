"""Reproducible paced local-container demo and measured throughput, no fake events."""
import argparse,json,time
from pathlib import Path
from urllib.parse import urlsplit
import httpx
parser=argparse.ArgumentParser();parser.add_argument('--api',default='http://127.0.0.1:8001');args=parser.parse_args()
url=urlsplit(args.api)
if url.hostname not in ('localhost','127.0.0.1') or url.username or url.password:raise SystemExit('This probe is for local container execution only')
with httpx.Client(base_url=args.api,timeout=20) as client:
    ready=client.get('/ready');ready.raise_for_status()
    match=client.get('/api/matches').json()[0]
    if match['event_source']!='local' or match['runtime_mode']!='mock':raise SystemExit('Local mock profile required')
    mid=match['match_id'];started=time.perf_counter()
    control=client.post('/api/matches/'+mid+'/demo',json={'action':'start','speed':100});control.raise_for_status()
    while time.perf_counter()-started<150:
        state=client.get('/api/matches/'+mid+'/state').json()
        if state['status'] in ('complete','error'):break
        time.sleep(.5)
    wall=time.perf_counter()-started
    report=client.get('/metrics').json()
    moments=client.get('/api/matches/'+mid+'/timeline').json()
    expected=len(Path('data/generated/phase5/seed99/match.jsonl').read_text().splitlines())
    success=state['status']=='complete' and state['total_events']==expected and report['agent_success']==12 and len(moments)==56
    report.update(status='pass' if success else 'failed',wall_seconds=wall,events_per_second=state['total_events']/wall,
        deployment='local Docker Compose, paced 100x original simulation, actual mock Agent Framework',
        source_event_count_expected=expected,moment_count=len(moments),score=[state['home_score'],state['away_score']],
        latency_definition='Existing Phase 5 receive/queue/processing/workflow completion records; does not measure browser paint latency.',
        probe='REST state polling only in this smoke test; the actual frontend uses initial REST plus SSE')
    Path('docs/submission/performance-container.json').write_text(json.dumps(report,indent=2)+'\n')
    Path('docs/submission/container-state.json').write_text(json.dumps(state,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('latency_samples','agent_latency')}))
    raise SystemExit(0 if success else 1)
