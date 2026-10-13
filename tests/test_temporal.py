"""Real-engine temporal contracts, provenance and controlled sensitivity."""
import asyncio
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from simulator.presentation.api import create_app, DEFAULT_PATH
from simulator.presentation.service import MatchService
from simulator.presentation.temporal import (
    dimensions, temporal_projection, comparison, sensitivity, sequence_choices,
    truth_projection, downstream_verification, guide, CAVEAT,
)
from simulator.production.config import AppConfig
from simulator.agentic.models import WorkflowResult, EvidenceStatement
from simulator.intelligence.engine import IntelligenceEngine
from simulator.realtime.models import parse_event, EventEnvelope

@pytest.fixture(scope='module')
def actual():
    s=MatchService(DEFAULT_PATH,AppConfig())
    es=[parse_event(json.loads(l)) for l in DEFAULT_PATH.read_text().splitlines()]
    report=IntelligenceEngine(s.match,es).analyze()
    return s,es,report

@pytest.fixture(scope='module')
def workflow():
    value=json.loads(Path('data/generated/phase5/paced/agent_results.json').read_text())[0]['result']
    return WorkflowResult.model_validate(value)


def test_vector_and_dna_repeatable(actual):
    _,es,r=actual
    a=temporal_projection(r.rolling_windows,r.tactical_states,es)
    assert a==temporal_projection(r.rolling_windows,r.tactical_states,es)
    assert len(a.current.dimensions)==8
    assert a.current.dimensions[0].home==r.rolling_windows[-2].home_possession_pct
    for i,d in enumerate(a.dna):
        vals=[p.dimensions[i].home for p in a.trajectory if p.dimensions[i].home is not None]
        assert d.home==round(sum(vals)/len(vals),2)
    assert all(p.evidence_ids for p in a.trajectory)
    assert a.trajectory[-1].score==[1,2]


def test_no_events_no_measured_vector(actual):
    s,_,_=actual
    assert s.state().temporal.current is None
    w=s.processor.matches[s.match.match_id].engine._calc_window([],0,300)
    assert dimensions(w)[6].home is None


def test_same_score_different_actual_stories():
    r=comparison();a,b=r['matches']
    assert r['same_score'] and a['score']==b['score']==[0,1]
    assert a['temporal']['dna']!=b['temporal']['dna']
    assert [p['state'] for p in a['temporal']['trajectory']]!=[p['state'] for p in b['temporal']['trajectory']]
    for m in (a,b):
        assert m['turning_points']
        for p in m['turning_points']:
            assert set(p['evidence_ids'])=={e['evidence_id'] for e in p['evidence']}
            assert set(p['source_event_ids'])=={e['event_id'] for e in p['source_events']}
    assert comparison() is r


def test_sensitivity_existing_engine_fixed_horizon(actual):
    s,es,_=actual;timestamp=1500
    choices=sequence_choices(es,timestamp)
    sid=next(c['sequence_id'] for c in choices if c['events']>=5)
    result=sensitivity(s.match,es,timestamp,sid)
    expected=[e for e in es if e.timestamp<=timestamp and getattr(e,'possession_sequence_id',None)!=sid]
    expected_w=IntelligenceEngine(s.match,expected)._calc_window(expected,timestamp,300)
    assert result['without_sequence']==dimensions(expected_w)
    assert result['caveat']==CAVEAT
    assert result==sensitivity(s.match,es,timestamp,sid)
    assert result['removed_event_ids']
    assert all(e.timestamp<=timestamp for e in es if e.event_id in result['removed_event_ids'])
    assert any(d['home'] or d['away'] for d in result['deltas'])
    with pytest.raises(ValueError):sensitivity(s.match,es,timestamp,'invented')


def test_actual_truth_claims_and_retrieved_citations(workflow):
    t=truth_projection(workflow)
    assert t.status=='SUPPORTED'
    statements={c.statement_id:c for c in workflow.state.statements}
    assert t.claims
    assert all(c.text==statements[c.statement_id].text for c in t.claims)
    assert any(s.tools for s in t.specialists)
    assert all('arguments' not in tool for s in t.specialists for tool in s.tools)
    assert t.confidence<=t.confidence_cap


def test_conflict_partial_and_rejection_remain_visible(workflow):
    partial=workflow.model_copy(deep=True)
    partial.workflow_status='PARTIAL';partial.state.missing_agents=['player'];partial.state.confidence_cap=.4
    partial.state.statements.append(EvidenceStatement(statement_id='actual-conflict-test',text='Pressure and territory point in conflicting directions.',category='contradiction',evidence_ids=[partial.state.evidence[0].evidence_id]))
    t=truth_projection(partial)
    assert t.status=='PARTIALLY SUPPORTED' and t.missing_agents==['player']
    assert 'not a specialist vote' in t.arbitration
    assert 'conflicting directions' in ' '.join(t.caveats)
    rejected=partial.model_copy(deep=True);rejected.state.explanation_validated=False
    t=truth_projection(rejected)
    assert t.status=='REJECTED' and t.confidence is None


def test_downstream_artifact_not_continuous_live(tmp_path):
    path=tmp_path/'verification.json'
    data={'status':'live_verified_browser','verified_at_date':'2026-10-05',
        'workspace_id':'00000000-0000-0000-0000-000000000000',
        'eventstream_id':'00000000-0000-0000-0000-000000000001',
        'eventhouse_id':'00000000-0000-0000-0000-000000000002',
        'replay_run_id':'0'*32,'events_published':1728,'events_processed':1728,
        'table':'MatchEvents','kql_result':{'Rows':1728,'Unique':1728,'Invalid':0}}
    path.write_text(json.dumps(data))
    v=downstream_verification(path)
    assert v['status']=='LAST DOWNSTREAM VERIFICATION'
    assert not v['continuous_verification'] and v['traffic'] is None
    assert v['date']=='2026-10-05' and v['rows']==1728
    assert 'groups/00000000-0000-0000-0000-000000000000' in v['url']
    assert downstream_verification(tmp_path/'missing.json')['status']=='UNVERIFIED'
    data['kql_result']['Invalid']=1
    path.write_text(json.dumps(data));assert downstream_verification(path)['status']=='UNVERIFIED'
    data['kql_result']['Invalid']=0;data['verified_at_date']='2099-01-01'
    path.write_text(json.dumps(data));assert downstream_verification(path)['status']=='UNVERIFIED'


def test_guide_uses_real_checkpoint_order(actual):
    _,es,r=actual;g=guide(str(DEFAULT_PATH))
    assert [x['timestamp'] for x in g]==[0,600,1200,1274.43,1500]
    assert 'prior' in g[0]['note']
    assert any(e.event_type.value=='GOAL' and e.timestamp==g[3]['timestamp'] for e in es)
    assert any(t.timestamp==g[2]['timestamp'] for t in r.state_transitions)


def test_additive_contract_cloud_honesty_and_future_guard():
    app=create_app(config=AppConfig())
    with TestClient(app) as c:
        mid=c.get('/api/matches').json()[0]['match_id']
        snapshot=c.get(f'/api/matches/{mid}').json()
        assert {'match','state','events','insights','stream_cursor'}<=set(snapshot)
        assert snapshot['state']['temporal']['current'] is None
        d=c.get('/api/data-plane').json()
        assert d['input']=='local' and d['processed_events']==0 and d['processed_rate']==0
        assert d['published_events'] is None and d['eventstream_current_traffic'] is None
        assert d['last_processed_at'] is None
        assert c.get(f'/api/matches/{mid}/sensitivity?sequence_id=fake&timestamp=1').status_code==400
        assert c.get(f'/api/matches/{mid}/sensitivity?sequence_id=fake&timestamp=0').status_code==404
        assert c.post(f'/api/matches/{mid}/guide/1',headers={'origin':'https://hostile.example'}).status_code==403
        assert c.post(f'/api/matches/{mid}/guide/99').status_code==404
        assert c.get('/api/comparison').json()['same_score']
        import time
        app.state.service.received_times.extend([time.monotonic()]*2000)
        assert c.get('/api/data-plane').json()['processed_rate_lower_bound'] is True


def test_real_processing_temporal_cache_and_evidence_resolution(actual):
    _,es,_=actual
    async def run():
        s=MatchService(DEFAULT_PATH,AppConfig())
        for i,e in enumerate(es):
            if e.timestamp>1500:break
            env=EventEnvelope.wrap(e,i+1)
            record=await s.processor.process(env)
            await s.on_event(env,record)
        first=s.state().temporal
        assert s.state().temporal is first
        for insight in s.insights.values():
            if not insight.truth:continue
            ids={e.evidence_id for e in insight.evidence}
            assert all(set(c.evidence_ids)<=ids for c in insight.truth.claims)
            assert all(set(t['evidence_ids'])<=ids for specialist in insight.truth.specialists for t in specialist.tools)
            assert insight.change
            assert all(d.current_timestamp<=insight.moment.timestamp for d in insight.change.deltas)
        assert s.received_at and s.received_times
        return s
    s=asyncio.run(run())
    assert s.state().temporal.current.timestamp==1200


def test_guided_replay_actually_pauses_without_static_snapshots():
    async def run():
        s=MatchService(DEFAULT_PATH,AppConfig())
        async def instant(gap):return None
        s.delay=instant;s.pause_at=600
        await s.control('start',100)
        for _ in range(300):
            if s.status=='paused':break
            await asyncio.sleep(.01)
        assert s.status=='paused' and s.clock>=600
        count=s.state().total_events
        await asyncio.sleep(.02)
        assert s.state().total_events==count
        assert any(i.moment.moment_type.value=='PRESSURE_SHIFT' for i in s.insights.values())
        s.pause_at=1200;s.pause_checkpoint=True;await s.control('resume',100)
        for _ in range(300):
            if s.status=='paused':break
            await asyncio.sleep(.01)
        assert s.status=='paused' and s.state().temporal.current.timestamp==1200
        stage=guide(str(DEFAULT_PATH))[3]
        s.pause_at=stage['timestamp'];s.pause_event_id=stage['event_id']
        await s.control('resume',100)
        for _ in range(300):
            if s.status=='paused':break
            await asyncio.sleep(.01)
        assert s.status=='paused' and s.clock==1274.43
        assert s.score==(1,1), 'A SHOT shares the goal timestamp; pause only after the actual GOAL'
        assert any(i.moment.moment_type.value=='GOAL' and i.moment.timestamp==1274.43 and i.truth for i in s.insights.values())
        s.pause_at=1500;s.pause_checkpoint=True
        await s.control('resume',100)
        for _ in range(300):
            if s.status=='paused':break
            await asyncio.sleep(.01)
        assert s.status=='paused' and s.state().temporal.current.timestamp==1500
        assert s.state().temporal.current.state=='AWAY_CONTROL'
        await s.close()
    asyncio.run(run())
