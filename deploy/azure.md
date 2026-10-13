# Azure deployment notes

For the public judge demo (Vercel frontend plus one Container Apps API), use [public-demo.md](public-demo.md). This page covers the Event Hubs/Foundry operator profile.

This is an operator guide for a deployment assembled from existing Azure resources. It does not claim that a public MatchLens deployment is currently running. Resource creation, registry publication and application deployment must be performed and verified in the target subscription.

## Local container smoke test

```sh
docker compose -f deploy/compose.yaml up --build
```

Open `http://127.0.0.1:3001/demo`. The Compose profile runs a local event source and mock agent workflow. It binds to loopback and does not require Azure credentials.

## Cloud configuration

Use existing Container Apps, a container registry, a user-assigned managed identity, Event Hubs and a Foundry project/model deployment. Fabric is a separate optional path described in [fabric-setup.md](fabric-setup.md). Configure private identity and resource IDs in the deployment environment; use Azure secret references for any exceptional secret. Never put tokens, keys or connection strings in this repository.

Build images for the target platform. The frontend API URL is compiled into browser assets and must be the public HTTPS API URL:

```sh
docker build -f Dockerfile -t YOUR_REGISTRY/matchlens-api:TAG .
docker build -f frontend/Dockerfile \
  --build-arg NEXT_PUBLIC_API_BASE_URL=https://YOUR_API_HOST \
  -t YOUR_REGISTRY/matchlens-web:TAG .
```

Start from `azure.env.example`, replace all placeholders with your own deployment values, and set cloud policy explicitly: `APP_ENV=azure`, `EVENT_SOURCE=eventhub`, `AGENTIC_MODE=foundry`, `APP_DEMO_CONTROLS=false`, `APP_DIAGNOSTICS=false`, and `APP_AUTOSTART=true` when the API should consume events on startup. Configure the allowed HTTPS frontend origin and an identity with the minimum required Event Hubs and Foundry permissions.

Use one API worker while replay state, cursor and deduplication state are in memory. A process restart loses that state; multiple replicas do not provide durable checkpoints or exactly-once processing. Keep operational diagnostics behind private ingress or deployment authentication. Configure platform health probes for `/health` and `/ready`; a 200 response alone is not proof that external services are configured or verified.

## Verify integrations separately

The opt-in `scripts/phase7_cloud.py` commands can exercise existing Foundry, Event Hubs and Fabric resources. They may make real model requests and publish/query cloud data. Review their behavior, configure an isolated test destination and authorize the intended operation before running them. Normal local tests do not require cloud access. A combined pipeline claim requires one correlated run that verifies every link; separate service checks do not establish that result.
