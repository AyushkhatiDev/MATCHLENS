# Public judge demo: Vercel frontend + Azure Container Apps API

This guide deploys the browser demo for judges. It does not create resources by itself, and nothing in this repository claims a deployment is running. Every command that creates or changes Azure or Vercel resources may bill your account; review it before running it.

## What runs where

| Part | Platform | Source | Listens on |
|---|---|---|---|
| Frontend (Next.js 16, App Router) | Vercel | `frontend/` | Vercel-managed |
| API (FastAPI + uvicorn, `simulator.production.public_api:app`) | Azure Container Apps | `Dockerfile` at the repo root | `0.0.0.0:8000` (set ingress target port to `8000`) |

The API image contains only the tracked seed-99 synthetic match (`data/generated/phase5/seed99/`). Each browser gets its own replay session, created by `POST /api/sessions` and owned through an HttpOnly cookie. All requests for that session go under `/sessions/{id}/…`.

**Everything is held in memory**: sessions, their owner cookies, replay state and the per-hour session budget. There is no database and no shared store. Consequences:

- Run **exactly one replica** (`--min-replicas 1 --max-replicas 1`). A second replica would not recognise another replica's sessions, and judges would get `403`/`404` at random.
- A restart, redeploy or scale event drops all sessions. Browsers then show "Demo session expired… Retry connection", and Retry starts a fresh session.
- Keep the image's single uvicorn worker (`--workers 1`). Don't add workers.

Built-in limits (in `simulator/production/public_api.py`):
- At most 8 concurrent sessions; the 9th gets `503 Demo capacity reached`.
- At most 30 new sessions per hour across all visitors (`429`).
- A replay restart at most once per 60 s per session.
- Idle sessions expire after 600 s.
- Each event stream closes after `APP_SSE_SECONDS` (180 s), and the browser's `EventSource` reconnects automatically from `Last-Event-ID`.
- At most 2 concurrent Foundry preparations.

Measured locally (linux/amd64 image, mock mode): about 61 MiB idle and about 283 MiB with all 8 sessions replaying.

## Choose how the browser reaches the API

The session cookie must reach the API on every request. Vercel (`*.vercel.app`) and Container Apps (`*.azurecontainerapps.io`) are different sites, so a cookie set directly by the API is a **third-party cookie**. Safari, and Chrome/Edge private windows, block third-party cookies by default, so a direct setup fails in exactly the private-window check judges are likely to do.

**Recommended: proxy mode.** Vercel forwards `/api/*` and `/sessions/*` to the API. The browser only talks to the Vercel origin, so the cookie is first-party. It is enabled by setting `MATCHLENS_API_ORIGIN` at build time (see `frontend/next.config.ts`). Vercel's proxy waits up to 120 s for the first byte, and then streaming may continue as long as data arrives at least every 120 s. The API sends a heartbeat every 15 s.

**Alternative: direct mode.** Leave `MATCHLENS_API_ORIGIN` unset and point `NEXT_PUBLIC_API_BASE_URL` at the API. This works only in browsers that allow third-party cookies, unless you put both apps on custom domains under one registrable domain (for example `demo.example.com` and `api.example.com`).

Proxy mode was verified locally with the Next.js production server (see "Verification record"). It has not yet been verified on Vercel's own proxy, so run the smoke test after deploying.

## Environment variables

### Azure Container Apps (API)

Start from [`public-demo.env.example`](public-demo.env.example).

| Variable | Value | Secret? |
|---|---|---|
| `APP_PUBLIC_DEMO` | `true`, which selects the isolated public session gateway | no |
| `APP_FRONTEND_ORIGINS` | Exactly one HTTPS origin, the Vercel **production** URL, e.g. `https://matchlens.vercel.app`. No path or trailing slash. | no |
| `AGENTIC_MODE` | `mock` (default, no paid AI) or `foundry` | no |
| `FORWARDED_ALLOW_IPS` | `*`. Required so uvicorn trusts the ingress's `X-Forwarded-Proto` and issues the cookie `Secure; SameSite=None`. Without it the cookie is `SameSite=Lax` and direct mode fails. Only the ingress can reach the container port. | no |
| `LOG_LEVEL` | `INFO` | no |
| `APP_SSE_SECONDS` | `180` (allowed 15–210; must stay below the 240 s ingress request timeout) | no |
| `APP_MAX_SSE_SUBSCRIBERS` | `100` (per session) | no |
| `FOUNDRY_PROJECT_ENDPOINT` | Foundry mode only. Use a Container Apps secret plus `secretref:`. | private config |
| `FOUNDRY_MODEL` | Foundry mode only, the deployment name. Use a secret plus `secretref:`. | private config |
| `AZURE_CLIENT_ID` | Foundry mode only, the client ID of the user-assigned managed identity | no (an identifier) |
| `AGENTIC_TIMEOUT_SECONDS` | Optional, default `60` (range up to 300) | no |

The image already sets `APP_ENV=demo`, `APP_DEMO_CONTROLS=true` and `APP_DIAGNOSTICS=false`. The public gateway forces local recorded replay, so `EVENT_SOURCE`, Event Hubs and Fabric settings have no effect in this deployment. Do not set `FOUNDRY_AGENT_ID`. No API keys, connection strings or database passwords are used. Foundry authentication uses the managed identity through `DefaultAzureCredential`.

### Vercel (frontend)

All three variables are read **at build time** and compiled into the bundle, so redeploy after changing any of them. None of them may hold a secret.

| Variable | Proxy mode (recommended) | Direct mode |
|---|---|---|
| `NEXT_PUBLIC_DEMO_SESSIONS` | `true` | `true` |
| `NEXT_PUBLIC_API_BASE_URL` | The Vercel production URL, e.g. `https://matchlens.vercel.app` | The API URL, e.g. `https://matchlens-api.<env>.<region>.azurecontainerapps.io` |
| `MATCHLENS_API_ORIGIN` | The API URL (server-side only, not exposed to the browser) | unset |

Because the API accepts exactly one frontend origin, only the production URL works. Preview deployments get `403`; that is expected.

## Deploy the API to Azure Container Apps

Prerequisites, all existing or created by you deliberately:
- a resource group
- a Container Apps environment
- an Azure Container Registry
- a user-assigned managed identity with `AcrPull` on the registry

Replace every `<…>` value.

```sh
RG=<resource-group> ENVNAME=<containerapps-environment> ACR=<registry-name> APP=matchlens-api
IDENTITY_ID=<user-assigned-identity-resource-id> TAG=$(git rev-parse --short HEAD)
FRONTEND=https://<your-project>.vercel.app

# Build for linux/amd64 in the registry (ACR task time is billed).
az acr build --registry "$ACR" --image matchlens-api:$TAG --platform linux/amd64 --file Dockerfile .

az containerapp create -g "$RG" -n "$APP" --environment "$ENVNAME" \
  --image "$ACR.azurecr.io/matchlens-api:$TAG" \
  --registry-server "$ACR.azurecr.io" --registry-identity "$IDENTITY_ID" --user-assigned "$IDENTITY_ID" \
  --ingress external --target-port 8000 \
  --min-replicas 1 --max-replicas 1 --cpu 0.5 --memory 1.0Gi \
  --env-vars APP_PUBLIC_DEMO=true AGENTIC_MODE=mock "APP_FRONTEND_ORIGINS=$FRONTEND" \
             "FORWARDED_ALLOW_IPS=*" LOG_LEVEL=INFO APP_SSE_SECONDS=180

az containerapp show -g "$RG" -n "$APP" --query properties.configuration.ingress.fqdn -o tsv
```

Health probes: in the portal, open the app, go to **Containers → Health probes**, and set:
- liveness: HTTP GET `/health`, port `8000`
- readiness: HTTP GET `/ready`, port `8000`

Both return 200 without contacting Foundry, Event Hubs or Fabric. On shutdown, uvicorn closes all sessions and their replay tasks, allowing up to 5 s (`--timeout-graceful-shutdown 5`).

Optional live Foundry mode (billed model requests on every new session's hero moment):
1. Grant the managed identity access to the Foundry project (for example the *Azure AI User* role).
2. Run:

```sh
az containerapp secret set -g "$RG" -n "$APP" --secrets foundry-endpoint=<endpoint> foundry-model=<deployment>
az containerapp update -g "$RG" -n "$APP" --set-env-vars AGENTIC_MODE=foundry \
  FOUNDRY_PROJECT_ENDPOINT=secretref:foundry-endpoint FOUNDRY_MODEL=secretref:foundry-model \
  AZURE_CLIENT_ID=<identity-client-id>
```

Use the shell's history settings or a file to avoid leaving the endpoint in your terminal history.

## Deploy the frontend to Vercel

1. In Vercel, choose **Add New → Project**, import `AyushkhatiDev/MATCHLENS`, and set:
   - Framework Preset: **Next.js**
   - Root Directory: **`frontend`**
   - Install Command: `npm ci`
   - Build Command: `npm run build` (runs `next build --webpack`)
   - Output Directory: default
   - Node.js Version: 24.x (matches CI)
2. Add the environment variables from the table above to **Production**.
3. Deploy, then confirm the production URL matches `APP_FRONTEND_ORIGINS` exactly. If it differs (for example after adding a custom domain), update the API with `az containerapp update -g "$RG" -n "$APP" --set-env-vars APP_FRONTEND_ORIGINS=https://<new-origin>`, and update `NEXT_PUBLIC_API_BASE_URL` too in proxy mode.
4. Make sure Vercel Deployment Protection does not apply to the production domain, so judges don't need a Vercel login.

## Smoke test

`/health` makes no outbound calls:

```sh
curl -s https://<api-fqdn>/health
```

The full flow below goes through the browser-facing origin. It covers session, cookie ownership, replay, ordered streaming, moments, evidence, Story Stress Test (checking that score and state are unchanged), and close:

```sh
python scripts/deployment_smoke.py https://<your-project>.vercel.app --api https://<api-fqdn>
```

The script refuses to run against a Foundry-mode API (to avoid billed inference) unless you pass `--allow-foundry`. It prints no cookies or secrets.

## Check in a private browser window

Use a Safari private window or a Chrome Incognito window, which have the strictest cookie rules:

1. Open `https://<your-project>.vercel.app/demo`. The match loads and the connection indicator turns connected.
2. Start the replay. Events, moments and checkpoints appear in order.
3. Open a moment's evidence.
4. Run Story Stress Test. The score and canonical timeline do not change.
5. Check the runtime labels. In mock mode they read **MOCK** / "Offline mock interpretation", never "FOUNDRY LIVE". In Foundry mode, only a completed, grounded run shows **FOUNDRY LIVE**; failures show **FOUNDRY UNAVAILABLE** or **FOUNDRY RESULT REJECTED**.
6. Open a second private window. It gets its own independent session.

## Logs and rollback

```sh
az containerapp logs show -g "$RG" -n "$APP" --follow          # application logs
az containerapp revision list -g "$RG" -n "$APP" -o table       # revisions and their images
az containerapp update -g "$RG" -n "$APP" --image "$ACR.azurecr.io/matchlens-api:<previous-tag>"   # roll back
```

The API is built without access logs (`--no-access-log`). On Vercel, roll back from **Deployments → (previous deployment) → Promote to Production** (or **Instant Rollback**). Rolling back either side does not need the other to change, as long as the origins stay the same.

## Integration status reporting

- **Foundry:** `/health` reports `MOCK` in mock mode. In Foundry mode it reports `FOUNDRY UNAVAILABLE` until a session's hero moment completes; `verified: true` appears only after a genuine `FOUNDRY LIVE` result. The endpoint itself never calls Foundry.
- **Event Hubs:** the public demo always replays the recorded local file. `/health` reports status `UNVERIFIED`, `continuous_connection: false` and the current input as "recorded local replay".
- **Fabric:** reported as `UNVERIFIED` unless a valid verification record is present, and the image does not include one.

## Cost notes

- **Container Apps, consumption plan:** the first 180,000 vCPU-seconds, 360,000 GiB-seconds and 2 million requests per subscription per month are free. One always-on 0.5 vCPU / 1 GiB replica uses about 1.3 million vCPU-seconds and 2.6 million GiB-seconds per month, so it exceeds the free grant; minimum-capacity replicas are billed at the reduced idle rate when not serving requests. Price it in the Azure pricing calculator for your region. `--min-replicas 0` lowers cost but adds cold starts.
- **Container Registry:** the Basic tier plus ACR build minutes.
- **Foundry mode only:** model tokens for each new session's hero moment. This is bounded by the gateway limits (at most 30 new sessions per hour, 2 concurrent preparations, one restart per minute per session), but it is not zero.
- **Vercel:** the Hobby tier is for non-commercial use; check Vercel's terms for your situation.

## Verification record

Checked locally for this guide, without Azure or Vercel:
- the linux/amd64 API image built
- under `--read-only --cap-drop ALL`, the image reported `MOCK` and Fabric `UNVERIFIED`
- the cookie was `Secure; SameSite=None` only when `FORWARDED_ALLOW_IPS=*` and `X-Forwarded-Proto: https`
- the 9th concurrent session got `503`
- the production frontend build (`MATCHLENS_API_ORIGIN` set) served through `next start` passed `scripts/deployment_smoke.py` end to end, including incremental, ordered streaming through the proxy

Not verified: the Vercel CDN proxy, the Container Apps ingress, live Foundry from Container Apps, Event Hubs and Fabric.
