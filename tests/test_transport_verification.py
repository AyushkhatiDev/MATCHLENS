"""Offline contract for the bounded Event Hubs → Fabric transport verification; no network."""
import asyncio
import base64
import json
import re
import uuid
import httpx
import pytest
from simulator.production.transport import (FabricReader, FabricTarget, VerificationFailed, connection_matches,
                                            verify_transport)
from simulator.realtime.azure import AzureConfig

WS, ES, EH, KQLDB, CONN = ('aaaaaaaa-0000-4000-8000-000000000001', 'aaaaaaaa-0000-4000-8000-000000000002',
                           'aaaaaaaa-0000-4000-8000-000000000003', 'aaaaaaaa-0000-4000-8000-000000000004',
                           '11111111-2222-3333-4444-555555555555')
NAMESPACE, HUB = 'matchlens-example.servicebus.windows.net', 'matchlens-events'
QUERY_URI = 'https://example.kusto.fabric.microsoft.com'
AZURE = AzureConfig(namespace=NAMESPACE, eventhub_name=HUB, consumer_group='matchlens-verification')
TARGET = FabricTarget(WS, ES, EH, QUERY_URI, 'MatchLens-Eventhouse', '$Default')


def path(endpoint=NAMESPACE, entity=HUB, group='$Default'):
    # Shape of the real EventHub connection path: a JSON object, not a delimited string.
    return json.dumps({'endpoint': endpoint, 'entityPath': entity, **({'consumerGroup': group} if group else {})})


def topology(group='$Default', kind='AzureEventHubExtended', item=KQLDB, workspace=WS, routed=True, table='MatchEvents',
             connection=CONN):
    """Structure of the real MatchLens-Eventstream definition (values substituted)."""
    source = {'consumerGroupName': group, 'startPosition': 'Latest', **({'dataConnectionId': connection} if connection else {})}
    destination = {'workspaceId': workspace, 'itemId': item, 'dataIngestionMode': 'DirectIngestion',
                   'mappingRuleName': 'MatchEvents_mapping', 'connectionName': 'MatchLens-Eventhouse-connection',
                   **({'tableName': table} if table else {})}
    return {'compatibilityLevel': '1.0',
            'sources': [{'name': 'azure-event-hub', 'type': kind, 'properties': source}],
            'streams': [{'name': 'MatchLens-Eventstream-stream', 'type': 'DefaultStream', 'inputNodes': [{'name': 'azure-event-hub'}]}],
            'operators': [],
            'destinations': [{'name': 'MatchLens-Eventhouse', 'type': 'Eventhouse', 'properties': destination,
                              'inputNodes': [{'name': 'MatchLens-Eventstream-stream' if routed else 'unrelated'}]}]}


class Hub:
    def __init__(self):
        self.messages = []


def toguid(value):
    """Kusto toguid(): 32-hex or hyphenated GUID text converts; anything else (e.g. `{run_id}:{seq}`) is null."""
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        return None


EXACT_QUERY = re.compile(r'^MatchEvents \| where correlation_id == "([^"]+)" \| project envelope$')
PREFIX_QUERY = re.compile(r'^MatchEvents \| where tostring\(envelope\.correlation_id\) startswith "([^"]+)" \| project envelope$')
TYPED_PREFIX_QUERY = re.compile(r'^MatchEvents \| where correlation_id startswith "([^"]+)" \| project envelope$')


def kusto_select(envelopes, csl):
    """Evaluate the verifier's KQL against rows shaped like the live table (mapping MatchEvents_mapping):
    a guid-typed correlation_id column next to the full original envelope. Unknown query shapes fail loudly."""
    rows = [(toguid(e.correlation_id), e) for e in envelopes]
    if match := EXACT_QUERY.match(csl):
        wanted = toguid(match[1])
        kept = [e for typed, e in rows if wanted is not None and typed == wanted]
    elif match := PREFIX_QUERY.match(csl):
        kept = [e for _, e in rows if e.correlation_id.startswith(match[1])]
    elif match := TYPED_PREFIX_QUERY.match(csl):
        kept = [e for typed, e in rows if (typed or '').startswith(match[1])]
    else:
        raise AssertionError(f'unexpected KQL shape: {csl}')
    return {'Tables': [{'Columns': [{'ColumnName': 'envelope'}], 'Rows': [[e.model_dump(mode='json')] for e in kept]}]}


class FakeFabric:
    def __init__(self, hub, definition=None, path=path(), rows=lambda messages: messages, connection_type='EventHub',
                 database=None):
        self.hub, self.topology, self.path, self.rows = hub, definition or topology(), path, rows
        self.connection_type = connection_type
        self.database = database or {'displayName': 'MatchLens-Eventhouse',
                                     'properties': {'parentEventhouseItemId': EH, 'queryServiceUri': QUERY_URI}}
        self.queries = 0

    async def item(self, workspace, item):
        return {'type': {ES: 'Eventstream', EH: 'Eventhouse', KQLDB: 'KQLDatabase'}.get(item, 'Lakehouse')}

    async def definition(self, workspace, item):
        return self.topology

    async def kql_database(self, workspace, item):
        if item != KQLDB:
            raise VerificationFailed('topology', 'KQL database read returned HTTP 404')
        return self.database

    async def connection(self, connection_id):
        return {'connectionDetails': {'type': self.connection_type, 'path': self.path},
                'credentialDetails': {'credentialType': 'Basic'}}

    async def query(self, endpoint, database, csl):
        self.queries += 1
        self.last_query = csl
        return kusto_select(self.rows(list(self.hub.messages)), csl)


class FakeProducer:
    def __init__(self, hub, error=None):
        self.hub, self.error = hub, error

    async def start(self):
        pass

    async def publish_batch(self, envelopes):
        if self.error:
            raise self.error
        self.hub.messages.extend(envelopes)

    async def stop(self):
        pass


class FakeReadback:
    def __init__(self, hub, transform=lambda messages: messages, ready=True, hang=False):
        self.hub, self.transform, self.is_ready, self.hang = hub, transform, ready, hang

    async def start(self):
        pass

    async def ready(self):
        if not self.is_ready:
            await asyncio.Event().wait()

    async def events(self):
        while not self.hub.messages:
            await asyncio.sleep(0)
        for envelope in self.transform(list(self.hub.messages)):
            yield envelope
        if self.hang:
            await asyncio.Event().wait()

    async def stop(self):
        pass


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    async def sleep(self, seconds):
        self.t += seconds


def run(*, azure=AZURE, target=TARGET, fabric=None, producer=None, readback=None, hub=None, **options):
    hub = hub or Hub()
    clock = Clock()
    record = asyncio.run(verify_transport(azure, target, fabric=fabric or FakeFabric(hub), producer=producer or FakeProducer(hub),
                                          readback=readback or FakeReadback(hub), run_id='a' * 32, sleep=clock.sleep,
                                          clock=clock, readback_timeout=options.pop('readback_timeout', 1),
                                          kql_timeout=options.pop('kql_timeout', 20), kql_interval=5, **options))
    return record, hub


def test_one_run_id_is_verified_across_publish_readback_and_fabric():
    record, hub = run()
    assert record['status'] == 'verified' and record['failure'] is None
    assert {e.correlation_id for e in hub.messages} == {record['run_id']}
    assert record['source']['unique_events'] == 374 and record['source']['published_messages'] == 375
    eventhubs = record['eventhubs']
    assert (eventhubs['published'], eventhubs['readback_unique'], eventhubs['readback_total']) == (375, 374, 375)
    assert eventhubs['integrity_verified'] and eventhubs['auth'] == 'entra_id'
    fabric = record['fabric']
    assert fabric['topology_verified'] and fabric['integrity_verified'] and fabric['rows_unique'] == 374 and fabric['rows_total'] == 375
    assert fabric['source_type'] == 'AzureEventHubExtended' and fabric['source_consumer_group'] == '$Default'
    assert (fabric['connection_endpoint'], fabric['connection_entity']) == (NAMESPACE, HUB)
    assert fabric['kql_database_item_id'] == KQLDB and fabric['destination_table'] == 'MatchEvents'
    assert fabric['connection_credential_type'] == 'Basic' and fabric['source_start_position'] == 'Latest'
    assert record['identifiers_consistent'] and record['agents_run'] is False and record['foundry_called'] is False
    assert record['started_at'] <= record['finished_at']


def test_legacy_azure_event_hub_source_type_is_still_accepted():
    hub = Hub()
    record, _ = run(hub=hub, fabric=FakeFabric(hub, definition=topology(kind='AzureEventHub')))
    assert record['status'] == 'verified' and record['fabric']['source_type'] == 'AzureEventHub'


OTHER = '00000000-0000-0000-0000-000000000000'


@pytest.mark.parametrize('fabric_kwargs,reason', [
    # Source and connection
    ({'path': path(endpoint='matchlens-other.servicebus.windows.net')}, 'no Event Hubs source reads'),
    ({'path': path(entity='matchlens-events-old')}, 'no Event Hubs source reads'),
    ({'definition': topology(group='matchlens-fabric')}, 'no Event Hubs source reads'),
    ({'definition': topology(group='matchlens-verification')}, 'no Event Hubs source reads'),
    ({'path': path(group='matchlens-verification')}, 'connection consumer group differs'),
    ({'definition': topology(kind='CustomEndpoint')}, 'no Azure Event Hubs source'),
    ({'definition': topology(kind='AzureEventHubFake')}, 'no Azure Event Hubs source'),
    ({'definition': topology(connection=None)}, 'no dataConnectionId'),
    ({'connection_type': 'AzureServiceBus'}, 'not an EventHub connection'),
    ({'path': f'{NAMESPACE};{HUB}'}, 'expected endpoint/entityPath JSON object'),
    ({'path': json.dumps({'endpoint': NAMESPACE})}, 'expected endpoint/entityPath JSON object'),
    ({'path': None}, 'expected endpoint/entityPath JSON object'),
    # Destination and table
    ({'definition': topology(item=OTHER)}, 'KQL database read returned HTTP 404'),
    ({'definition': topology(workspace=OTHER)}, 'no routed Eventhouse destination'),
    ({'database': {'displayName': 'MatchLens-Eventhouse', 'properties': {'parentEventhouseItemId': OTHER, 'queryServiceUri': QUERY_URI}}},
     'no routed Eventhouse destination'),
    ({'database': {'displayName': 'OtherDatabase', 'properties': {'parentEventhouseItemId': EH, 'queryServiceUri': QUERY_URI}}},
     'no routed Eventhouse destination'),
    ({'database': {'displayName': 'MatchLens-Eventhouse', 'properties': {'parentEventhouseItemId': EH,
                                                                          'queryServiceUri': 'https://other.kusto.fabric.microsoft.com'}}},
     'no routed Eventhouse destination'),
    ({'definition': topology(routed=False)}, 'does not route'),
    ({'definition': topology(table='OtherTable')}, 'does not write to table MatchEvents'),
    ({'definition': topology(table=None)}, 'does not write to table MatchEvents'),
    # Malformed definitions
    ({'definition': {'sources': None, 'destinations': None}}, 'no Azure Event Hubs source'),
    ({'definition': {**topology(), 'destinations': []}}, 'no Eventhouse destination'),
])
def test_topology_mismatch_fails_before_anything_is_published(fabric_kwargs, reason):
    hub = Hub()
    record, _ = run(hub=hub, fabric=FakeFabric(hub, **fabric_kwargs))
    assert record['status'] == 'failed' and record['failure']['stage'] == 'topology' and reason in record['failure']['reason']
    assert hub.messages == [] and record['eventhubs']['published'] == 0


@pytest.mark.parametrize('azure,reason', [
    (AzureConfig(namespace=NAMESPACE, eventhub_name=HUB, consumer_group='$Default'), 'must differ'),
    (AzureConfig(namespace=NAMESPACE, eventhub_name=HUB, consumer_group='matchlens-verification',
                 connection_string='Endpoint=sb://x/;SharedAccessKeyName=k;SharedAccessKey=SECRETVALUE'), 'Entra ID only'),
])
def test_unsafe_configuration_fails_without_network(azure, reason):
    record, hub = run(azure=azure)
    assert record['failure']['stage'] == 'configuration' and reason in record['failure']['reason']
    assert hub.messages == [] and 'SECRETVALUE' not in json.dumps(record)


def foreign(messages):
    return [e.model_copy(update={'correlation_id': 'b' * 32}) for e in messages]


def test_readback_ignores_other_runs_and_fails_when_this_run_is_missing():
    hub = Hub()
    record, _ = run(hub=hub, readback=FakeReadback(hub, transform=lambda messages: [*foreign(messages[:5]), *messages]))
    assert record['status'] == 'verified' and record['eventhubs']['readback_total'] == 375
    hub = Hub()
    record, _ = run(hub=hub, readback=FakeReadback(hub, transform=foreign))
    assert record['failure']['stage'] == 'eventhub_readback' and 'stream ended' in record['failure']['reason']
    assert record['eventhubs']['published'] == 375 and record['fabric']['rows_total'] == 0


def test_missing_readback_times_out_closed():
    hub = Hub()
    record, _ = run(hub=hub, readback=FakeReadback(hub, transform=lambda messages: [], hang=True), readback_timeout=0.05)
    assert record['failure'] == {'stage': 'eventhub_readback', 'reason': 'timed out before every published event was read back'}


def test_readback_consumer_that_never_initializes_publishes_nothing():
    hub = Hub()
    record, _ = run(hub=hub, readback=FakeReadback(hub, ready=False), readback_timeout=0.05)
    assert record['failure']['stage'] == 'eventhub_readback' and 'nothing was published' in record['failure']['reason']
    assert hub.messages == []


def test_altered_envelope_in_readback_is_detected():
    hub = Hub()
    altered = lambda messages: [messages[0].model_copy(update={'simulation_timestamp': 9999.0}), *messages[1:]]
    record, _ = run(hub=hub, readback=FakeReadback(hub, transform=altered))
    assert record['failure'] == {'stage': 'eventhub_readback', 'reason': 'envelope differs from the published envelope'}


def test_missing_fabric_rows_time_out_closed():
    hub = Hub()
    record, _ = run(hub=hub, fabric=FakeFabric(hub, rows=lambda messages: messages[:100]))
    assert record['failure']['stage'] == 'fabric_destination' and 'timed out with 99 of 374' in record['failure']['reason']
    assert record['eventhubs']['integrity_verified'] and not record['fabric']['integrity_verified']


def test_fabric_rows_without_the_deliberate_duplicate_are_not_accepted():
    hub = Hub()
    record, _ = run(hub=hub, fabric=FakeFabric(hub, rows=lambda messages: messages[1:]))
    assert record['failure']['stage'] == 'fabric_destination' and 'timed out with 374 of 374' in record['failure']['reason']


def test_altered_fabric_row_is_detected():
    hub = Hub()
    altered = lambda messages: [*messages[:-1], messages[-1].model_copy(update={'source': 'tampered'})]
    record, _ = run(hub=hub, fabric=FakeFabric(hub, rows=altered))
    assert record['failure'] == {'stage': 'fabric_destination', 'reason': 'stored envelope differs from the published envelope'}


def test_record_is_sanitized_even_when_an_sdk_error_carries_secrets():
    hub = Hub()
    record, _ = run(hub=hub, producer=FakeProducer(hub, error=RuntimeError('https://x.servicebus.windows.net/?sig=TOPSECRET')))
    text = json.dumps(record)
    assert record['failure'] == {'stage': 'publish', 'reason': 'unexpected RuntimeError'}
    assert 'TOPSECRET' not in text and 'sig=' not in text
    verified, published = run()
    text = json.dumps(verified)
    for forbidden in ('payload', 'Bearer', 'SharedAccessKey', 'connection_string', 'token'):
        assert forbidden not in text
    assert not any(e.event_id in text for e in published.messages)


@pytest.mark.parametrize('env,reason', [
    ({}, 'missing'),
    ({'FABRIC_WORKSPACE_ID': 'nope'}, 'must be UUIDs'),
    ({'FABRIC_KQL_ENDPOINT': 'https://example.kusto.windows.net'}, 'kusto.fabric.microsoft.com'),
    ({'FABRIC_KQL_ENDPOINT': 'https://example.kusto.fabric.microsoft.com/db?x=1'}, 'kusto.fabric.microsoft.com'),
])
def test_fabric_target_configuration_fails_closed(env, reason):
    base = {'FABRIC_WORKSPACE_ID': WS, 'FABRIC_EVENTSTREAM_ID': ES, 'FABRIC_EVENTHOUSE_ID': EH,
            'FABRIC_KQL_ENDPOINT': 'https://example.kusto.fabric.microsoft.com', 'FABRIC_KQL_DATABASE': 'db',
            'FABRIC_SOURCE_CONSUMER_GROUP': '$Default'}
    with pytest.raises(VerificationFailed) as error:
        FabricTarget.from_env({**base, **env} if env else {})
    assert reason in error.value.reason


def test_connection_path_matching_is_exact():
    details = lambda value: {'type': 'EventHub', 'path': value}
    assert connection_matches(details(path()), NAMESPACE, HUB)
    assert connection_matches(details(path(endpoint=f'sb://{NAMESPACE.upper()}/')), NAMESPACE, HUB)
    assert not connection_matches(details(path(entity=HUB + '-old')), NAMESPACE, HUB)
    assert not connection_matches(details(path(endpoint='x' + NAMESPACE)), NAMESPACE, HUB)
    with pytest.raises(VerificationFailed):
        connection_matches(details(f'{NAMESPACE};{HUB}'), NAMESPACE, HUB)


class Credential:
    async def get_token(self, scope):
        return type('Token', (), {'token': 'FAKE-TOKEN'})()


def test_fabric_reader_reports_status_codes_without_response_bodies_and_decodes_definitions():
    definition = topology()

    def handler(request):
        if request.url.path.endswith('/getDefinition'):
            payload = base64.b64encode(json.dumps(definition).encode()).decode()
            return httpx.Response(200, json={'definition': {'parts': [{'path': 'eventstream.json', 'payload': payload}]}})
        return httpx.Response(403, json={'message': 'denied', 'leak': 'BODYSECRET'})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reader = FabricReader(Credential(), client)
            assert await reader.definition(WS, ES) == definition
            with pytest.raises(VerificationFailed) as error:
                await reader.connection(CONN)
            return error.value
    failure = asyncio.run(scenario())
    assert failure.reason == 'Fabric connection read returned HTTP 403' and 'BODYSECRET' not in str(failure)
