# Optional Microsoft Fabric integration

MatchLens can send Event Hubs events to a Fabric Eventstream and Eventhouse. This path requires existing, authorized Azure and Fabric resources. It is separate from the default local mock demo, and this repository does not provision cloud resources or include account-specific IDs or run receipts.

## Configure an existing destination

1. Create or select an Event Hubs source, a Fabric Eventstream, and an Eventhouse/KQL database with the expected `MatchEvents` destination and mapping.
2. Configure the Eventstream connection to read the intended hub and route events to the Eventhouse table. Check the envelope mapping against `fabric-schema.kql` and the event contract under `docs/phase5/`.
3. Set the deployment-specific `FABRIC_WORKSPACE_ID`, `FABRIC_EVENTSTREAM_ID`, `FABRIC_EVENTHOUSE_ID`, `FABRIC_KQL_ENDPOINT` and `FABRIC_KQL_DATABASE` in the operator's private environment. These values are deliberately placeholders in `azure.env.example`.
4. Authenticate as an identity with only the access needed to inspect the existing items and query the destination. Do not put access tokens, keys or connection strings in source control.
5. Publish a fresh synthetic replay through the separately configured Event Hubs path, then query the Eventhouse for that replay's correlated event IDs, duplicate count and invalid-envelope count.

The application reports a historical downstream check only when a valid local verification artifact is available. Traffic is not continuously monitored. A row query alone does not prove Eventstream ingestion; verify the correlated source replay and destination records. Do not describe separately executed Foundry, Event Hubs and Fabric checks as one combined live acceptance.

`fabric-schema.kql` is a reference for the expected fields, not an automatic migration. Review the existing destination schema and mapping before applying any change. This repository contains no private browser receipts, workspace IDs or cloud run identifiers.
