"""Offline contract for opt-in hosted streaming: publisher → Event Hubs → API consumer → Fabric; no network."""
import asyncio
import json
import time
import pytest
from fastapi.testclient import TestClient
import simulator.production.streaming as streaming
import simulator.realtime.azure as azure_module
from simulator.presentation.api import DEFAULT_PATH, create_app
from simulator.production.config import AppConfig
from simulator.production.public_api import create_public_app
from simulator.production.streaming import StreamController, StreamRejected, StreamSettings
from simulator.production.transport import VerificationFailed, in_run, key
from simulator.realtime.azure import AzureConfig
from simulator.realtime.sources import LocalReplaySource
from tests.test_transport_verification import HUB, NAMESPACE, TARGET, FakeFabric, path, topology

EXPECTED = sum(1 for line in DEFAULT_PATH.read_text().splitlines() if line.strip())
BASE_ENV = {'APP_FRONTEND_ORIGINS': 'https://matchlens.example', 'AGENTIC_MODE': 'mock'}
STREAM_ENV = {**BASE_ENV, 'APP_STREAMING': 'true', 'AZURE_EVENT_HUB_FULLY_QUALIFIED_NAMESPACE': NAMESPACE,
              'AZURE_EVENT_HUB_NAME': HUB, 'AZURE_EVENT_HUB_CONSUMER_GROUP': 'matchlens-api',
              'REALTIME_RECEIVE_TIMEOUT_SECONDS': '3', 'APP_STREAM_COOLDOWN_SECONDS': '0'}


class EventHub:
    """In-memory hub: every consumer group receives every published message independently."""

    def __init__(self, transform=lambda envelope: [envelope]):
        self.log, self.groups, self.transform = [], {}, transform

    def publish(self, envelope):
        self.log.append(envelope)
        for queues in self.groups.values():
            for queue in queues:
                for delivered in self.transform(envelope):
                    queue.put_nowait(delivered)


def fakes(hub, *, ready=True, drop=frozenset(), duplicate=frozenset(), fail_start=False):
    class Consumer:
        def __init__(self, config):
            self.config, self.ready = config, asyncio.Event()

        async def start(self):
            pass

        async def receive(self, handler, starting_position=None):
            queue = asyncio.Queue()
            hub.groups.setdefault(self.config.consumer_group, []).append(queue)
            if ready:
                self.ready.set()
            while True:
                await handler(await queue.get())

        async def stop(self):
            pass

    class Producer:
        def __init__(self, config):
            self.config = config

        async def start(self):
            if fail_start:
                raise RuntimeError('https://example.servicebus.windows.net/?sig=SECRET')

        async def publish_stream(self, envelopes):
            count = 0
            async for envelope in envelopes:
                if envelope.sequence_number not in drop:
                    hub.publish(envelope)
                    if envelope.sequence_number in duplicate:  # resend after an ambiguous acknowledgement
                        hub.publish(envelope)
                count += 1
            return count

        async def stop(self):
            pass
    return Consumer, Producer


class StreamFabric(FakeFabric):
    """Applies the same run-prefix filter the real KQL query uses."""

    def __init__(self, hub, rows=lambda messages: messages, fail=False, **kwargs):
        super().__init__(hub, rows=rows, **kwargs)
        self.eventhub, self.fail = hub, fail

    async def query(self, endpoint, database, csl):
        if self.fail:
            raise VerificationFailed('fabric_destination', 'KQL query returned HTTP 503')
        prefix = csl.split('"')[1]
        stored = [e for e in self.rows(list(self.eventhub.log)) if e.correlation_id.startswith(prefix)]
        return {'Tables': [{'Columns': [{'ColumnName': 'envelope'}], 'Rows': [[e.model_dump(mode='json')] for e in stored]}]}


@pytest.fixture(autouse=True)
def instant_replay(monkeypatch):
    # Production pacing is bounded (1–100×); tests replay instantly.
    monkeypatch.setattr(streaming, 'LocalReplaySource', lambda path, speed: LocalReplaySource(path, 0))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    async def sleep(self, seconds):
        self.t += seconds


def settings(fabric=None, **overrides):
    values = dict(speed=20, run_timeout=120, cooldown=0, runs_per_hour=10, fabric_timeout=30)
    values.update(overrides)
    return StreamSettings(AzureConfig(namespace=NAMESPACE, eventhub_name=HUB, consumer_group='matchlens-api',
                                      receive_timeout_seconds=3), fabric, **values)


def stream_service():
    config = AppConfig.from_env({**STREAM_ENV, 'APP_ENV': 'demo', 'EVENT_SOURCE': 'eventhub', 'APP_DEMO_CONTROLS': 'false',
                                 'APP_DIAGNOSTICS': 'false', 'APP_AUTOSTART': 'false'})
    return create_app(DEFAULT_PATH, config).state.service


def run_controller(monkeypatch, hub, *, fabric=None, fabric_reader=None, starts=1, **fake_options):
    consumer, producer = fakes(hub, **fake_options)
    monkeypatch.setattr(azure_module, 'AzureEventHubConsumer', consumer)
    clock = Clock()

    async def scenario():
        service = stream_service()
        controller = StreamController(settings(fabric), service, producer_factory=producer,
                                      fabric_factory=(lambda: fabric_reader) if fabric else None, clock=clock, sleep=clock.sleep)
        for _ in range(starts):
            await controller.start()
            await asyncio.gather(controller.task, return_exceptions=True)
        status = controller.status()
        await service.close()
        return controller, status
    return asyncio.run(scenario())


# ---- configuration and gateway source selection ----

def test_streaming_is_off_by_default_and_recorded_sessions_are_unchanged():
    with TestClient(create_public_app(BASE_ENV)) as client:
        assert client.get('/api/stream/status').json() == {'enabled': False, 'source_mode': 'local'}
        assert client.post('/api/stream/runs', headers={'Origin': BASE_ENV['APP_FRONTEND_ORIGINS']}).status_code == 404
        assert client.get('/stream/api/matches').status_code == 404
        health = client.get('/health').json()
        assert health['streaming'] == {'enabled': False} and health['event_hubs']['status'] == 'UNVERIFIED'
        session = client.post('/api/sessions').json()
        match = client.get(session['api_path'] + '/api/matches').json()[0]
        assert match['event_source'] == 'local' and match['demo_controls'] is True


@pytest.mark.parametrize('change,reason', [
    ({'AZURE_EVENT_HUB_CONSUMER_GROUP': ''}, 'its own'),
    ({'AZURE_EVENT_HUB_CONSUMER_GROUP': '$Default'}, 'its own'),
    ({'AZURE_EVENT_HUB_CONSUMER_GROUP': 'matchlens-verification'}, 'its own'),
    ({'AZURE_EVENT_HUB_CONNECTION_STRING': 'Endpoint=sb://x/;SharedAccessKeyName=k;SharedAccessKey=SECRET'}, 'managed identity only'),
    ({'APP_STREAM_SPEED': '0'}, 'out of range'),
    ({'APP_STREAM_RUNS_PER_HOUR': '1000'}, 'out of range'),
    ({'APP_STREAMING': 'yes'}, 'true or false'),
    ({'APP_STREAM_FABRIC_VERIFY': 'true'}, 'Fabric verification configuration invalid'),
])
def test_invalid_streaming_configuration_fails_closed(change, reason):
    with pytest.raises(ValueError) as error:
        create_public_app({**STREAM_ENV, **change})
    assert reason in str(error.value) and 'SECRET' not in str(error.value)


def test_fabric_verification_cannot_share_the_api_consumer_group():
    env = {**STREAM_ENV, 'APP_STREAM_FABRIC_VERIFY': 'true', 'FABRIC_WORKSPACE_ID': TARGET.workspace_id,
           'FABRIC_EVENTSTREAM_ID': TARGET.eventstream_id, 'FABRIC_EVENTHOUSE_ID': TARGET.eventhouse_id,
           'FABRIC_KQL_ENDPOINT': TARGET.kql_endpoint, 'FABRIC_KQL_DATABASE': TARGET.kql_database,
           'FABRIC_SOURCE_CONSUMER_GROUP': 'matchlens-api'}
    with pytest.raises(ValueError, match='must differ'):
        StreamSettings.from_env(env)
    assert StreamSettings.from_env({**env, 'FABRIC_SOURCE_CONSUMER_GROUP': '$Default'}).fabric == TARGET


def test_gateway_selects_event_hubs_only_for_the_opt_in_stream(monkeypatch):
    hub = EventHub()
    consumer, producer = fakes(hub)
    monkeypatch.setattr(azure_module, 'AzureEventHubConsumer', consumer)
    app = create_public_app(STREAM_ENV, producer_factory=producer)
    with TestClient(app) as client:
        origin = {'Origin': STREAM_ENV['APP_FRONTEND_ORIGINS']}
        stream_match = client.get('/stream/api/matches').json()[0]
        assert stream_match['event_source'] == 'eventhub' and stream_match['demo_controls'] is False
        assert stream_match['runtime_mode'] == 'mock'
        assert app.state.stream.service.config.azure.consumer_group == 'matchlens-api'
        session = client.post('/api/sessions').json()
        assert client.get(session['api_path'] + '/api/matches').json()[0]['event_source'] == 'local'
        assert client.post('/stream/api/matches/x/demo', headers=origin, json={'action': 'start'}).status_code == 405
        assert client.get('/stream/api/data-plane').json()['namespace'] is None


# ---- hosted end-to-end run through the gateway ----

def wait_for_run(client, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        status = client.get('/api/stream/status').json()
        if status['run'] and status['run']['state'] in ('complete', 'failed'):
            return status
        time.sleep(0.2)
    raise AssertionError('stream run did not finish')


def test_public_run_streams_the_whole_match_into_the_ui_view(monkeypatch):
    hub = EventHub()
    consumer, producer = fakes(hub)
    monkeypatch.setattr(azure_module, 'AzureEventHubConsumer', consumer)
    with TestClient(create_public_app(STREAM_ENV, producer_factory=producer)) as client:
        origin = {'Origin': STREAM_ENV['APP_FRONTEND_ORIGINS']}
        assert client.post('/api/stream/runs', headers={'Origin': 'https://evil.example'}).status_code == 403
        started = client.post('/api/stream/runs', headers=origin)
        assert started.status_code == 202
        run_id = started.json()['run']['run_id']
        status = wait_for_run(client)
        run = status['run']
        assert run['run_id'] == run_id and run['state'] == 'complete' and run['failure'] is None
        assert (run['expected_events'], run['published'], run['api_received'], run['api_processed']) == (EXPECTED,) * 4
        assert run['processing_errors'] == 0 and run['consumer_ready'] is False and run['last_event_at']
        assert run['fabric'] == 'NOT CONFIGURED' and run['reconciled'] is False and run['stages']['fabric'] is None
        assert [e.correlation_id for e in hub.log] == [f'{run_id}:{i}' for i in range(EXPECTED)]
        assert len({e.event_id for e in hub.log}) == EXPECTED
        snapshot = client.get('/stream/api/matches/' + hub.log[0].match_id).json()
        assert snapshot['state']['total_events'] == EXPECTED and snapshot['state']['status'] == 'complete'
        assert snapshot['match']['event_source'] == 'eventhub'
        text = json.dumps(status)
        assert NAMESPACE not in text and 'servicebus' not in text and 'SharedAccess' not in text
        assert client.get('/health').json()['streaming'] == {'enabled': True, 'active': False, 'run_state': 'complete',
                                                            'fabric': 'NOT CONFIGURED'}


# ---- controller: guards, reconciliation and failures ----

class IdleService:
    """Just enough MatchService surface for the start guards; no events are consumed."""
    path = DEFAULT_PATH
    stream_received, stream_processed, stream_errors, received_at = [], [], 0, None

    def stream_connected(self):
        return False


def test_a_second_run_is_refused_while_one_is_active_and_budgets_apply():
    async def scenario():
        controller = StreamController(settings(cooldown=60, runs_per_hour=2), IdleService(),
                                      producer_factory=None, clock=lambda: 0.0)
        never = asyncio.Event()

        async def hang(run):
            await never.wait()
        controller._execute = hang
        await controller.start()
        with pytest.raises(StreamRejected) as active:
            await controller.start()
        controller.task.cancel()
        await asyncio.gather(controller.task, return_exceptions=True)
        with pytest.raises(StreamRejected) as cooldown:
            await controller.start()
        return active.value.status, cooldown.value.status
    assert asyncio.run(scenario()) == (409, 429)


def test_hourly_budget_limits_public_publishing():
    async def scenario():
        clock = Clock()
        controller = StreamController(settings(cooldown=0, runs_per_hour=2), IdleService(),
                                      producer_factory=None, clock=clock)

        async def done(run):
            run.state = 'complete'
        controller._execute = done
        for _ in range(2):
            await controller.start()
            await controller.task
        with pytest.raises(StreamRejected) as budget:
            await controller.start()
        clock.t = 3601
        await controller.start()
        return budget.value.status
    assert asyncio.run(scenario()) == 429


def test_verified_run_reconciles_publisher_event_hubs_api_and_fabric(monkeypatch):
    hub = EventHub()
    fabric = StreamFabric(hub)
    controller, status = run_controller(monkeypatch, hub, fabric=TARGET, fabric_reader=fabric, duplicate={5, 6})
    run = status['run']
    assert run['state'] == 'complete' and run['fabric'] == 'VERIFIED' and run['reconciled'] is True
    stages = run['stages']
    assert stages['published'] == {'total': EXPECTED, 'unique': EXPECTED, 'missing': 0, 'missing_sample': [],
                                   'duplicates': 0, 'unexpected': 0, 'altered': 0}
    assert stages['eventhub_readback']['duplicates'] == 2 and stages['api_processed']['duplicates'] == 0
    assert stages['fabric']['duplicates'] == 2 and stages['fabric']['unique'] == EXPECTED
    assert controller.run.topology['source_type'] == 'AzureEventHubExtended'


def test_topology_mismatch_prevents_any_publication(monkeypatch):
    hub = EventHub()
    fabric = StreamFabric(hub, path=path(entity='matchlens-events-old'))
    _, status = run_controller(monkeypatch, hub, fabric=TARGET, fabric_reader=fabric)
    assert status['run']['failure']['stage'] == 'topology' and status['run']['fabric'] == 'FAILED'
    assert hub.log == [] and status['run']['published'] == 0


def test_consumer_that_never_connects_publishes_nothing(monkeypatch):
    hub = EventHub()
    _, status = run_controller(monkeypatch, hub, ready=False)
    assert status['run']['failure'] == {'stage': 'connecting', 'reason': 'API consumer did not connect; nothing was published'}
    assert hub.log == []


def test_event_hubs_unavailable_fails_safely_without_leaking_errors(monkeypatch):
    hub = EventHub()
    _, status = run_controller(monkeypatch, hub, fail_start=True)
    assert status['run']['failure'] == {'stage': 'publishing', 'reason': 'unexpected RuntimeError'}
    assert 'SECRET' not in json.dumps(status)


def test_events_missing_from_event_hubs_fail_the_run(monkeypatch):
    hub = EventHub()
    _, status = run_controller(monkeypatch, hub, drop={100})
    run = status['run']
    assert run['state'] == 'failed' and run['failure']['stage'] == 'consuming'
    assert run['api_processed'] < EXPECTED and run['reconciled'] is False


def test_event_altered_in_transit_is_detected(monkeypatch):
    def alter(envelope):
        return [envelope.model_copy(update={'source': 'tampered'}) if envelope.sequence_number == 42 else envelope]
    hub = EventHub(transform=alter)
    _, status = run_controller(monkeypatch, hub)
    run = status['run']
    assert run['failure'] == {'stage': 'reconciling', 'reason': 'published, Event Hubs and API event sets differ'}
    assert run['stages']['eventhub_readback']['altered'] == 1


@pytest.mark.parametrize('rows,reason', [
    (lambda messages: messages[:-10], 'missing 10'),
    (lambda messages: [*messages[:-1], messages[-1].model_copy(update={'source': 'tampered'})], 'stored envelope differs'),
])
def test_partial_or_altered_fabric_ingestion_is_never_verified(monkeypatch, rows, reason):
    hub = EventHub()
    _, status = run_controller(monkeypatch, hub, fabric=TARGET, fabric_reader=StreamFabric(hub, rows=rows))
    run = status['run']
    assert run['state'] == 'failed' and run['fabric'] == 'FAILED' and reason in run['failure']['reason']
    assert run['failure']['stage'] == 'fabric_destination' and run['reconciled'] is False


def test_fabric_unavailable_is_reported_not_verified(monkeypatch):
    hub = EventHub()
    _, status = run_controller(monkeypatch, hub, fabric=TARGET, fabric_reader=StreamFabric(hub, fail=True))
    assert status['run']['fabric'] == 'FAILED' and status['run']['failure']['reason'] == 'KQL query returned HTTP 503'


def test_other_runs_in_fabric_are_excluded_by_the_run_prefix(monkeypatch):
    hub = EventHub()
    fabric = StreamFabric(hub, rows=lambda messages: [*messages, *[e.model_copy(update={'correlation_id': f"{'b' * 32}:{e.sequence_number}"})
                                                              for e in messages[:50]]])
    _, status = run_controller(monkeypatch, hub, fabric=TARGET, fabric_reader=fabric)
    assert status['run']['fabric'] == 'VERIFIED' and status['run']['stages']['fabric']['total'] == EXPECTED


def test_run_prefix_matching_is_exact():
    run = 'a' * 32
    assert in_run(f'{run}:0', run, True) and in_run(f'{run}:1727', run, True)
    assert not in_run(f'{run}:x', run, True) and not in_run(f'{run}x:1', run, True) and not in_run(run, run, True)
    assert not in_run(f"{'b' * 32}:1", run, True) and in_run(run, run, False)
