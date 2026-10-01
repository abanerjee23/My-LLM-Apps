# Automatic Arize AX tracing

The application uses Arize AX, the hosted Arize AI product, with the supported
OpenInference Google ADK instrumentor. Normal ADK execution generates the trace
tree: root and specialist agent runs, model requests, tool calls, timings, token
counts and errors. Application code does not construct traces or duplicate each
tool's logging. Galileo dependencies and local settings have been removed.

## Product and operational purpose

Trace the work behind a customer answer so a failed turn can be attributed to
routing, retrieval, model latency, delivery or action persistence. Follow a slow
model/tool span into its parent turn; use Firestore and its audit history to
establish whether a support request was actually persisted or reviewed.

| Evidence source | What it establishes | Limit |
|---|---|---|
| Arize AX automatic traces | Agent/model/tool execution, timing, usage and sanitized error metadata | An OK trace does not prove the answer was correct |
| Cloud Logging/Trace | Deployment, serving and native cloud diagnostics | Native content capture is separately configured |
| Firestore action/audit records | Stored request state and reviewer-reported changes | No payment or fulfilment integration executes the action |
| Local evaluation reports | Explicit case outputs and custom-judge grades | Two development cases are not broad quality coverage |

This integration uses hosted **Arize AX**, not a Phoenix collector. Hosted Arize
evaluators, experiments, score uploads, online quality jobs and alert thresholds
are not configured. The custom Gemini judge remains outside the ADK instrumentor;
its grading calls and scores are not automatically exported. See
[evaluation and trace correlation](evaluation.md#relationship-to-automatic-arize-ax-traces)
and [cost accounting](costs.md).

## Configuration

Keep these non-secret settings in `.env`:

```dotenv
ARIZE_ENABLED=true
ARIZE_SPACE_ID=<space ID from Arize settings>
ARIZE_PROJECT_NAME=tarnfield-customer-bot
ARIZE_CAPTURE_CONTENT=false
```

Keep the Arize API key in the ignored `.env.local`:

```dotenv
ARIZE_API_KEY=<your Arize API key>
```

The project name groups this assistant's traces. The space ID must match the
space that the key can access. The default collector is
`https://otlp.arize.com/v1/traces`. The key stays server-side and must not be
placed in browser configuration, committed files or deployment-facing `.env`.
`agents-cli deploy` converts `.env` entries into runtime environment variables;
it does not load `.env.local`.

`ARIZE_ENABLED=false` disables Arize export. Enabling it with missing credentials
or space configuration fails startup visibly instead of silently losing traces.

## Automatic instrumentation and data capture

Startup attaches a buffered Arize exporter to the existing OpenTelemetry provider
and instruments Google ADK once per process. The HTTP entry point initializes
Google Cloud telemetry before attaching Arize. CLI and SDK imports also initialize
the integration, so tracing is not limited to a special smoke script.

The exporter runs in the background, with a bounded network timeout and a bounded
flush during graceful shutdown. It keeps Arize's project resource separate from
the Google exporter. Only spans from the OpenInference ADK instrumentor are sent
to Arize, avoiding duplicate model usage from native Google GenAI spans.
Export outages do not make every customer turn wait for the
collector; exporter warnings still need operational monitoring.

Span durations include child work where the framework keeps a parent operation
open. Do not sum parent and child durations to estimate total latency. The root
trace duration measures the whole turn, and the tree identifies slow substeps.

Prompt text, model replies, tool arguments/results and model invocation payloads
are hidden by default. The export boundary also removes exception messages,
stack traces and status descriptions that could contain customer data. Span names,
parent relationships, timing, model identifiers, token counts, error types and
status codes remain available. `ARIZE_CAPTURE_CONTENT=true` opts into content
collection for OpenInference spans on the shared provider, affecting both Arize
and Google Cloud trace exports. Native Google telemetry remains under its own
capture configuration. Google's native `NO_CONTENT` setting does not redact
OpenInference content independently.

## Verify delivery

```bash
uv sync --locked
uv run python scripts/arize_trace_smoke.py
```

The default mode exercises deterministic ADK execution and real exporter
acceptance without a model bill; it is a transport and instrumentation check,
not an agent-quality evaluation. Add `--real` only for a live-model check. The
real mode runs one unchanged root-agent final-sale question against the live
Gemini and RAG clients using an isolated local session. It spends model credits.
The check reports automatically generated AGENT, LLM and TOOL spans and actual
OTLP exporter acceptance. A flush returning successfully alone is not proof of
collector acceptance.

In Arize, open the configured space, select `tarnfield-customer-bot`, and look for
the smoke run's trace ID. Expand the trace to verify agent, model and tool spans.
Allow for ingestion delay. A locally visible trace proves local instrumentation;
a new chat through the local frontend against the deployed agent must be checked
separately after rollout. The customer frontend is not currently publicly hosted;
the private Cloud Run link belongs to the reviewer dashboard.

Verification on 1 October 2026: the live model/RAG smoke completed in 19.294
seconds. Its trace `0ed1a33c31cb40b27ef0f441abbf1cc1` contained ten automatic
spans: two chains, two agents, four model calls and two tools. All ten spans were
accepted and the trace tree was confirmed in the Arize dashboard. Input/output
content appeared as redacted.
This is evidence of delivery and one successful turn, not a p95 latency result.

The local frontend's connection to the deployed agent was verified after the
export correction on the same day.
Managed session `3156356794222116864` produced trace
`ee18fb93d34d639e485828b08573974e`, confirmed in the Arize dashboard with status
OK and ten automatic spans: two chains, two agents, four model calls and two
tools. The root trace took 35.14 seconds and reported 11,424 tokens without
duplicate native model spans. Model and tool content was masked. The assistant
returned the sourced final-sale explanation and filed no support request.
The migration establishes tracing; this sample still leaves latency improvement
as a separate product priority.

Final automated verification passed 147 Python checks, with one opt-in live check
skipped, and Ruff. The focused tracing suite covers 58 checks, including duplicate
native spans, exporter acceptance and privacy failures involving error events,
status descriptions and content payloads. The smoke script inspects actual
outbound SDK copies and rejects unsafe batches before transport.

After the correction, the default deterministic transport smoke also accepted
five automatic spans and passed its privacy checks. Its fixture and span count
differ from the ten-span live turn; it does not constitute another quality grade.

## Google Cloud rollout

Store the Arize key in a Secret Manager secret named `arize-api-key`. Grant the
existing agent's runtime identity access to that secret. Preserve its Gemini key
binding when updating the deployment:

```bash
env -u GOOGLE_APPLICATION_CREDENTIALS agents-cli deploy \
  --project=gemini-enterprise-learning --region=us-central1 --update-only \
  --secrets=GEMINI_API_KEY=gemini-api-key:latest,ARIZE_API_KEY=arize-api-key:latest
```

Set the non-secret Arize configuration in `.env` before deploying. Update the
existing runtime in place to preserve managed sessions. Obtain deployment
approval after local verification. Confirm startup logs, send a real frontend
chat, and verify that its trace appears in Arize. Cloud Logging/Trace remains the
source for deployment and infrastructure diagnostics.

The rollout updated the existing runtime
`projects/823305428259/locations/us-central1/reasoningEngines/3469440221970432000`
in place. Both Gemini and Arize secret bindings were retained, and the existing
zero-to-ten-instance scaling settings were preserved.

Rollback uses the previous runtime build or `ARIZE_ENABLED=false` in an approved
runtime configuration update. This migration does not change model choices,
prompts, policy rules or support-action behavior. Trace ingestion is separate from
evaluation setup, monitors and alert thresholds.

Current service links, private dashboard access and recovery boundaries are in
[the deployment runbook](deployment.md). The latest Cloud Run service is
[Support Operations](https://support-ops-dashboard-tdghggm6ma-uc.a.run.app/ops/);
an anonymous 403 is expected and is separate from agent tracing health.

## Troubleshooting and monitoring gaps

| Symptom | Check |
|---|---|
| Startup rejects Arize configuration | Validate enabled flag, non-empty space/project and supported AX endpoint; confirm the key exists without printing it |
| No trace after a turn | Check collector acceptance diagnostics, key/space access, selected Arize project, ingestion delay and runtime startup logs |
| Cloud trace exists but Arize trace is missing | Check Arize export initialization/configuration; native Cloud spans alone do not establish AX delivery |
| Duplicate token or cost totals | Count OpenInference LLM spans once; confirm only `openinference.instrumentation.google_adk` reaches Arize |
| Long customer turn | Read root duration and slow model/tool spans; do not add inclusive parent/child durations |
| A model response appears safe but a request is missing | Inspect persistence result and Firestore audit records; a fluent reply is not storage proof |
| Content appears in an export | Keep capture off, inspect outbound sanitized copies and error/event/status/link fields; review native Google capture independently |
| Exporter fails during customer traffic | Inspect warnings and acceptance counters; buffered export avoids per-turn collector waits, but delivery can still fail |

The helper exposes process-local attempted/successful/failed export diagnostics.
These are not a persistent monitoring dashboard or an alert policy. Trace
delivery is buffered and is not a guarantee of lossless collection after an
abrupt process failure.

Before public traffic, assign owners and thresholds for export loss, model/API
errors, latency, token/cost changes, retrieval failure, action-write failure,
overdue review and policy freshness. Establish retention, access, consent and
deletion procedures in both Arize and native Cloud telemetry. Those controls
remain open; the migration did not configure them automatically.

Keep local JSON/HTML evaluation reports out of shared customer-data workflows:
they contain prompts, responses and tool evidence and are not masked by Arize's
export boundary. Use synthetic orders/contact details when verifying content
handling. Metadata-only traces do not supply the answer content needed for
content-based hosted evaluations.

## References

- [ADK's official Arize AX integration](https://adk.dev/integrations/arize-ax/)
- [Arize's Google ADK tracing guide](https://arize.com/docs/ax/integrations/python-agent-frameworks/google-adk/google-adk-tracing)
