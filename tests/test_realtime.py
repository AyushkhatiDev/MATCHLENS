"""Offline Phase 5 contracts, real Phase 4 execution and SDK transport fakes."""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from simulator.generation.generator import generate_match
from simulator.engine.match_engine import MatchEngine
from simulator.intelligence.engine import IntelligenceEngine
from simulator.intelligence.models import SignificanceTier
from simulator.models.events import EventType, MatchEvent, ShotEvent
from simulator.realtime.models import EventEnvelope, parse_event, PipelineMetrics
from simulator.realtime.sources import LocalReplaySource
from simulator.realtime.intelligence import IncrementalIntelligence, moment_key
from simulator.realtime.processor import EventProcessor
from simulator.realtime.pipeline import run_pipeline
from simulator.realtime.azure import (AzureConfig, AzureEventHubProducer, AzureEventHubConsumer,
    AzureEventHubSource, AzureTransportError, retry, serialize, deserialize)
from simulator.output.writer import write_events_jsonl

def run(coroutine):
    return asyncio.run(coroutine)

@pytest.fixture(scope='module')
def stream():
    match = generate_match(seed=99)
    return match, MatchEngine(match).simulate()

def envelope(stream, index=0):
    return EventEnvelope.wrap(stream[1][index], index)

def comparable(intel):
    value = intel.model_dump(mode='json')
    value.pop('generated_at')
    return value

def test_envelope_subtype_roundtrip(stream):
    event = next(e for e in stream[1] if isinstance(e, ShotEvent))
    original = EventEnvelope.wrap(event, 3)
    restored = EventEnvelope.model_validate_json(original.model_dump_json())
    assert isinstance(restored.payload, ShotEvent)
    assert restored.payload == event
    assert restored.produced_at.utcoffset().total_seconds() == 0

@pytest.mark.parametrize('field,value', [('event_id','wrong'),('match_id','wrong'),
    ('event_type','GOAL'),('simulation_timestamp',99),('schema_version','2.0'),
    ('sequence_number',-1),('sequence_number','1'),('produced_at','2026-01-01T00:00:00'),
    ('simulation_timestamp',float('nan'))])
def test_envelope_invalid_headers(stream, field, value):
    data = envelope(stream).model_dump(mode='json')
    data[field] = value
    with pytest.raises((ValidationError, ValueError)):
        EventEnvelope.model_validate(data)

def test_envelope_rejects_invalid_typed_payload(stream):
    event = next(e for e in stream[1] if isinstance(e, ShotEvent))
    value = EventEnvelope.wrap(event, 1).model_dump(mode='json')
    value['payload']['shot_quality'] = 1.5
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(value)

@pytest.mark.parametrize('speed', [1,10,20])
def test_replay_speed_order_and_original_time(tmp_path, speed):
    events = [MatchEvent(event_id=str(i), match_id='match', timestamp=t, minute=int(t//60),
                        event_type=EventType.MATCH_START) for i,t in enumerate([100,105,105,115])]
    path = write_events_jsonl(events, tmp_path/'match.jsonl')
    waits = []
    async def sleep(seconds): waits.append(seconds)
    async def exercise():
        source = LocalReplaySource(path,speed,sleep=sleep)
        await source.start()
        values = [e async for e in source.events()]
        await source.stop()
        return values
    values = run(exercise())
    assert waits == [5/speed,10/speed]
    assert [e.simulation_timestamp for e in values] == [100,105,105,115]
    assert [e.sequence_number for e in values] == [0,1,2,3]

@pytest.mark.parametrize('speed', [-1,float('inf'),float('nan')])
def test_bad_speed(speed):
    with pytest.raises(ValueError): LocalReplaySource('unused',speed)

def test_replay_rejects_backwards_time(tmp_path):
    events = [MatchEvent(event_id=str(i), match_id='m', timestamp=t, minute=0,
                        event_type=EventType.MATCH_START) for i,t in enumerate([10,5])]
    path = write_events_jsonl(events,tmp_path/'bad.jsonl')
    async def exercise():
        source = LocalReplaySource(path,0)
        await source.start()
        try:
            with pytest.raises(ValueError):
                _ = [e async for e in source.events()]
        finally:
            await source.stop()
    run(exercise())

def test_replay_stop_interrupts_wait(tmp_path):
    events = [MatchEvent(event_id=str(i), match_id='m', timestamp=t, minute=0,
                        event_type=EventType.MATCH_START) for i,t in enumerate([0,3600])]
    path = write_events_jsonl(events,tmp_path/'stop.jsonl')
    async def exercise():
        source = LocalReplaySource(path,1)
        await source.start()
        iterator = source.events()
        await anext(iterator)
        pending = asyncio.create_task(anext(iterator))
        await asyncio.sleep(.005)
        await source.stop()
        with pytest.raises(StopAsyncIteration): await asyncio.wait_for(pending,.2)
    run(exercise())

def test_dedup_conflict_late_unknown_and_metrics(stream):
    async def exercise():
        p = EventProcessor([stream[0]])
        first = envelope(stream)
        assert await p.process(first) == 'PROCESSED'
        assert await p.process(first) == 'DUPLICATE'
        conflict = EventEnvelope.wrap(first.payload,99)
        assert await p.process(conflict) == 'IDENTITY_CONFLICT'
        assert await p.process(envelope(stream,3)) == 'PROCESSED'
        assert await p.process(envelope(stream,1)) == 'LATE_REJECTED'
        unknown = first.payload.model_copy(update={'event_id':'other','match_id':'unknown'})
        assert await p.process(EventEnvelope.wrap(unknown,0)) == 'UNKNOWN_MATCH'
        assert await p.process({'schema_version':'future','password':'do-not-log'}) == 'INVALID_ENVELOPE'
        assert p.metrics.events_received == 7
        assert p.metrics.events_processed == 2
        assert p.metrics.events_duplicate == 1
        assert p.metrics.events_late == 1
        assert p.metrics.events_failed == 3
        assert len(p.matches[stream[0].match_id].events) == 2
        assert p.records[-2].status == 'UNKNOWN_MATCH'
        assert p.records[0].intelligence_updated_at is not None
        assert p.records[0].processing_latency_ms >= 0
        assert p.records[0].end_to_end_latency_ms >= p.records[0].ingestion_latency_ms
    run(exercise())

def test_duplicate_sequence_conflict(stream):
    async def exercise():
        p=EventProcessor([stream[0]])
        await p.process(envelope(stream))
        assert await p.process(EventEnvelope.wrap(stream[1][1],0)) == 'IDENTITY_CONFLICT'
        assert p.metrics.events_processed == 1
    run(exercise())

def test_tied_time_sequence_late(stream):
    async def exercise():
        p=EventProcessor([stream[0]])
        e=stream[1][0]
        await p.process(EventEnvelope.wrap(e,2))
        late=e.model_copy(update={'event_id':'late_same_time'})
        assert await p.process(EventEnvelope.wrap(late,1))=='LATE_REJECTED'
    run(exercise())

def test_nested_mutation_revalidated(stream):
    async def exercise():
        p=EventProcessor([stream[0]])
        value=envelope(stream).model_copy(deep=True)
        value.payload.match_id='wrong'
        assert await p.process(value)=='INVALID_ENVELOPE'
        assert not p.matches[stream[0].match_id].events
    run(exercise())

def test_unknown_roster_does_not_mutate(stream):
    async def exercise():
        p=EventProcessor([stream[0]])
        event=stream[1][1].model_copy(update={'team_id':'unknown'})
        assert await p.process(EventEnvelope.wrap(event,1))=='UNKNOWN_TEAM'
        assert not p.matches[stream[0].match_id].events
    run(exercise())

def test_match_isolation_and_concurrent_duplicate(stream):
    async def exercise():
        match2=stream[0].model_copy(update={'match_id':'match_two'})
        p=EventProcessor([stream[0],match2])
        first=envelope(stream)
        second=EventEnvelope.wrap(first.payload.model_copy(update={'match_id':'match_two'}),0)
        statuses=await asyncio.gather(p.process(first),p.process(first),p.process(second))
        assert sorted(statuses)==['DUPLICATE','PROCESSED','PROCESSED']
        assert len(p.matches[match2.match_id].events)==1
        await p.process(envelope(stream,1))
        assert p.matches[stream[0].match_id].summary.total_events==2
        assert p.matches[match2.match_id].summary.total_events==1
        assert p.matches[match2.match_id].players != p.matches[stream[0].match_id].players
    run(exercise())

def test_incremental_state_and_component_projection(stream, monkeypatch):
    def forbidden(*args,**kwargs): raise AssertionError('Batch analyze is forbidden on ingestion')
    monkeypatch.setattr(IntelligenceEngine,'analyze',forbidden)
    a=IncrementalIntelligence(stream[0])
    for e in stream[1][:70]: a.push(e)
    assert a.summary.total_events==70
    assert sum(p.touches for p in a.players.values())==sum(e.player_id is not None for e in a.events)
    assert sum(t.passing.attempts for t in a.teams.values())==a.summary.total_passes
    assert a.possession_team_id in a.teams
    assert a.recent[-1].timestamp-a.recent[0].timestamp <= 600

@pytest.mark.parametrize('seed',[42,99])
def test_full_incremental_equals_batch(seed):
    match=generate_match(seed=seed)
    events=MatchEngine(match).simulate()
    expected=IntelligenceEngine(match,events).analyze()
    incremental=IncrementalIntelligence(match)
    for event in events: incremental.push(event)
    assert comparable(incremental.finish())==comparable(expected)

def test_checkpoint_includes_all_equal_timestamp_events(stream):
    a=IncrementalIntelligence(stream[0])
    first=stream[1][0]
    for i,t in enumerate([0,300,300,301]):
        a.push(first.model_copy(update={'timestamp':t,'minute':int(t//60),'event_id':str(i)}))
    assert a.windows[0].timestamp==300
    assert a.windows[2].event_density==.6  # three events / five minutes

def test_priority_filter(stream):
    p=EventProcessor([stream[0]])
    assert p.should_trigger(SignificanceTier.CRITICAL)
    assert p.should_trigger(SignificanceTier.HIGH)
    assert not p.should_trigger(SignificanceTier.NOTABLE)
    assert not p.should_trigger(SignificanceTier.LOW)
    assert EventProcessor([stream[0]],explain_notable=True).should_trigger(SignificanceTier.NOTABLE)

def test_real_offline_pipeline_agents_and_equivalence(stream,tmp_path):
    path=write_events_jsonl(stream[1],tmp_path/'match.jsonl')
    async def exercise():
        p=EventProcessor([stream[0]])
        await run_pipeline(LocalReplaySource(path,0),p,queue_size=4)
        assert p.metrics.events_processed==len(stream[1])
        assert p.metrics.events_failed==0
        assert p.metrics.agent_workflows_failed==0
        assert p.metrics.agent_workflows_triggered==len(p.results)>0
        assert {'GOAL','PRESSURE_SHIFT'}<={r['moment_type'] for r in p.results}
        for row in p.results:
            result=row['result']
            assert result['runtime_mode']=='mock'
            assert result['workflow_status']=='COMPLETE'
            assert result['state']['explanation_validated']
            assert all(step['status']=='OK' for step in result['state']['trace_steps'])
            assert result['evidence_ids']
        assert comparable(p.final_intelligence(stream[0].match_id))==comparable(IntelligenceEngine(*stream).analyze())
        assert p.metrics.queue_depth==0
        assert p.metrics.queue_high_watermark==4
        assert p.metrics.backpressure_count>0
        assert len({r['live_moment_key'] for r in p.results})==len(p.results)
    run(exercise())

def test_workflow_failure_does_not_stop_match(stream):
    class Broken:
        def __init__(self,*args,**kwargs): pass
        async def process_moment(self,*args): raise RuntimeError('secret-token-never-log')
        async def aclose(self): pass
    async def exercise():
        p=EventProcessor([stream[0]],runner_factory=Broken)
        goal_index=next(i for i,e in enumerate(stream[1]) if e.event_type==EventType.GOAL)
        for i,e in enumerate(stream[1][:goal_index+2]): await p.process(EventEnvelope.wrap(e,i))
        assert p.metrics.agent_workflows_failed==p.metrics.agent_workflows_triggered>0
        assert p.metrics.events_failed==0
        assert p.metrics.events_processed==goal_index+2
    run(exercise())

def test_result_callback_failure_is_not_agent_failure(stream):
    async def callback(result): raise RuntimeError('failed display')
    async def exercise():
        p=EventProcessor([stream[0]],result_callback=callback)
        for i,e in enumerate(stream[1][:200]): await p.process(EventEnvelope.wrap(e,i))
        assert p.metrics.agent_workflows_triggered>0
        assert p.metrics.agent_workflows_failed==0
    run(exercise())

def test_bounded_slow_consumer_no_loss(tmp_path):
    events=[MatchEvent(event_id=str(i),match_id='m',timestamp=i,minute=0,event_type=EventType.MATCH_START) for i in range(12)]
    path=write_events_jsonl(events,tmp_path/'queue.jsonl')
    class Slow:
        metrics=PipelineMetrics()
        accepted=[]
        async def process(self,event):
            await asyncio.sleep(.001)
            self.accepted.append(event.event_id)
            return 'PROCESSED'
    p=Slow()
    run(run_pipeline(LocalReplaySource(path,0),p,queue_size=1))
    assert p.accepted==[str(i) for i in range(12)]
    assert p.metrics.backpressure_count>0
    assert p.metrics.queue_high_watermark==1

def test_pipeline_cancels_source_on_processing_exception(stream,tmp_path):
    path=write_events_jsonl(stream[1],tmp_path/'queue.jsonl')
    source=LocalReplaySource(path,0)
    class Broken:
        metrics=PipelineMetrics()
        async def process(self,event): raise RuntimeError('processor stopped')
    with pytest.raises(ExceptionGroup): run(run_pipeline(source,Broken(),queue_size=1))
    assert not source.running

def test_queue_rejects_unbounded_size(stream,tmp_path):
    with pytest.raises(ValueError):
        run(run_pipeline(LocalReplaySource('unused'),EventProcessor([stream[0]]),queue_size=0))

def test_malformed_replay_row_is_counted(stream,tmp_path):
    path=tmp_path/'malformed.jsonl'
    path.write_text('not-json\n'+stream[1][0].model_dump_json()+'\n')
    p=EventProcessor([stream[0]])
    run(run_pipeline(LocalReplaySource(path,0),p))
    assert p.metrics.events_failed==1
    assert p.metrics.events_processed==1

def test_sdk_serialization_deserialization_without_network(stream):
    original=envelope(stream,1)
    assert deserialize(serialize(original))==original

def test_sdk_deserialization_invalid_schema(stream):
    from azure.eventhub import EventData
    value=envelope(stream).model_dump(mode='json');value['schema_version']='2.0'
    with pytest.raises(ValidationError): deserialize(EventData(json.dumps(value)))

@pytest.mark.parametrize('kwargs',[{}, {'eventhub_name':'hub'}, {'namespace':'https://bad','eventhub_name':'hub'}])
def test_azure_config_clear_errors(kwargs):
    with pytest.raises(ValidationError): AzureConfig(**kwargs)

def config(**kwargs):
    return AzureConfig(namespace='demo.servicebus.windows.net',eventhub_name='matchlens',**kwargs)

def test_connection_string_secret_excluded():
    c=AzureConfig(eventhub_name='hub',connection_string='SUPER_SECRET')
    assert 'SUPER_SECRET' not in repr(c)
    assert 'SUPER_SECRET' not in c.model_dump_json()

def test_retry_backoff_and_safe_logs(caplog):
    from azure.eventhub.exceptions import ConnectError
    attempts=[];waits=[]
    async def operation():
        attempts.append(1)
        if len(attempts)<3: raise ConnectError('SUPER_SECRET')
        return 'ok'
    async def sleep(t): waits.append(t)
    with caplog.at_level('INFO',logger='matchlens.realtime'):
        assert run(retry(operation,config(),sleep=sleep))=='ok'
    assert waits==[.5,1]
    assert 'SUPER_SECRET' not in caplog.text

def test_retry_exhaustion_and_nontransient():
    from azure.eventhub.exceptions import ConnectError
    async def fail(): raise ConnectError('secret')
    async def immediate(t): pass
    with pytest.raises(AzureTransportError,match='exhausted'):
        run(retry(fail,config(max_attempts=2),sleep=immediate))
    async def permanent(): raise RuntimeError('secret')
    with pytest.raises(AzureTransportError,match='authentication'):
        run(retry(permanent,config(),sleep=immediate))

def test_producer_partition_key_batch_split_and_order(stream):
    class Batch:
        def __init__(self,key): self.key=key;self.items=[]
        def __len__(self): return len(self.items)
        def add(self,item):
            if len(self.items)==2: raise ValueError('full')
            self.items.append(item)
    class Client:
        def __init__(self):self.sent=[];self.closed=False
        async def create_batch(self,*,partition_key):return Batch(partition_key)
        async def send_batch(self,batch):self.sent.append(batch)
        async def close(self):self.closed=True
    client=Client()
    values=[envelope(stream,i) for i in range(5)]
    other=EventEnvelope.wrap(stream[1][0].model_copy(update={'match_id':'other_match'}),0)
    async def exercise():
        p=AzureEventHubProducer(config(),client=client)
        await p.start();await p.publish_batch([*values,other]);await p.stop()
    run(exercise())
    assert [b.key for b in client.sent]==[stream[0].match_id]*3+['other_match']
    assert [deserialize(e).sequence_number for b in client.sent[:3] for e in b.items]==list(range(5))
    assert client.closed

def test_consumer_validation_order_and_cursor(stream):
    from azure.eventhub import EventData
    class Context: partition_id='0'
    class Received:
        def __init__(self,data,offset):self.data=data;self.offset=offset
        def body_as_str(self,encoding):return self.data.body_as_str(encoding)
    class Client:
        async def get_partition_ids(self):return ['0']
        async def receive(self,callback,**options):
            assert options['prefetch']==2
            await options['on_partition_initialize'](Context())
            for i in range(3): await callback(Context(),Received(serialize(envelope(stream,i)),str(i)))
            await callback(Context(),Received(EventData('not-json'),'3'))
        async def close(self):pass
    accepted=[]
    async def exercise():
        consumer=AzureEventHubConsumer(config(queue_size=2),client=Client())
        await consumer.start()
        async def handler(value):accepted.append(value)
        await consumer.receive(handler)
        assert consumer.ready.is_set()
        assert consumer.positions=={'0':'3'}
        await consumer.stop()
    run(exercise())
    assert [v.sequence_number for v in accepted[:3]]==[0,1,2]
    assert accepted[3]=={'invalid_azure_envelope':True}

def test_azure_source_bounded_filter_and_same_envelopes(stream):
    class Consumer:
        config=config(queue_size=1)
        async def start(self):pass
        async def stop(self):pass
        async def receive(self,handler,**kwargs):
            await handler(envelope(stream,2))
            await handler(envelope(stream,0))
            await handler(envelope(stream,1))
    ids=[envelope(stream,i).correlation_id for i in range(2)]
    async def exercise():
        source=AzureEventHubSource(Consumer(),correlation_ids=ids,expected_event_count=2)
        await source.start()
        try: values=[v async for v in source.events()]
        finally:await source.stop()
        assert source.queue.maxsize==1
        return values
    assert [v.sequence_number for v in run(exercise())]==[0,1]

def test_no_secrets_logged_even_untrusted_identifiers(stream,caplog):
    async def exercise():
        p=EventProcessor([stream[0]])
        e=stream[1][0].model_copy(update={'match_id':'SUPER_SECRET','event_id':'SUPER_SECRET'})
        await p.process(EventEnvelope.wrap(e,0,correlation_id='SUPER_SECRET'))
        await p.process({'connection_string':'SUPER_SECRET'})
    with caplog.at_level('INFO',logger='matchlens.realtime'):run(exercise())
    assert 'SUPER_SECRET' not in caplog.text
    for record in caplog.records:
        value=json.loads(record.message)
        assert {'trace_id','match_id','event_id','sequence_number'}<=value.keys()

def test_cli_azure_missing_config_fails_without_fallback(monkeypatch,capsys):
    from simulator.live import main
    for name in ['AZURE_EVENT_HUB_NAME','AZURE_EVENT_HUB_FULLY_QUALIFIED_NAMESPACE','AZURE_EVENT_HUB_CONNECTION_STRING']:
        monkeypatch.delenv(name,raising=False)
    with pytest.raises(SystemExit) as e:main(['--match','unused','--mode','azure'])
    assert e.value.code==1
    assert 'Configuration error' in capsys.readouterr().err

def test_missing_schema_version_is_rejected(stream):
    value=envelope(stream).model_dump(mode='json')
    del value['schema_version']
    with pytest.raises(ValidationError):EventEnvelope.model_validate(value)

def test_live_team_projections_match_batch_prefix(stream):
    a=IncrementalIntelligence(stream[0])
    for event in stream[1][:150]:a.push(event)
    batch=IntelligenceEngine(stream[0],stream[1][:150]).analyze()
    assert a.teams[batch.home_team.team_id]==batch.home_team
    assert a.teams[batch.away_team.team_id]==batch.away_team
    assert sorted(a.players.values(),key=lambda p:(p.team_id,p.player_id))==batch.players

def test_schema_contains_typed_payload_alternatives(stream):
    from simulator.realtime.schema import envelope_json_schema
    schema=envelope_json_schema()
    assert 'schema_version' in schema['required']
    assert len(schema['properties']['payload']['oneOf'])==15
    assert schema['$defs']['ShotEvent']['properties']['event_type']['const']=='SHOT'
    assert 'shot_distance_meters' in schema['$defs']['ShotEvent']['required']

def test_paced_replay_10x_measured(tmp_path):
    import time
    events=[MatchEvent(event_id=str(i),match_id='m',timestamp=t,minute=0,
        event_type=EventType.MATCH_START) for i,t in enumerate([0,.5,1])]
    path=write_events_jsonl(events,tmp_path/'paced.jsonl')
    async def exercise():
        source=LocalReplaySource(path,10)
        await source.start()
        started=time.perf_counter()
        values=[v async for v in source.events()]
        elapsed=time.perf_counter()-started
        await source.stop()
        assert elapsed>=.09
        assert [v.simulation_timestamp for v in values]==[0,.5,1]
    run(exercise())

def test_failed_component_closes_only_affected_match(stream,monkeypatch):
    async def exercise():
        other=stream[0].model_copy(update={'match_id':'other'})
        p=EventProcessor([stream[0],other])
        def broken(event):raise ValueError('do not disclose')
        monkeypatch.setattr(p.matches[stream[0].match_id],'push',broken)
        assert await p.process(envelope(stream))=='INTELLIGENCE_FAILURE'
        assert await p.process(envelope(stream,1))=='MATCH_CLOSED'
        e=stream[1][0].model_copy(update={'match_id':'other'})
        assert await p.process(EventEnvelope.wrap(e,0))=='PROCESSED'
    run(exercise())

def test_consumer_partition_error_reaches_bounded_retry(caplog):
    from azure.eventhub.exceptions import ConnectError
    class Client:
        attempts=0
        async def get_partition_ids(self):return ['0']
        async def receive(self,callback,**kwargs):
            self.attempts+=1
            await kwargs['on_error'](None,ConnectError('SUPER_SECRET'))
            await asyncio.Event().wait()
        async def close(self):pass
    client=Client()
    async def exercise():
        consumer=AzureEventHubConsumer(config(max_attempts=2,backoff_seconds=0),client=client)
        await consumer.start()
        async def handler(envelope):pass
        with pytest.raises(AzureTransportError,match='exhausted'):
            await asyncio.wait_for(consumer.receive(handler),1)
        await consumer.stop()
    with caplog.at_level('INFO',logger='matchlens.realtime'):run(exercise())
    assert client.attempts==2
    assert 'SUPER_SECRET' not in caplog.text

@pytest.mark.parametrize('source,expected',[('local','local'),('eventhub','azure')])
def test_cli_environment_mode(monkeypatch,source,expected):
    from simulator.live import resolve_mode
    monkeypatch.setenv('EVENT_SOURCE',source)
    assert resolve_mode()==expected
    assert resolve_mode('local')=='local'

def test_cli_environment_invalid_and_missing_config(monkeypatch,capsys):
    from simulator.live import main, resolve_mode
    monkeypatch.setenv('EVENT_SOURCE','SUPER_SECRET')
    with pytest.raises(ValueError,match='withheld'):resolve_mode()
    monkeypatch.setenv('EVENT_SOURCE','eventhub')
    for key in ('AZURE_EVENT_HUB_NAME','AZURE_EVENT_HUB_CONNECTION_STRING','AZURE_EVENT_HUB_FULLY_QUALIFIED_NAMESPACE'):
        monkeypatch.delenv(key,raising=False)
    with pytest.raises(SystemExit):main(['--match','unused'])
    assert 'Configuration error' in capsys.readouterr().err

@pytest.mark.parametrize('option',[{'batch_size':0},{'flush_seconds':0},{'publish_timeout_seconds':0}])
def test_publish_policy_validation(option):
    with pytest.raises(ValidationError):config(**option)

def test_stream_count_flush_preserves_payload_and_order(stream):
    batches=[]
    class RecordingProducer(AzureEventHubProducer):
        async def publish_batch(self,values):batches.append(list(values))
    values=[envelope(stream,i) for i in range(5)]
    async def events():
        for value in values:yield value
    p=RecordingProducer(config(batch_size=2))
    assert run(p.publish_stream(events()))==5
    assert [len(b) for b in batches]==[2,2,1]
    assert [v for b in batches for v in b]==values

def test_stream_timer_flush_does_not_cancel_pacing(stream):
    batches=[];closed=[]
    class RecordingProducer(AzureEventHubProducer):
        async def publish_batch(self,values):batches.append(list(values))
    async def events():
        try:
            yield envelope(stream,0)
            await asyncio.sleep(.04)
            assert len(batches)==1
            yield envelope(stream,1)
        finally:closed.append(True)
    assert run(RecordingProducer(config(flush_seconds=.01)).publish_stream(events()))==2
    assert [len(b) for b in batches]==[1,1]
    assert closed==[True]

def test_stream_send_failure_closes_pending_read_and_bounds_backpressure(stream):
    reads=[];closed=[]
    class RecordingProducer(AzureEventHubProducer):
        async def publish_batch(self,values):
            assert len(reads)==2
            raise AzureTransportError('sanitized')
    async def events():
        try:
            for i in range(10):
                reads.append(i)
                yield envelope(stream,i)
        finally:closed.append(True)
    with pytest.raises(AzureTransportError):run(RecordingProducer(config(batch_size=2)).publish_stream(events()))
    assert closed==[True] and reads==[0,1]

def test_publish_timeout_retries_bounded_and_cancellation_propagates():
    attempts=[]
    async def hang():
        attempts.append(1)
        await asyncio.Event().wait()
    with pytest.raises(AzureTransportError,match='exhausted'):
        run(retry(hang,config(max_attempts=2,backoff_seconds=0),timeout=.01))
    assert len(attempts)==2
    async def cancelled():raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):run(retry(cancelled,config(),timeout=.01))

def test_stream_cancellation_closes_pending_paced_iterator(stream):
    closed=[];sent=[]
    class RecordingProducer(AzureEventHubProducer):
        async def publish_batch(self,values):sent.extend(values)
    async def events():
        try:
            yield envelope(stream,0)
            await asyncio.Event().wait()
        finally:closed.append(True)
    async def exercise():
        task=asyncio.create_task(RecordingProducer(config(flush_seconds=.01)).publish_stream(events()))
        await asyncio.sleep(.03)
        assert len(sent)==1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    run(exercise())
    assert closed==[True]
