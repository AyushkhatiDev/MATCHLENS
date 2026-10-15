"""Render the sanitized transport-verification record as submission evidence.

The renderer refuses any record that is not a fully verified run, has inconsistent counts,
claims agent or Foundry execution, carries unexpected fields or contains credential-like text.
It only restates what the record proves: one bounded transport run, not continuous monitoring
and not the public demo's input path.
"""
import hashlib
import json
import re
from datetime import datetime

KEYS = {
    '': {'status', 'run_id', 'started_at', 'finished_at', 'scope', 'agents_run', 'foundry_called', 'source',
         'eventhubs', 'fabric', 'identifiers_consistent', 'failure'},
    'source': {'generator', 'match_id', 'unique_events', 'published_messages', 'deliberate_duplicates'},
    'eventhubs': {'namespace', 'eventhub', 'readback_consumer_group', 'auth', 'published', 'readback_unique',
                  'readback_total', 'integrity_verified'},
    'fabric': {'workspace_id', 'eventstream_id', 'eventhouse_id', 'kql_database', 'table', 'topology_verified',
               'rows_unique', 'rows_total', 'integrity_verified', 'source_name', 'source_type', 'source_consumer_group',
               'source_start_position', 'connection_id', 'connection_type', 'connection_endpoint', 'connection_entity',
               'connection_credential_type', 'destination_name', 'destination_table', 'kql_database_item_id',
               'ingestion_mode', 'mapping_rule_name'},
}
SECRET = re.compile(r'(SharedAccessKey|AccountKey|Bearer\s|sig=|password|client_secret|eyJ[A-Za-z0-9_-]{10,}|-----BEGIN)', re.I)


class EvidenceError(ValueError):
    pass


def check(record):
    """Validate that the record is a sanitized, internally consistent, verified transport run."""
    for section, allowed in KEYS.items():
        value = record if not section else record.get(section)
        if not isinstance(value, dict):
            raise EvidenceError(f'missing section {section or "record"}')
        unexpected = set(value) - allowed
        if unexpected or allowed - set(value):
            raise EvidenceError(f'unexpected or missing fields in {section or "record"}')
    if SECRET.search(json.dumps(record)):
        raise EvidenceError('record contains credential-like text')
    if record['status'] != 'verified' or record['failure'] is not None:
        raise EvidenceError('record is not a verified run')
    if not re.fullmatch(r'[0-9a-f]{32}', record['run_id']):
        raise EvidenceError('run ID is not a 32-character hex identifier')
    if record['agents_run'] is not False or record['foundry_called'] is not False:
        raise EvidenceError('a transport record must not claim agent or Foundry execution')
    started, finished = (datetime.fromisoformat(record[k]) for k in ('started_at', 'finished_at'))
    if started.utcoffset() is None or finished < started:
        raise EvidenceError('timestamps must be timezone-aware and ordered')
    source, hubs, fabric = record['source'], record['eventhubs'], record['fabric']
    unique, published = source['unique_events'], source['published_messages']
    if published != unique + source['deliberate_duplicates'] or source['deliberate_duplicates'] < 1:
        raise EvidenceError('published count does not equal unique events plus deliberate duplicates')
    if (hubs['published'], hubs['readback_unique'], hubs['readback_total']) != (published, unique, published):
        raise EvidenceError('Event Hubs counts are inconsistent with the published batch')
    if fabric['rows_unique'] != unique or fabric['rows_total'] < published:
        raise EvidenceError('Fabric row counts are inconsistent with the published batch')
    flags = (hubs['integrity_verified'], fabric['integrity_verified'], fabric['topology_verified'], record['identifiers_consistent'])
    if not all(flag is True for flag in flags):
        raise EvidenceError('an integrity, topology or identifier check is not verified')
    if hubs['readback_consumer_group'] == fabric['source_consumer_group']:
        raise EvidenceError('readback must use a consumer group separate from Fabric')
    if (fabric['connection_endpoint'], fabric['connection_entity']) != (hubs['namespace'], hubs['eventhub']):
        raise EvidenceError('Fabric source connection does not match the published hub')
    if fabric['destination_table'] != fabric['table']:
        raise EvidenceError('Fabric destination table does not match the queried table')
    return record


def render(record, *, record_sha256):
    check(record)
    source, hubs, fabric = record['source'], record['eventhubs'], record['fabric']
    started, finished = (datetime.fromisoformat(record[k]) for k in ('started_at', 'finished_at'))
    rows = [
        ('Synthetic generator', f"{source['generator']} (match `{source['match_id']}`)", f"{source['unique_events']} unique events + "
         f"{source['deliberate_duplicates']} deliberate duplicate = {source['published_messages']} messages"),
        ('Azure Event Hubs publish', f"`{hubs['namespace']}` / `{hubs['eventhub']}` (Microsoft Entra ID)", f"{hubs['published']} published"),
        ('Event Hubs readback', f"consumer group `{hubs['readback_consumer_group']}`",
         f"{hubs['readback_total']} read back, {hubs['readback_unique']} unique, envelopes unchanged"),
        ('Fabric Eventstream source', f"`{fabric['source_name']}` ({fabric['source_type']}), consumer group "
         f"`{fabric['source_consumer_group']}`, start position {fabric['source_start_position']}",
         f"topology verified before publishing"),
        ('Fabric Eventhouse / KQL', f"database `{fabric['kql_database']}`, table `{fabric['table']}` ({fabric['ingestion_mode']}, "
         f"mapping `{fabric['mapping_rule_name']}`)", f"{fabric['rows_total']} rows, {fabric['rows_unique']} unique, envelopes unchanged"),
    ]
    table = '\n'.join(f'| {stage} | {where} | {result} |' for stage, where, result in rows)
    return f"""# MatchLens transport verification evidence

**Status: VERIFIED** · run `{record['run_id']}` · {started.strftime('%Y-%m-%d %H:%M:%S')} → {finished.strftime('%H:%M:%S')} UTC ({(finished - started).total_seconds():.1f} s)

## What this proves

One bounded, live run carried a deterministic synthetic football event batch through
**Azure Event Hubs → Microsoft Fabric Eventstream → Fabric Eventhouse (KQL)**. Every stage used the same
correlation ID (the run ID), and every stored event envelope was compared with the published one.

| Stage | Where | Result |
|---|---|---|
{table}

Event identifiers were identical across the published batch, the Event Hubs readback and the Fabric rows.
The duplicate was delivered and stored, consistent with at-least-once delivery.

## Verified Fabric topology

Checked through the Fabric REST APIs before any event was published:

- Eventstream `{fabric['eventstream_id']}` in workspace `{fabric['workspace_id']}`
- Source `{fabric['source_name']}`: type `{fabric['source_type']}`, connection `{fabric['connection_id']}`
  ({fabric['connection_type']}, credential type {fabric['connection_credential_type']}) →
  `{fabric['connection_endpoint']}` / `{fabric['connection_entity']}`, consumer group `{fabric['source_consumer_group']}`
- Route: the source reaches destination `{fabric['destination_name']}` through the Eventstream graph
- Destination KQL database item `{fabric['kql_database_item_id']}` (`{fabric['kql_database']}`), belonging to
  Eventhouse `{fabric['eventhouse_id']}`, table `{fabric['destination_table']}`

## Limits of this evidence

- **One bounded run, not continuous monitoring.** It shows the path worked at the time above; it does not show
  that the pipeline is running now.
- **Not the public demo's input path.** The public MatchLens demo replays a recorded local file per visitor
  session; it does not read from Event Hubs or Fabric.
- **No AI was involved.** No agent workflow ran and Microsoft Foundry was not called during this run
  (`agents_run: false`, `foundry_called: false`). Foundry runs in the public demo and is evidenced separately.
- At-least-once delivery; no claim of global ordering or exactly-once processing.

## Reproduce

```sh
set -a; . ./.env.transport-verify; set +a      # local, Git-ignored identifiers; Entra ID sign-in, no keys
python -m scripts.transport_verify --dry-run   # validates configuration only
python -m scripts.transport_verify             # publishes one 375-message batch; writes the sanitized record
python -m scripts.transport_report             # validates the record and renders this report
```

Source record: `docs/submission/transport-verification.json`, SHA-256 `{record_sha256}`.
The record contains identifiers, counts and outcomes only: no keys, tokens, connection strings or event payloads.
"""


def render_file(path):
    data = path.read_bytes()
    return render(json.loads(data), record_sha256=hashlib.sha256(data).hexdigest())
