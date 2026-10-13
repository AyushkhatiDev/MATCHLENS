# Fabric readiness — prepared, no live Fabric connection verified

This is a schema/setup guide. No Fabric workspace, Eventstream or Eventhouse has
been provisioned or connected by this implementation. Azure Event Hubs transport
is implemented and verified offline; live Azure verification is separately opt-in.

The intended future path is Azure Event Hubs → Fabric Eventstream → Eventhouse
KQL database → real-time queries. Eventstream can ingest and transform Event Hubs
events and route them into an Eventhouse. See Microsoft's
[Eventstreams documentation](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/overview)
and [Eventhouse overview](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/eventhouse).

## Setup in an existing authorized workspace

1. Create an Eventhouse with a KQL database in a Fabric workspace with the required
   Real-Time Intelligence capacity and permissions.
2. Create a `MatchEvents` table using the KQL below.
3. Create an Eventstream. Add an Azure Event Hubs source with the existing namespace,
   hub, and a **dedicated Fabric consumer group**, separate from the Python demo.
   Configure a supported connection/authentication option in Fabric's connection UI.
   Keep credentials in that connection, never in these example files.
4. Select JSON input. Add transformations to project `team_id=payload.team_id`,
   `player_id=payload.player_id` and `timestamp=simulation_timestamp`; retain the
   entire original `payload` as dynamic JSON and retain the envelope headers.
5. Add the KQL database/Eventhouse destination and map columns to `MatchEvents`.
   If using direct JSON ingestion, use the mapping below instead of flattening.
6. Publish the Eventstream and send an explicitly authorized test replay. Inspect
   rows in Eventhouse, confirm counts/IDs/timestamps against the source, and record
   actual success before describing the integration as connected.

Fabric source setup reference:
[Azure Event Hubs source](https://learn.microsoft.com/en-us/fabric/real-time-intelligence/event-streams/add-source-azure-event-hubs).
Check workspace capacity, network access and the current UI prerequisites there.

## Table and direct JSON ingestion mapping

```kql
.create table MatchEvents (
    event_id:string,
    match_id:string,
    event_type:string,
    simulation_timestamp:real,
    produced_at:datetime,
    sequence_number:long,
    team_id:string,
    player_id:string,
    timestamp:real,
    source:string,
    schema_version:string,
    correlation_id:string,
    payload:dynamic
)

.create table MatchEvents ingestion json mapping 'MatchEnvelopeV1' '[
  {"column":"event_id","path":"$.event_id","datatype":"string"},
  {"column":"match_id","path":"$.match_id","datatype":"string"},
  {"column":"event_type","path":"$.event_type","datatype":"string"},
  {"column":"simulation_timestamp","path":"$.simulation_timestamp","datatype":"real"},
  {"column":"produced_at","path":"$.produced_at","datatype":"datetime"},
  {"column":"sequence_number","path":"$.sequence_number","datatype":"long"},
  {"column":"team_id","path":"$.payload.team_id","datatype":"string"},
  {"column":"player_id","path":"$.payload.player_id","datatype":"string"},
  {"column":"timestamp","path":"$.simulation_timestamp","datatype":"real"},
  {"column":"source","path":"$.source","datatype":"string"},
  {"column":"schema_version","path":"$.schema_version","datatype":"string"},
  {"column":"correlation_id","path":"$.correlation_id","datatype":"string"},
  {"column":"payload","path":"$.payload","datatype":"dynamic"}
]'
```

`timestamp` is a compatibility alias for **simulation seconds**, not a wall-clock
datetime. `produced_at` is UTC wall time. Lifecycle events can have null team/player
IDs. Preserve numeric fractional simulation seconds; do not convert to ingestion
wall time. `payload.timestamp` remains the unchanged Phase 1 simulation timestamp.

Example query (read-time dedup; delivery is at least once):

```kql
MatchEvents
| where schema_version == "1.0"
| summarize arg_max(produced_at, *) by match_id, event_id, sequence_number
| order by match_id asc, simulation_timestamp asc, sequence_number asc
```

Schema version `1.0` is the only Python-supported version. Unsupported versions
are rejected and counted before intelligence state changes. Introduce an explicit
schema migration and update Fabric mappings before publishing another version.
Fabric's table itself does not enforce envelope compatibility or the processor's
identity-conflict policy; configure routing for unsupported versions separately.

The accompanying `event_envelope.schema.json` contains typed Phase 1 alternatives.
`event_envelope.example.json` is an actual serialized seed-99 synthetic event.
