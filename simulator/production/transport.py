"""Bounded, correlated Event Hubs → Fabric Eventstream → Eventhouse transport verification.

One fresh run ID travels through every stage: publish to Event Hubs, read back on a dedicated
consumer group, then query the Eventhouse table. The Eventstream topology (source namespace,
hub, consumer group and its route to the Eventhouse) is verified before anything is published.
Only Event Hubs is written; Fabric is only read. No agent workflow runs and Microsoft Foundry
is never called. The record holds identifiers, counts and outcomes only: never tokens, keys,
connection strings, response bodies or event payloads.
"""
import asyncio
import base64
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from simulator.realtime.azure import AzureConfig, AzureEventHubConsumer, AzureEventHubProducer, AzureEventHubSource
from simulator.realtime.models import EventEnvelope
from .cloud import fabric_envelopes, seed99

# Eventstream source types documented to reference an Azure Event Hubs data connection through
# dataConnectionId plus consumerGroupName (the Extended type replaces inputSerialization with
# startPosition). Anything else fails closed and reports the observed types.
SOURCE_TYPES = frozenset({'AzureEventHub', 'AzureEventHubExtended'})
TABLE = 'MatchEvents'
FABRIC_API = 'https://api.fabric.microsoft.com/v1'
SOURCE_LABEL = 'transport_verification'
SCOPE = ('One bounded transport run through Event Hubs, Eventstream and Eventhouse. Not continuous monitoring; '
         'the public MatchLens demo replays a recorded local file and does not use this path.')


class VerificationFailed(Exception):
    def __init__(self, stage, reason):
        super().__init__(f'{stage}: {reason}')
        self.stage = stage
        self.reason = reason


@dataclass(frozen=True)
class FabricTarget:
    workspace_id: str
    eventstream_id: str
    eventhouse_id: str
    kql_endpoint: str
    kql_database: str
    source_consumer_group: str
    table: str = TABLE

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        names = ('FABRIC_WORKSPACE_ID', 'FABRIC_EVENTSTREAM_ID', 'FABRIC_EVENTHOUSE_ID', 'FABRIC_KQL_ENDPOINT',
                 'FABRIC_KQL_DATABASE', 'FABRIC_SOURCE_CONSUMER_GROUP')
        missing = [name for name in names if not env.get(name)]
        if missing:
            raise VerificationFailed('configuration', 'missing ' + ', '.join(missing))
        try:
            ids = [str(uuid.UUID(env[name])) for name in names[:3]]
        except ValueError:
            raise VerificationFailed('configuration', 'Fabric workspace, Eventstream and Eventhouse IDs must be UUIDs') from None
        endpoint = env['FABRIC_KQL_ENDPOINT'].rstrip('/')
        url = urlsplit(endpoint)
        if (url.scheme != 'https' or not url.hostname or not url.hostname.endswith('.kusto.fabric.microsoft.com')
                or url.username or url.password or url.query or url.fragment or url.path):
            raise VerificationFailed('configuration', 'FABRIC_KQL_ENDPOINT must be a bare https://*.kusto.fabric.microsoft.com URI')
        return cls(*ids, endpoint, env['FABRIC_KQL_DATABASE'], env['FABRIC_SOURCE_CONSUMER_GROUP'])


def validate(azure, target):
    """Checks that need no network access and must pass before any Fabric read or Event Hubs write."""
    if azure.connection_string is not None:
        raise VerificationFailed('configuration', 'unset AZURE_EVENT_HUB_CONNECTION_STRING; this verification uses Entra ID only')
    if not azure.namespace:
        raise VerificationFailed('configuration', 'AZURE_EVENT_HUB_FULLY_QUALIFIED_NAMESPACE is required')
    if azure.consumer_group == target.source_consumer_group:
        raise VerificationFailed('configuration', 'readback consumer group must differ from the Fabric source consumer group')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', target.table):
        raise VerificationFailed('configuration', 'invalid table name')


def run_envelopes(run_id, produced_at):
    """Deterministic seed-99 prefix up to and including its first goal, all tagged with one run ID."""
    match, events = seed99()
    prefix = events[:next(i + 1 for i, e in enumerate(events) if e.event_type.value == 'GOAL')]
    envelopes = [EventEnvelope.wrap(e, i, source=SOURCE_LABEL, produced_at=produced_at, correlation_id=run_id)
                 for i, e in enumerate(prefix)]
    return match.match_id, envelopes


def key(envelope):
    return envelope.match_id, envelope.event_id, envelope.sequence_number


def connection_endpoint(details):
    """Parse an EventHub connection's path, a JSON object with endpoint, entityPath and consumerGroup."""
    if (details or {}).get('type') != 'EventHub':
        raise VerificationFailed('topology', 'source connection is not an EventHub connection')
    try:
        path = json.loads(details.get('path') or '')
    except ValueError:
        path = None
    if not isinstance(path, dict) or not isinstance(path.get('endpoint'), str) or not isinstance(path.get('entityPath'), str):
        raise VerificationFailed('topology', 'source connection path is not the expected endpoint/entityPath JSON object')
    endpoint = path['endpoint'].lower().removeprefix('sb://').rstrip('/')
    group = path.get('consumerGroup')
    return endpoint, path['entityPath'], group if isinstance(group, str) else None


def connection_matches(details, namespace, eventhub):
    endpoint, entity, _ = connection_endpoint(details)
    return endpoint == namespace.lower() and entity == eventhub


def reaches(topology, start, end):
    """True when node `end` consumes (transitively) from node `start` through inputNodes links."""
    nodes = [n for part in ('sources', 'streams', 'operators', 'destinations') for n in topology.get(part) or []]
    consumers = {}
    for node in nodes:
        for upstream in node.get('inputNodes') or []:
            consumers.setdefault(upstream.get('name'), set()).add(node.get('name'))
    seen, frontier = {start}, [start]
    while frontier:
        for name in consumers.get(frontier.pop(), ()):
            if name not in seen:
                seen.add(name)
                frontier.append(name)
    return end in seen


async def verify_topology(fabric, azure, target):
    for item, kind in ((target.eventstream_id, 'Eventstream'), (target.eventhouse_id, 'Eventhouse')):
        if (await fabric.item(target.workspace_id, item)).get('type') != kind:
            raise VerificationFailed('topology', f'configured {kind} ID is not an {kind} item')
    topology = await fabric.definition(target.workspace_id, target.eventstream_id)
    sources = [s for s in topology.get('sources') or [] if s.get('type') in SOURCE_TYPES]
    if not sources:
        observed = sorted({str(s.get('type')) for s in topology.get('sources') or []})
        raise VerificationFailed('topology', f'no Azure Event Hubs source in the Eventstream (observed source types: {observed})')
    destinations = [d for d in topology.get('destinations') or [] if d.get('type') == 'Eventhouse']
    if not destinations:
        raise VerificationFailed('topology', 'Eventstream has no Eventhouse destination')
    observed = []
    for source in sources:
        properties = source.get('properties') or {}
        group = properties.get('consumerGroupName')
        connection_id = properties.get('dataConnectionId')
        if not connection_id:
            raise VerificationFailed('topology', 'Event Hubs source has no dataConnectionId')
        connection = await fabric.connection(connection_id)
        details = connection.get('connectionDetails') or {}
        endpoint, entity, connection_group = connection_endpoint(details)
        same_hub = connection_matches(details, azure.namespace, azure.eventhub_name)
        observed.append({'consumer_group': group, 'matches_namespace_and_hub': same_hub})
        if not same_hub or group != target.source_consumer_group:
            continue
        if connection_group not in (None, group):
            raise VerificationFailed('topology', 'source connection consumer group differs from the Eventstream source')
        routed = [d for d in destinations if reaches(topology, source.get('name'), d.get('name'))]
        if not routed:
            raise VerificationFailed('topology', 'matching Event Hubs source does not route to an Eventhouse destination')
        destination = await verify_destination(fabric, target, routed)
        return {'source_name': source.get('name'), 'source_type': source.get('type'), 'source_consumer_group': group,
                'source_start_position': properties.get('startPosition'), 'connection_id': connection_id,
                'connection_type': details.get('type'), 'connection_endpoint': endpoint, 'connection_entity': entity,
                'connection_credential_type': (connection.get('credentialDetails') or {}).get('credentialType'),
                **destination}
    raise VerificationFailed('topology', f'no Event Hubs source reads {azure.eventhub_name} on {azure.namespace} '
                             f'with consumer group {target.source_consumer_group} (observed: {observed})')


async def verify_destination(fabric, target, destinations):
    """An Eventhouse destination references its KQL database item; that database must belong to the
    configured Eventhouse, be the configured database and serve the configured query URI."""
    for destination in destinations:
        properties = destination.get('properties') or {}
        if properties.get('workspaceId') != target.workspace_id or not properties.get('itemId'):
            continue
        database = await fabric.kql_database(target.workspace_id, properties['itemId'])
        details = database.get('properties') or {}
        if (details.get('parentEventhouseItemId') != target.eventhouse_id or database.get('displayName') != target.kql_database
                or (details.get('queryServiceUri') or '').rstrip('/') != target.kql_endpoint):
            continue
        if properties.get('tableName') != target.table:
            raise VerificationFailed('topology', f'Eventhouse destination does not write to table {target.table}')
        return {'destination_name': destination.get('name'), 'destination_table': properties.get('tableName'),
                'kql_database_item_id': properties['itemId'], 'ingestion_mode': properties.get('dataIngestionMode'),
                'mapping_rule_name': properties.get('mappingRuleName')}
    raise VerificationFailed('topology', 'no routed Eventhouse destination writes to the configured Eventhouse database')


async def read_back(readback, expected, run_id, timeout):
    """Collect this run's envelopes from the dedicated consumer group; any altered envelope fails."""
    received = []

    async def collect():
        async for item in readback.events():
            if not isinstance(item, EventEnvelope):
                raise VerificationFailed('eventhub_readback', 'invalid envelope received')
            if item.correlation_id != run_id:
                continue
            if expected.get(key(item)) != item:
                raise VerificationFailed('eventhub_readback', 'envelope differs from the published envelope')
            received.append(item)
            if len({key(e) for e in received}) == len(expected):
                return
    try:
        await asyncio.wait_for(collect(), timeout)
    except TimeoutError:
        raise VerificationFailed('eventhub_readback', 'timed out before every published event was read back') from None
    except VerificationFailed:
        raise
    except Exception as error:
        raise VerificationFailed('eventhub_readback', f'receive failed ({type(error).__name__})') from None
    if len({key(e) for e in received}) != len(expected):
        raise VerificationFailed('eventhub_readback', 'stream ended before every published event was read back')
    return received


async def query_destination(fabric, target, expected, run_id, *, timeout, interval, sleep, clock):
    """Poll the Eventhouse table for this run until every event and the deliberate duplicate are present."""
    csl = f'{target.table} | where correlation_id == "{run_id}" | project envelope'
    deadline = clock() + timeout
    rows = []
    while True:
        data = await fabric.query(target.kql_endpoint, target.kql_database, csl)
        try:
            rows = fabric_envelopes(data, expected)
        except ValueError:
            raise VerificationFailed('fabric_destination', 'stored envelope differs from the published envelope') from None
        if any(e.correlation_id != run_id for e in rows):
            raise VerificationFailed('fabric_destination', 'query returned rows from another run')
        if len({key(e) for e in rows}) == len(expected) and len(rows) >= len(expected) + 1:
            return rows
        if clock() >= deadline:
            raise VerificationFailed('fabric_destination', f'timed out with {len({key(e) for e in rows})} of '
                                     f'{len(expected)} events (and the duplicate) observed in {target.table}')
        await sleep(interval)


def now():
    return datetime.now(timezone.utc)


async def verify_transport(azure, target, *, fabric, producer, readback, run_id=None, readback_timeout=120,
                           kql_timeout=300, kql_interval=5, sleep=asyncio.sleep, clock=None):
    """Run the bounded proof; always returns a sanitized record and never raises for verification failures."""
    clock = clock or asyncio.get_running_loop().time
    run_id = run_id or uuid.uuid4().hex
    started = now()
    match_id, envelopes = run_envelopes(run_id, started)
    expected = {key(e): e for e in envelopes}
    published = [envelopes[0], envelopes[0], *envelopes[1:]]
    record = {'status': 'failed', 'run_id': run_id, 'started_at': started.isoformat(), 'finished_at': None,
              'scope': SCOPE, 'agents_run': False, 'foundry_called': False,
              'source': {'generator': 'seed-99 synthetic match, prefix through first goal', 'match_id': match_id,
                         'unique_events': len(envelopes), 'published_messages': len(published), 'deliberate_duplicates': 1},
              'eventhubs': {'namespace': azure.namespace, 'eventhub': azure.eventhub_name,
                            'readback_consumer_group': azure.consumer_group, 'auth': 'entra_id',
                            'published': 0, 'readback_unique': 0, 'readback_total': 0, 'integrity_verified': False},
              'fabric': {'workspace_id': target.workspace_id, 'eventstream_id': target.eventstream_id,
                         'eventhouse_id': target.eventhouse_id, 'kql_database': target.kql_database, 'table': target.table,
                         'topology_verified': False, 'rows_unique': 0, 'rows_total': 0, 'integrity_verified': False},
              'identifiers_consistent': False, 'failure': None}
    stage = 'configuration'
    try:
        validate(azure, target)
        stage = 'topology'
        record['fabric'].update(await verify_topology(fabric, azure, target), topology_verified=True)
        stage = 'eventhub_readback'
        await readback.start()
        try:
            await asyncio.wait_for(readback.ready(), readback_timeout)
        except TimeoutError:
            raise VerificationFailed('eventhub_readback', 'readback consumer did not initialize; nothing was published') from None
        stage = 'publish'
        await producer.start()
        await producer.publish_batch(published)
        record['eventhubs']['published'] = len(published)
        stage = 'eventhub_readback'
        received = await read_back(readback, expected, run_id, readback_timeout)
        record['eventhubs'].update(readback_unique=len({key(e) for e in received}), readback_total=len(received),
                                   integrity_verified=True)
        stage = 'fabric_destination'
        rows = await query_destination(fabric, target, expected, run_id, timeout=kql_timeout, interval=kql_interval,
                                       sleep=sleep, clock=clock)
        record['fabric'].update(rows_unique=len({key(e) for e in rows}), rows_total=len(rows), integrity_verified=True)
        stages = [set(expected), {key(e) for e in received}, {key(e) for e in rows}]
        if not stages[0] == stages[1] == stages[2]:
            raise VerificationFailed('comparison', 'event identifiers differ between stages')
        record['identifiers_consistent'] = True
        record['status'] = 'verified'
    except VerificationFailed as failure:
        record['failure'] = {'stage': failure.stage, 'reason': failure.reason}
    except Exception as error:
        # SDK and HTTP errors can carry URLs or bodies; record only the stage and exception type.
        record['failure'] = {'stage': stage, 'reason': f'unexpected {type(error).__name__}'}
    finally:
        for closer in (producer.stop, readback.stop):
            try:
                await closer()
            except Exception:
                pass
        record['finished_at'] = now().isoformat()
    return record


class EventHubReadback:
    """Adapter over the production consumer/source; ready() waits for every partition to initialize."""

    def __init__(self, azure, run_id, expected_count):
        self.consumer = AzureEventHubConsumer(azure)
        self.source = AzureEventHubSource(self.consumer, correlation_ids=[run_id], expected_event_count=expected_count)

    async def start(self):
        await self.source.start()

    async def ready(self):
        await self.consumer.ready.wait()

    def events(self):
        return self.source.events()

    async def stop(self):
        await self.source.stop()


class FabricReader:
    """Read-only Fabric REST and KQL calls. Errors surface as HTTP status codes only, never bodies."""

    def __init__(self, credential, client):
        self.credential = credential
        self.client = client

    async def _headers(self, scope):
        return {'Authorization': 'Bearer ' + (await self.credential.get_token(scope)).token}

    async def _json(self, response, what):
        if response.status_code >= 400:
            raise VerificationFailed('topology' if what != 'KQL query' else 'fabric_destination',
                                     f'{what} returned HTTP {response.status_code}')
        return response.json()

    async def item(self, workspace, item):
        headers = await self._headers('https://api.fabric.microsoft.com/.default')
        return await self._json(await self.client.get(f'{FABRIC_API}/workspaces/{workspace}/items/{item}', headers=headers), 'Fabric item read')

    async def definition(self, workspace, item):
        headers = await self._headers('https://api.fabric.microsoft.com/.default')
        response = await self.client.post(f'{FABRIC_API}/workspaces/{workspace}/items/{item}/getDefinition', headers=headers)
        if response.status_code == 202:
            location = response.headers.get('Location', '')
            if not location.startswith(FABRIC_API + '/operations/'):
                raise VerificationFailed('topology', 'unexpected Fabric operation location')
            for _ in range(30):
                state = await self._json(await self.client.get(location, headers=headers), 'Fabric definition operation')
                if state.get('status') == 'Succeeded':
                    response = await self.client.get(location + '/result', headers=headers)
                    break
                if state.get('status') == 'Failed':
                    raise VerificationFailed('topology', 'Fabric definition operation failed')
                await asyncio.sleep(2)
            else:
                raise VerificationFailed('topology', 'Fabric definition operation timed out')
        parts = (await self._json(response, 'Eventstream definition read')).get('definition', {}).get('parts', [])
        payload = next((p.get('payload') for p in parts if p.get('path') == 'eventstream.json'), None)
        if payload is None:
            raise VerificationFailed('topology', 'Eventstream definition has no eventstream.json part')
        return json.loads(base64.b64decode(payload))

    async def kql_database(self, workspace, item):
        headers = await self._headers('https://api.fabric.microsoft.com/.default')
        return await self._json(await self.client.get(f'{FABRIC_API}/workspaces/{workspace}/kqlDatabases/{item}', headers=headers), 'KQL database read')

    async def connection(self, connection_id):
        headers = await self._headers('https://api.fabric.microsoft.com/.default')
        return await self._json(await self.client.get(f'{FABRIC_API}/connections/{connection_id}', headers=headers), 'Fabric connection read')

    async def query(self, endpoint, database, csl):
        headers = await self._headers('https://api.kusto.windows.net/.default')
        return await self._json(await self.client.post(endpoint + '/v1/rest/query', headers=headers,
                                                       json={'db': database, 'csl': csl}), 'KQL query')


async def run(env=None, **options):
    """Live entry point: builds Entra-authenticated clients from the environment."""
    import httpx
    from azure.identity.aio import DefaultAzureCredential
    try:
        azure = AzureConfig.from_env()
        target = FabricTarget.from_env(env)
        validate(azure, target)
    except VerificationFailed as failure:
        return {'status': 'failed', 'failure': {'stage': failure.stage, 'reason': failure.reason}}
    except ValueError:
        return {'status': 'failed', 'failure': {'stage': 'configuration', 'reason': 'invalid Event Hubs settings'}}
    run_id = uuid.uuid4().hex
    async with DefaultAzureCredential() as credential, httpx.AsyncClient(timeout=60) as client:
        return await verify_transport(azure, target, fabric=FabricReader(credential, client),
                                      producer=AzureEventHubProducer(azure),
                                      readback=EventHubReadback(azure, run_id, len(run_envelopes(run_id, now())[1])),
                                      run_id=run_id, **options)
