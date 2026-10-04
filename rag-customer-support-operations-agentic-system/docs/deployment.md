# Deployment and operations

> Build update — 4 October 2026: source now contains only the single policy agent.
> This inventory describes the older live deployment and remains for recovery.
> The local customer gateway executes current source with managed state; it does
> not invoke the old deployed model workflow. No cloud resources were removed.
> Legacy action smoke scripts have been deleted; do not follow their commands.

Status checked **1 October 2026**. This runbook describes the existing portfolio
deployment and its boundaries. Documentation edits do not require redeployment.

The product entry point is **[Tarnfield Care customer chat](http://127.0.0.1:3010)**,
currently hosted locally on this computer and connected to the deployed agent.
Follow [the frontend setup guide](frontend.md) to start it. The private
reviewer service below supports internal operations; public customer hosting is
not deployed yet.

## Current service inventory

| Component | Location or address | Verified state and boundary |
|---|---|---|
| Customer chat — product frontend | [Open Tarnfield Care](http://127.0.0.1:3010) | Local Next.js application; not publicly hosted |
| Google project | `gemini-enterprise-learning` / `823305428259` | Existing project for this application's cloud resources |
| Agent Runtime | `projects/823305428259/locations/us-central1/reasoningEngines/3469440221970432000` | Existing runtime updated in place for Arize; a normal frontend chat was verified after rollout |
| Support Operations | [Cloud Run dashboard](https://support-ops-dashboard-tdghggm6ma-uc.a.run.app/ops/) | Private reviewer application, not the customer frontend |
| Cloud Run service | `support-ops-dashboard`, region `us-central1` | Latest ready revision `support-ops-dashboard-00004-nt7`; Ready, ConfigurationsReady and RoutesReady all true |
| Chat gateway | `http://127.0.0.1:8081` | Local server-side bridge to the deployed agent |
| ADK playground | `http://127.0.0.1:8080` | Alternative local interface, started by `make playground` |
| Arize AX | Project `tarnfield-customer-bot` in the configured Arize space | Automatic deployed ADK trace delivery verified; hosted evaluators and alerts not configured |

Agent Runtime is configured with **min instances 0, max instances 10**. A ready
service may scale to zero and incur a cold start. This inventory is a dated
check, not continuous uptime monitoring. Firestore stores deployed support
actions; managed sessions and Memory Bank hold conversation state. The RAG
corpus is resolved by display name rather than a hardcoded corpus ID.

## Open the private Cloud Run dashboard

The canonical service URL reported by Google Cloud is
`https://support-ops-dashboard-tdghggm6ma-uc.a.run.app`; the UI is at `/ops/`.
Anonymous GET `/ops/` returned **403** during verification, as expected for a
private service. Direct browser navigation does not send Cloud Run's required
authentication token, even when the browser is signed into a permitted Google
account. The proxy below supplies the active CLI account's token for browser
requests. This is distinct from a missing IAM permission. A Cloud Run readiness
check does not establish that every authenticated business workflow has just
been retested.

Authenticate the Google Cloud CLI with an account permitted to invoke the
service, then start its authenticated local proxy:

```bash
gcloud auth login
gcloud run services proxy support-ops-dashboard \
  --project=gemini-enterprise-learning --region=us-central1 --port=8090
```

Open `http://127.0.0.1:8090/ops/`. Application reviewer admission must also be
configured. The deployed MVP uses `cloud_run_iam` mode and maps admitted IAM
invokers to **one configured reviewer**. It does not attribute each admitted
person independently. IAP-based identity and a viewer/reviewer/admin permission
design are future launch requirements. Do not make the service public to work
around a 403.

For programmatic calls, Cloud Run requires a permitted identity and an ID token
with the correct service audience. The authenticated proxy is the simplest
manual review path. See [Google's service authentication guide](https://docs.cloud.google.com/run/docs/authenticating/service-to-service).
Google also documents [authenticated browser access through the local proxy](https://docs.cloud.google.com/run/docs/authenticating/developers).

## Run the customer frontend against the current agent

Follow [the frontend setup guide](frontend.md). Model calls use the
Gemini Developer API; cloud retrieval and managed services use Google Application
Default Credentials (ADC). CLI login and ADC login are separate credential
paths.

```bash
gcloud auth application-default login
npm --prefix frontend ci
make chat-gateway
```

Run `make chat-ui` in another terminal and open `http://127.0.0.1:3010`. The
gateway reads ignored `deployment_metadata.json` or the environment variable
`DEPLOYED_AGENT_RUNTIME_ID`. A fresh clone can use the metadata example after
filling the actual resource, or set this variable before starting the gateway:

```bash
export DEPLOYED_AGENT_RUNTIME_ID=projects/823305428259/locations/us-central1/reasoningEngines/3469440221970432000
```

Keep the gateway's cloud credentials and both API keys on the server. A signed
anonymous demonstration identity does not provide verified customer login or
order ownership. Those checks belong in a production identity design before
hosting this frontend publicly.

## Configuration and secrets

| Setting | Local location | Deployed location |
|---|---|---|
| Non-secret project, RAG and Arize settings | `.env`, copied from `.env.example` | Runtime environment configuration |
| `GEMINI_API_KEY` | Ignored `.env.local`, copied from `.env.local.example` | Secret Manager `gemini-api-key:latest` |
| `ARIZE_API_KEY` | Ignored `.env.local` | Secret Manager `arize-api-key:latest` |
| Runtime resource | Ignored deployment metadata or gateway environment | Existing managed runtime resource |

`agents-cli deploy` reads `.env` into deployed environment variables and does
not load `.env.local`. Keep `.env` secret-free. The runtime's service identity
needs access to the referenced secret versions; do not place key values in
command arguments, logs or browser configuration. The Arize rollout retained
both secret bindings and the existing runtime ID.

## Update the existing runtime

First make the change reviewable: run appropriate regression checks, inspect
configuration differences and identify the last known working source/config.
For behavioural changes, use the [evaluation guide](evaluation.md) under a
controlled synthetic-data budget. Preserve the resource ID and managed state.

After deployment approval, the established in-place update path is:

```bash
env -u GOOGLE_APPLICATION_CREDENTIALS agents-cli deploy \
  --project=gemini-enterprise-learning --region=us-central1 --update-only \
  --secrets=GEMINI_API_KEY=gemini-api-key:latest,ARIZE_API_KEY=arize-api-key:latest \
  --no-wait
env -u GOOGLE_APPLICATION_CREDENTIALS agents-cli deploy --status
```

The environment override avoids a stale custom credential-file path; it still
requires working ADC. This command updates the agent, not the separate dashboard
or a public customer UI. Review generated deployment metadata and startup logs
after completion. Verify one synthetic frontend turn and its newly generated
Arize trace, rather than relying only on deployment success or a buffered flush.

Do not recreate the runtime to solve a telemetry issue: that changes its identity
and disrupts the managed-session path. The SDK/CLI deployment and manually
provisioned services already own resources that must be reconciled/imported
before Terraform adoption. A broad `terraform apply` is not a prerequisite for
this in-place update.

## Release checks and recovery

| Check or symptom | Operational response |
|---|---|
| Python/frontend regression | Run the relevant checks listed in [README](../README.md#run-and-verify); use an unused integration-server port |
| Quality change | Generate and grade a controlled synthetic dataset; inspect errors, critical cases and held-out results |
| Agent latency or timeout | Inspect root-trace duration and slow model/tool spans; the gateway defaults to a 120s turn limit and 30s API-operation limit, and the frontend proxy has a 150s timeout |
| Stream stopped or interrupted | Reload the conversation and inspect action status before repeating a consequential request; visible Stop does not guarantee cloud cancellation |
| Missing Arize trace | Check enabled settings, secret permission, project/space, collector diagnostics and ingestion delay using the [observability guide](observability.md) |
| Dashboard 403 | Check the Google account, service invocation permission and application reviewer configuration; keep private access intact |
| Wrong or missing runtime resource | Check gateway environment and ignored deployment metadata against the inventory above |
| Retrieval failure | Inspect ADC, project, region and `make rag-status`; ingestion and project-wide RAG mode changes need deliberate review |
| Pending action inconsistent with reply | Read the Firestore record and audit history; stored state is authoritative and completion must be human-reported |

The live deployed-action smoke makes real cloud calls and synthetic Firestore
writes. Use dedicated synthetic records, verify cleanup and avoid treating it
as a read-only health check. Current checks do not justify resetting customer
sessions, deleting the runtime or removing a corpus as a routine repair.

For agent rollback, retain the previous tested source, lockfile, non-secret
configuration and secret references, then perform an approved in-place update
from that version and repeat verification. There is no automated agent rollback
pipeline configured here. A Cloud Run rollback can route traffic to a known
working existing revision after its compatibility is checked; that is separate
from an Agent Runtime update. Disabling Arize with `ARIZE_ENABLED=false` is a
configuration rollback requiring a runtime update, not a dashboard change.

## Customer launch gaps

Public frontend hosting, verified customer identity/order ownership, independent
reviewer attribution, abuse/rate/quota controls, broader evaluation gates,
measured latency, policy freshness ownership, retention/consent, alerts,
CI/CD and a rehearsed rollback remain open. Readiness of the two deployed
services does not resolve these product requirements.

See [the build plan](build-plan.md), [cost guide](costs.md) and
[engineering notes](engineering-notes.md) for evidence and tradeoffs.
