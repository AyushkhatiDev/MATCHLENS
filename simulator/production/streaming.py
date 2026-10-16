"""Opt-in hosted synthetic-match streaming for the public demo.

One run at a time: the API publishes the recorded seed-99 match to Event Hubs (paced, tagged
`{run_id}:{sequence}`), consumes it back with its own consumer group through the normal
MatchService pipeline, and, when Fabric is configured, verifies the Eventstream topology before
publishing and reconciles the same run in the Eventhouse afterwards. Recorded replay is unaffected.
Managed identity only; the public status never includes resource identifiers or credentials.
"""
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from simulator.realtime.azure import AzureConfig
from simulator.realtime.models import EventEnvelope
from simulator.realtime.sources import LocalReplaySource, correlated_replay
from .transport import FabricTarget, VerificationFailed, key, query_destination, reconcile, validate, verify_topology

# Consumer groups the API must never read with: Fabric's source group is checked separately.
RESERVED_GROUPS = frozenset({'$Default', 'matchlens-verification'})


class StreamRejected(Exception):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def boolean(env, name):
    value = env.get(name, 'false').lower()
    if value not in ('true', 'false'):
        raise ValueError(f'{name} must be true or false')
    return value == 'true'


@dataclass(frozen=True)
class StreamSettings:
    azure: AzureConfig
    fabric: FabricTarget | None
    speed: float = 20
    run_timeout: float = 900
    cooldown: float = 300
    runs_per_hour: int = 4
    fabric_timeout: float = 300

    @classmethod
    def from_env(cls, env):
        """Validated streaming configuration, or None when streaming is not enabled. Fails closed."""
        if not boolean(env, 'APP_STREAMING'):
            return None
        if env.get('AZURE_EVENT_HUB_CONNECTION_STRING'):
            raise ValueError('Streaming uses managed identity only; unset AZURE_EVENT_HUB_CONNECTION_STRING')
        group = env.get('AZURE_EVENT_HUB_CONSUMER_GROUP', '')
        if not group or group in RESERVED_GROUPS:
            raise ValueError('Streaming needs its own AZURE_EVENT_HUB_CONSUMER_GROUP (not $Default or the verifier group)')
        azure = AzureConfig(namespace=env.get('AZURE_EVENT_HUB_FULLY_QUALIFIED_NAMESPACE') or None,
                            eventhub_name=env.get('AZURE_EVENT_HUB_NAME') or None, consumer_group=group,
                            receive_timeout_seconds=float(env.get('REALTIME_RECEIVE_TIMEOUT_SECONDS', '60')),
                            publish_timeout_seconds=float(env.get('REALTIME_PUBLISH_TIMEOUT_SECONDS', '30')),
                            max_attempts=int(env.get('REALTIME_MAX_ATTEMPTS', '3')),
                            batch_size=int(env.get('REALTIME_BATCH_SIZE', '32')))
        fabric = None
        if boolean(env, 'APP_STREAM_FABRIC_VERIFY'):
            try:
                fabric = FabricTarget.from_env(env)
                validate(azure, fabric)
            except VerificationFailed as failure:
                raise ValueError(f'Fabric verification configuration invalid: {failure.reason}') from None
        settings = cls(azure, fabric, speed=float(env.get('APP_STREAM_SPEED', '20')),
                       run_timeout=float(env.get('APP_STREAM_RUN_TIMEOUT_SECONDS', '900')),
                       cooldown=float(env.get('APP_STREAM_COOLDOWN_SECONDS', '300')),
                       runs_per_hour=int(env.get('APP_STREAM_RUNS_PER_HOUR', '4')),
                       fabric_timeout=float(env.get('APP_STREAM_FABRIC_TIMEOUT_SECONDS', '300')))
        if not 1 <= settings.speed <= 100 or not 60 <= settings.run_timeout <= 3600 or settings.cooldown < 0 \
                or not 1 <= settings.runs_per_hour <= 60 or not 30 <= settings.fabric_timeout <= 1800:
            raise ValueError('Streaming limits out of range')
        return settings


def now():
    return datetime.now(timezone.utc)


@dataclass
class StreamRun:
    run_id: str
    expected: int
    state: str = 'starting'
    started_at: str = field(default_factory=lambda: now().isoformat())
    finished_at: str | None = None
    published: list = field(default_factory=list)
    publish_acknowledged: int = 0
    fabric: str = 'NOT CONFIGURED'
    topology: dict | None = None
    reconciliation: dict | None = None
    failure: dict | None = None


class StreamController:
    """Single-active-run orchestration around a MatchService configured for Event Hubs input."""

    def __init__(self, settings, service, *, producer_factory, fabric_factory=None, clock=time.monotonic, sleep=asyncio.sleep):
        self.settings = settings
        self.service = service
        self.producer_factory = producer_factory
        self.fabric_factory = fabric_factory
        self.clock = clock
        self.sleep = sleep
        self.run = None
        self.task = None
        self.starts = []
        self.lock = asyncio.Lock()

    def active(self):
        return self.task is not None and not self.task.done()

    async def start(self):
        async with self.lock:
            if self.active():
                raise StreamRejected(409, 'A synthetic match is already streaming; watch it or wait for it to finish')
            moment = self.clock()
            self.starts = [t for t in self.starts if moment - t < 3600]
            if self.starts and moment - self.starts[-1] < self.settings.cooldown:
                raise StreamRejected(429, 'Streaming cooldown in effect; try again shortly')
            if len(self.starts) >= self.settings.runs_per_hour:
                raise StreamRejected(429, 'Hourly streaming budget reached; recorded replay remains available')
            self.starts.append(moment)
            expected = sum(1 for line in Path(self.service.path).read_text().splitlines() if line.strip())
            self.run = StreamRun(uuid.uuid4().hex, expected, fabric='PENDING' if self.settings.fabric else 'NOT CONFIGURED')
            self.task = asyncio.create_task(self._execute(self.run))
            return self.status()

    async def _execute(self, run):
        stage = 'starting'
        producer = None
        try:
            async with asyncio.timeout(self.settings.run_timeout):
                fabric = self.fabric_factory() if self.settings.fabric else None
                if fabric:
                    stage = run.state = 'topology'
                    run.topology = await verify_topology(fabric, self.settings.azure, self.settings.fabric)
                    run.fabric = 'TOPOLOGY VERIFIED'
                stage = run.state = 'connecting'
                ids = {f'{run.run_id}:{i}' for i in range(run.expected)}
                await self.service.start_stream(ids, run.expected)
                ready = await self.service.stream_ready(self.settings.azure.receive_timeout_seconds)
                if not ready:
                    raise VerificationFailed('connecting', 'API consumer did not connect; nothing was published')
                stage = run.state = 'publishing'
                producer = self.producer_factory(self.settings.azure)
                await producer.start()
                replay = LocalReplaySource(self.service.path, self.settings.speed)
                await replay.start()
                try:
                    run.publish_acknowledged = await producer.publish_stream(correlated_replay(replay, run.run_id, published=run.published))
                finally:
                    await replay.stop()
                stage = run.state = 'consuming'
                await self.service.stream_finished()
                if self.service.status != 'complete':
                    raise VerificationFailed('consuming', 'API consumer stopped before receiving the whole run')
                expected = {key(e): e for e in run.published}
                readback = self.service.stream_received
                altered = sum(1 for e in readback if expected.get(key(e)) not in (None, e))
                stages = {'published': ([key(e) for e in run.published],),
                          'eventhub_readback': ([key(e) for e in readback], altered),
                          'api_processed': (list(self.service.stream_processed),), 'fabric': None}
                if fabric:
                    stage = run.state = 'reconciling'
                    rows = await query_destination(fabric, self.settings.fabric, expected, run.run_id, prefix=True,
                                                   min_rows=len(expected), timeout=self.settings.fabric_timeout,
                                                   interval=10, sleep=self.sleep, clock=self.clock)
                    stages['fabric'] = ([key(e) for e in rows],)
                run.reconciliation = reconcile(expected, stages)
                transport = all(run.reconciliation['stages'][s] and run.reconciliation['stages'][s]['missing'] == 0
                                and not run.reconciliation['stages'][s]['altered'] and not run.reconciliation['stages'][s]['unexpected']
                                for s in ('published', 'eventhub_readback', 'api_processed'))
                if not transport:
                    raise VerificationFailed('reconciling', 'published, Event Hubs and API event sets differ')
                if fabric:
                    if not run.reconciliation['reconciled']:
                        raise VerificationFailed('reconciling', 'Fabric event set differs from the published run')
                    run.fabric = 'VERIFIED'
                run.state = 'complete'
        except TimeoutError:
            run.failure = {'stage': stage, 'reason': 'run exceeded its time limit'}
        except VerificationFailed as failure:
            run.failure = {'stage': failure.stage, 'reason': failure.reason}
        except asyncio.CancelledError:
            run.failure = {'stage': stage, 'reason': 'run cancelled'}
            raise
        except Exception as error:
            # SDK errors can carry URLs or bodies: keep only the stage and exception type.
            run.failure = {'stage': stage, 'reason': f'unexpected {type(error).__name__}'}
        finally:
            if run.failure:
                run.state = 'failed'
                if run.fabric in ('PENDING', 'TOPOLOGY VERIFIED'):
                    run.fabric = 'FAILED' if run.failure['stage'] in ('topology', 'reconciling', 'fabric_destination') else 'NOT VERIFIED'
                await self.service.stop_stream()
            if producer is not None:
                try:
                    await producer.stop()
                except Exception:
                    pass
            run.finished_at = now().isoformat()

    def status(self):
        """Public, sanitized run status: counts and states only, no resource identifiers."""
        run = self.run
        service = self.service
        stages = (run.reconciliation or {}).get('stages') if run else None
        return {'enabled': True, 'source_mode': 'eventhub', 'consumer_group': self.settings.azure.consumer_group,
                'speed': self.settings.speed, 'active': self.active(),
                'run': None if run is None else {
                    'run_id': run.run_id, 'state': run.state, 'started_at': run.started_at, 'finished_at': run.finished_at,
                    'expected_events': run.expected, 'published': len(run.published),
                    'publish_acknowledged': run.publish_acknowledged,
                    'api_received': len(service.stream_received), 'api_processed': len(service.stream_processed),
                    'processing_errors': service.stream_errors, 'last_event_at': service.received_at,
                    'consumer_ready': service.stream_connected(),
                    'fabric': run.fabric, 'reconciled': bool(run.reconciliation and run.reconciliation['reconciled']),
                    'stages': stages, 'failure': run.failure}}

    async def close(self):
        if self.active():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
