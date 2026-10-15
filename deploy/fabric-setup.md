# Optional Microsoft Fabric integration

MatchLens can send Event Hubs events to a Fabric Eventstream and Eventhouse. This path requires existing, authorized Azure and Fabric resources. It is separate from the default local mock demo, and this repository does not provision cloud resources or include account-specific IDs or run receipts.

## Configure an existing destination

1. Create or select an Event Hubs source, a Fabric Eventstream, and an Eventhouse/KQL database with the expected `MatchEvents` destination and mapping.
2. Configure the Eventstream connection to read the intended hub and route events to the Eventhouse table. Check the envelope mapping against `fabric-schema.kql` and the event contract under `docs/phase5/`.
3. Set the deployment-specific `FABRIC_WORKSPACE_ID`, `FABRIC_EVENTSTREAM_ID`, `FABRIC_EVENTHOUSE_ID`, `FABRIC_KQL_ENDPOINT` and `FABRIC_KQL_DATABASE` in the operator's private environment. These values are deliberately placeholders in `azure.env.example`.
4. Authenticate as an identity with only the access needed to inspect the existing items and query the destination. Do not put access tokens, keys or connection strings in source control.
5. Run the bounded transport verification below, which publishes one correlated synthetic batch and checks it in Event Hubs and the Eventhouse.

## Bounded transport verification

`scripts/transport_verify.py` proves one live run through Event Hubs → Eventstream → Eventhouse. It runs no agents and never calls Foundry.

1. **Checks before writing anything.** It reads the Eventstream definition and its Event Hubs connection through the Fabric REST APIs, and requires all of these to match the configuration:
   - the source namespace, hub and consumer group;
   - the route to the Eventhouse destination;
   - the destination's KQL database, its parent Eventhouse and Query URI;
   - the `MatchEvents` table.

   `AzureEventHub` and `AzureEventHubExtended` sources are accepted.
2. **Publishes** the seed-99 match up to its first goal (374 events) plus one deliberate duplicate, all under a fresh run ID, signing in with Microsoft Entra ID only.
3. **Reads back** on a dedicated consumer group, which must differ from Fabric's.
4. **Queries** `MatchEvents` for the run ID, comparing whole event envelopes at each stage.

Any mismatch, missing stage or timeout fails the run.

Requirements:
- The signed-in identity needs *Azure Event Hubs Data Sender* and *Data Receiver* scoped to the hub.
- That identity also needs Fabric read and write permission on the Eventstream (required by the definition API), access to its connection, and query access to the database.
- Put the identifiers in a Git-ignored `.env.*` file, and leave `AZURE_EVENT_HUB_CONNECTION_STRING` unset.

```sh
set -a; . ./.env.transport-verify; set +a
python -m scripts.transport_verify --dry-run   # configuration only; no network
python -m scripts.transport_verify             # one 375-message batch; writes docs/submission/transport-verification.json
python -m scripts.transport_report             # offline: validates the record, writes docs/submission/transport-verification.md
```

### Full-match reconciliation

`simulator.live --mode azure --fabric-reconcile` publishes the **whole** seed-99 match (1,728 events) with correlation IDs `{run_id}:{sequence}`. It reads the match back on the readback consumer group and processes it with MatchLens agents (mock by default). It verifies the topology before publishing and queries `MatchEvents | where correlation_id startswith "<run_id>:"` afterwards. It then compares four event sets: published, Event Hubs readback, MatchLens processed, and Fabric. Duplicates from retries are reported; missing, extra or altered events fail the run. Connection strings are refused, and so is a readback group equal to `FABRIC_SOURCE_CONSUMER_GROUP`.

```sh
set -a; . ./.env.transport-verify; set +a
python -m simulator.live --match data/generated/phase5/seed99/match.jsonl --mode azure --speed 100 --quiet \
  --fabric-reconcile --record docs/submission/full-match-verification.json   # 1,728 messages; mock agents
python -m scripts.transport_report --record docs/submission/full-match-verification.json \
  --output docs/submission/full-match-verification.md
```

The record and report contain identifiers, counts and outcomes only. They stay in the Git-ignored `docs/submission/` folder. A verified record proves one bounded run at its timestamp, not continuous connectivity. The public demo still replays a recorded local file.

The application reports a historical downstream check only when a valid local verification artifact is available. Traffic is not continuously monitored. A row query alone does not prove Eventstream ingestion; verify the correlated source replay and destination records. Do not describe separately executed Foundry, Event Hubs and Fabric checks as one combined live acceptance.

`fabric-schema.kql` is a reference for the expected fields, not an automatic migration. Review the existing destination schema and mapping before applying any change. This repository contains no private browser receipts, workspace IDs or cloud run identifiers.
