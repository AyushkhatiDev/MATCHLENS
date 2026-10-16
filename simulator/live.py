"""Phase 5 local replay / real Event Hubs CLI."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import signal
from pathlib import Path
import sys
import time
import uuid
from simulator.generation.generator import generate_match
from simulator.models.match import Match
from simulator.agentic.config import AgenticConfig
from simulator.realtime.models import EventEnvelope, parse_event
from simulator.realtime.sources import LocalReplaySource
from simulator.realtime.processor import EventProcessor
from simulator.realtime.pipeline import run_pipeline

def load_match(path, metadata=None):
    path = Path(path)
    metadata = Path(metadata) if metadata else path.with_name(path.stem + '_meta.json')
    value = json.loads(metadata.read_text())
    # New callers may provide a full Match. Existing Phase 1 metadata omits
    # attributes/team_id on players; regenerate its deterministic roster and
    # verify identity, rather than inventing attributes or changing that schema.
    if 'competition_name' in value:
        return Match.model_validate(value)
    match = generate_match(seed=value['random_seed'], duration_minutes=value['duration_minutes'])
    if (match.match_id != value['match_id'] or match.home_team.team_id != value['home_team']['team_id']
            or match.away_team.team_id != value['away_team']['team_id']
            or [p.player_id for p in match.home_team.players] != [p['player_id'] for p in value['home_team']['players']]
            or [p.player_id for p in match.away_team.players] != [p['player_id'] for p in value['away_team']['players']]):
        raise ValueError('Metadata roster cannot be restored; supply a full Match JSON with --metadata')
    return match

async def run(args):
    # Validate Azure settings before opening clients or generating any output.
    azure_config = None
    args.mode = resolve_mode(args.mode)
    if getattr(args, 'fabric_reconcile', False) and args.mode != 'azure':
        raise ValueError('Fabric reconciliation requires Azure mode')
    if args.mode == 'azure':
        from simulator.realtime.azure import AzureConfig
        azure_config = AzureConfig.from_env()
    match = load_match(args.match, args.metadata)
    if args.agent_mode == 'mock':
        config = AgenticConfig(mode='mock')
    else:
        config = AgenticConfig.from_env({**os.environ, 'AGENTIC_MODE': 'foundry'})
    async def result_callback(result):
        print('AGENTIC WORKFLOW RESULT', result.workflow_status, result.moment_id)
        print(' -> '.join(step.stage + ':' + step.status for step in result.state.trace_steps))
    processor = EventProcessor([match], explain_notable=args.explain_notable, agent_config=config,
                               result_callback=result_callback)
    async def event_callback(event, status):
        if isinstance(event, EventEnvelope) and not args.quiet:
            minute, second = divmod(int(event.simulation_timestamp), 60)
            print(f'{minute:02d}:{second:02d} {event.event_type.value} {status}')
    started = time.perf_counter()
    record = None
    if args.mode == 'local':
        await run_pipeline(LocalReplaySource(args.match, args.speed), processor, event_callback=event_callback)
    else:
        from simulator.realtime.azure import AzureEventHubProducer, AzureEventHubConsumer, AzureEventHubSource
        from simulator.realtime.sources import correlated_replay
        events = [parse_event(json.loads(line)) for line in Path(args.match).read_text().splitlines() if line.strip()]
        if any(e.match_id != match.match_id for e in events):
            raise ValueError('Azure demo input must belong to its configured match')
        if not events:
            raise ValueError('Azure replay input is empty')
        fabric_group = os.getenv('FABRIC_SOURCE_CONSUMER_GROUP')
        if fabric_group and azure_config.consumer_group == fabric_group:
            raise ValueError('Readback consumer group must differ from the Fabric source consumer group')
        run_id = uuid.uuid4().hex
        correlations = {f'{run_id}:{index}' for index in range(len(events))}
        producer = AzureEventHubProducer(azure_config)
        consumer = AzureEventHubConsumer(azure_config)
        source = AzureEventHubSource(consumer, correlation_ids=correlations, expected_event_count=len(events))
        replay = LocalReplaySource(args.match, args.speed)
        published = 0
        sent, received, processed = [], [], []
        async def recording_callback(event, status):
            if isinstance(event, EventEnvelope):
                received.append(event)
                if status == 'PROCESSED':
                    processed.append((event.match_id, event.event_id, event.sequence_number))
            await event_callback(event, status)
        async def publish():
            nonlocal published
            await asyncio.wait_for(consumer.ready.wait(), azure_config.receive_timeout_seconds)
            await producer.start()
            await replay.start()
            published = await producer.publish_stream(correlated_replay(replay, run_id, published=sent))
            print(f'Event Hubs acknowledged {published} canonical replay events; Fabric receipt requires downstream verification.')
        reconciler = (FullMatchReconciler(azure_config, run_id, match.match_id, len(events), args)
                      if getattr(args, 'fabric_reconcile', False) else None)
        try:
            if reconciler:
                await reconciler.verify_topology()
            async with asyncio.TaskGroup() as group:
                group.create_task(run_pipeline(source, processor, event_callback=recording_callback))
                group.create_task(publish())
            if reconciler:
                await reconciler.reconcile(sent, received, processed)
        except BaseException as error:
            if reconciler:
                reconciler.fail(error)
            raise
        finally:
            await replay.stop()
            await producer.stop()
            if reconciler:
                record = await reconciler.finish(published)
                if args.record:
                    Path(args.record).parent.mkdir(parents=True, exist_ok=True)
                    Path(args.record).write_text(json.dumps(record, indent=2) + '\n')
    duration = time.perf_counter() - started
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    intel = processor.final_intelligence(match.match_id)
    (output / 'match_intelligence.json').write_text(intel.model_dump_json(indent=2))
    (output / 'agent_results.json').write_text(json.dumps(list(processor.results), indent=2))
    (output / 'processing_records.json').write_text(json.dumps([r.model_dump(mode='json') for r in processor.records], indent=2))
    (output / 'workflow_records.json').write_text(json.dumps(list(processor.workflow_records), indent=2))
    measurement = {'elapsed_seconds': duration, 'events_per_second': processor.metrics.events_processed / duration,
                   'metrics': processor.metrics.model_dump(mode='json'),
                   'azure_live_tested': args.mode == 'azure'}
    if args.mode == 'azure':
        measurement.update(events_published=published, replay_run_id=run_id,
                           namespace=azure_config.namespace, eventhub=azure_config.eventhub_name,
                           fabric_verified=bool(record and record['status'] == 'verified'))
    (output / 'metrics.json').write_text(json.dumps(measurement, indent=2))
    print(json.dumps(measurement, indent=2))
    return processor

class FullMatchReconciler:
    """Full-match Fabric reconciliation for --fabric-reconcile, reusing the transport verifier's
    topology check, run-scoped KQL query and stage reconciliation. Entra ID only."""

    def __init__(self, azure_config, run_id, match_id, expected, args, *, fabric=None, target=None, clock=None, sleep=asyncio.sleep):
        from simulator.production.transport import FabricTarget, validate
        self.target = target or FabricTarget.from_env()
        validate(azure_config, self.target)
        self.azure, self.run_id, self.match_id, self.expected, self.args = azure_config, run_id, match_id, expected, args
        self.fabric, self.clock, self.sleep = fabric, clock, sleep
        self.resources = []
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.topology = None
        self.reconciliation = None
        self.failure = None
        self.stage = 'topology'

    def reader(self):
        if self.fabric is None:
            import httpx
            from azure.identity.aio import DefaultAzureCredential
            from simulator.production.transport import FabricReader
            credential, client = DefaultAzureCredential(), httpx.AsyncClient(timeout=60)
            self.resources = [credential, client]
            self.fabric = FabricReader(credential, client)
        return self.fabric

    async def verify_topology(self):
        from simulator.production.transport import verify_topology
        self.topology = await verify_topology(self.reader(), self.azure, self.target)
        self.stage = 'publish'

    async def reconcile(self, sent, received, processed):
        from simulator.production.transport import VerificationFailed, key, query_destination, reconcile
        expected = {key(e): e for e in sent}
        if len(expected) != self.expected:
            raise VerificationFailed('publish', 'publisher did not hand over the whole match')
        altered = sum(1 for e in received if expected.get(key(e)) not in (None, e))
        self.stage = 'fabric_destination'
        rows = await query_destination(self.reader(), self.target, expected, self.run_id, prefix=True, min_rows=len(expected),
                                       timeout=self.args.fabric_timeout, interval=min(10.0, self.args.fabric_timeout / 10), sleep=self.sleep,
                                       clock=self.clock or asyncio.get_running_loop().time)
        self.reconciliation = reconcile(expected, {'published': ([key(e) for e in sent],),
                                                   'eventhub_readback': ([key(e) for e in received], altered),
                                                   'api_processed': (processed,), 'fabric': ([key(e) for e in rows],)})
        if not self.reconciliation['reconciled']:
            raise VerificationFailed('reconciling', 'stage event sets differ from the published match')

    def fail(self, error):
        from simulator.production.transport import VerificationFailed
        if isinstance(error, VerificationFailed):
            self.failure = {'stage': error.stage, 'reason': error.reason}
        else:
            # SDK errors can carry URLs or bodies; keep only the stage and exception type.
            self.failure = {'stage': self.stage, 'reason': f'unexpected {type(error).__name__}'}

    async def finish(self, published):
        for resource in self.resources:
            await asyncio.gather(resource.close() if hasattr(resource, 'close') else resource.aclose(), return_exceptions=True)
        from simulator.production.transport import full_match_record
        return full_match_record(run_id=self.run_id, started_at=self.started_at, match_id=self.match_id,
                                 expected=self.expected, speed=self.args.speed, agent_mode=self.args.agent_mode,
                                 azure=self.azure, target=self.target, topology=self.topology, published=published,
                                 reconciliation=self.reconciliation, failure=self.failure)


def resolve_mode(explicit=None):
    if explicit is not None:
        return explicit
    source = os.getenv('EVENT_SOURCE', 'local')
    if source not in ('local', 'eventhub'):
        raise ValueError('Invalid EVENT_SOURCE; values withheld')
    return 'azure' if source == 'eventhub' else 'local'

async def run_until_shutdown(args):
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        return await run(args)
    finally:
        loop.remove_signal_handler(signal.SIGTERM)

def main(argv=None):
    parser = argparse.ArgumentParser(description='MATCHLENS Phase 5 streaming intelligence')
    parser.add_argument('--match', required=True)
    parser.add_argument('--metadata', help='Full Match JSON or existing synthetic metadata (default: sibling *_meta.json)')
    parser.add_argument('--mode', choices=('local', 'azure'), default=None,
                        help='Defaults to EVENT_SOURCE (local or eventhub); explicit mode overrides it')
    parser.add_argument('--speed', type=float, default=10, help='Simulation seconds per wall second; 0 = instant benchmark')
    parser.add_argument('--agent-mode', choices=('mock', 'foundry'), default='mock')
    parser.add_argument('--explain-notable', action='store_true')
    parser.add_argument('--quiet', action='store_true')
    parser.add_argument('--output', default='data/generated/phase5/live')
    parser.add_argument('--fabric-reconcile', action='store_true',
                        help='Azure mode only: verify Fabric topology before publishing, then reconcile the run in the Eventhouse')
    parser.add_argument('--fabric-timeout', type=float, default=300)
    parser.add_argument('--record', help='Write the sanitized full-match verification record here (with --fabric-reconcile)')
    args = parser.parse_args(argv)
    if args.fabric_reconcile and args.mode == 'local':
        parser.error('--fabric-reconcile requires --mode azure')
    if args.record and not args.fabric_reconcile:
        parser.error('--record requires --fabric-reconcile')
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    # Azure SDK loggers can emit service details; application traces deliberately
    # log safe error codes instead. SDK HTTP wire logging is disabled on clients.
    logging.getLogger('azure').setLevel(logging.CRITICAL)
    try:
        asyncio.run(run_until_shutdown(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print('Replay stopped.', file=sys.stderr)
        raise SystemExit(130) from None
    except Exception as error:
        from pydantic import ValidationError
        if isinstance(error, ValidationError):
            if args.agent_mode == 'foundry':
                print('Configuration error: Foundry needs FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL; check Azure Event Hubs settings when --mode azure.', file=sys.stderr)
            else:
                print('Configuration error: set AZURE_EVENT_HUB_NAME and namespace or explicit connection-string fallback; check numeric settings.', file=sys.stderr)
        elif isinstance(error, (FileNotFoundError, KeyError)):
            print('Input error: replay JSONL and matching metadata are required.', file=sys.stderr)
        else:
            print('Replay failed: check input order, configuration, dependencies and safe structured error codes. No cloud fallback was used.', file=sys.stderr)
        raise SystemExit(1) from None

if __name__ == '__main__':
    main()
