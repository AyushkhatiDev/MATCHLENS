"""Bounded Event Hubs → Fabric transport verification; writes a sanitized record.

The live run publishes one deterministic batch to Event Hubs (Entra ID only), reads it back on a
dedicated consumer group and queries the Eventhouse table. --dry-run validates configuration
without credentials or network access. No agents run and Microsoft Foundry is never called.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from simulator.production.transport import FabricTarget, VerificationFailed, run, run_envelopes, validate
from simulator.realtime.azure import AzureConfig

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument('--dry-run', action='store_true', help='Validate configuration only; no credentials, network or cloud writes.')
parser.add_argument('--output', type=Path, default=Path('docs/submission/transport-verification.json'))
parser.add_argument('--readback-timeout', type=float, default=120)
parser.add_argument('--kql-timeout', type=float, default=300)
parser.add_argument('--kql-interval', type=float, default=5)
args = parser.parse_args()

if args.dry_run:
    try:
        azure = AzureConfig.from_env()
        target = FabricTarget.from_env()
        validate(azure, target)
    except VerificationFailed as failure:
        sys.exit(f'Configuration invalid ({failure.stage}): {failure.reason}')
    except ValueError:
        sys.exit('Configuration invalid: Event Hubs settings (namespace FQDN and hub name required).')
    _, envelopes = run_envelopes('0' * 32, None)
    print(json.dumps({'dry_run': True, 'eventhubs': {'namespace': azure.namespace, 'eventhub': azure.eventhub_name,
                      'readback_consumer_group': azure.consumer_group, 'auth': 'entra_id'},
                      'fabric': {'workspace_id': target.workspace_id, 'eventstream_id': target.eventstream_id,
                                 'eventhouse_id': target.eventhouse_id, 'kql_database': target.kql_database,
                                 'table': target.table, 'source_consumer_group': target.source_consumer_group},
                      'would_publish_messages': len(envelopes) + 1, 'unique_events': len(envelopes)}, indent=2))
    sys.exit(0)

record = asyncio.run(run(readback_timeout=args.readback_timeout, kql_timeout=args.kql_timeout, kql_interval=args.kql_interval))
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record, indent=2))
sys.exit(0 if record.get('status') == 'verified' else 1)
