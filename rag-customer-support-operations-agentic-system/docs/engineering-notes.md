# Engineering Notes

> Historical implementation evidence for the legacy support product. The
> agreed replacement and its gates are in [the build plan](build-plan.md).

**Last verified:** 1 October 2026.

[BUILD_PLAN.md](build-plan.md) defines user value, measured evidence, proposed gates and
remaining decisions. This file records implementation boundaries and the failures that informed
them. The current system includes the deployed Agent Runtime, private Cloud Run reviewer
dashboard, and a local Next.js customer chat on port 3010 through a gateway on port 8081.
There is no public customer frontend or commerce execution connector.

---

## 1. ADK specifics that are easy to get wrong

### Automatic Arize AX tracing

`app/app_utils/observability.py` instruments ADK with OpenInference and exports
through the Arize SDK's HTTP OTLP exporter. Normal root/specialist runs generate
their own agent, model and tool spans; application code does not create spans
manually. The HTTP lifespan initializes this after ADK's Google Cloud telemetry
bootstrap. `app/agent.py` initializes it for CLI and SDK imports as well.

Reuse the existing global tracer provider. Replacing it with a second provider
would drop the existing Cloud processors. Arize receives exported span copies
with its project resource; the original resources stay available to Cloud.
OpenInference content masking also covers tool parameters and ADK legacy payload
attributes, because Google's native `NO_CONTENT` flag does not mask OpenInference data.

Deployed verification exposed a second instrumentation layer: native Google
GenAI spans duplicated ADK model usage and bypassed the OpenInference masking
configuration. The Arize exporter therefore accepts only the ADK OpenInference
instrumentation scope, `openinference.instrumentation.google_adk`, and requires an
OpenInference span kind. A native span is omitted from Arize even if it declares an
LLM kind. The export boundary sanitizes copied attributes, links, exception events
and status descriptions when `ARIZE_CAPTURE_CONTENT=false`. Operational names,
timing, status/error types and token usage remain. Google Cloud receives the original
spans under its separate native telemetry capture configuration. This is an Arize
privacy boundary, not proof that all Google-native spans are content-free.

The locked integration uses Google ADK 2.6.2, OpenInference Google ADK 0.1.28 and
`arize-otel` 0.14.1. The OpenInference dependency is bounded below 1.0 for compatibility
with this ADK version. The Arize SDK HTTP exporter targets the AX collector; Phoenix
is not the destination. No second provider or manually constructed trace is needed.

Exports are buffered. Delivery counters distinguish collector acceptance from
a merely drained queue. The installed OpenTelemetry 1.42 batch processor ignores
its `force_flush` timeout argument, so the helper bounds the caller's wait and
reuses an unfinished flush rather than creating repeated flush workers.
Close runners before the final flush. The exporter has a five-second network timeout;
the batch queue holds 512 spans, exports at most 128 per batch and schedules every
second. The default flush wait is five seconds. These bounds limit collector impact;
they do not guarantee delivery after an abrupt process exit or a full queue.

`setup_arize_observability()` is idempotent. `flush_arize_observability()` bounds the
caller wait; `get_arize_export_diagnostics()` reports attempted/successful/failed
batches and spans without payloads or credentials. Success counters reflect actual
`SpanExportResult.SUCCESS`, not just queue draining. `shutdown_arize_observability()`
flushes and closes Arize export without shutting down the shared Cloud provider. Setup
after shutdown requires a process restart; it is not a dynamic reconfiguration API.

Keep the key in `.env.local` and bind Secret Manager in the deployed runtime;
`.env` is deployment-facing configuration. Enabled but missing/invalid Arize settings
fail startup clearly. `ARIZE_CAPTURE_CONTENT=true` is an explicit opt-in for
OpenInference payload capture on the shared provider, affecting both export
destinations. Native Google telemetry remains independently configured.

Latest deployed verification on 1 October 2026: a normal frontend final-sale question
produced trace `ee18fb93d34d639e485828b08573974e`, managed session
`3156356794222116864`. Arize showed status OK, ten automatic spans, a 35.14-second
root duration and 11,424 tokens. Model/tool content was masked and native usage was
not duplicated. An earlier local live smoke took 19.294 seconds and all ten spans
were accepted. These are individual delivery samples, not a p95 latency claim.
See [configuration, verification and rollout](observability.md).

### Explicit specialist AgentTools (BUILD_PLAN §4)

Returns and Billing are real `LlmAgent` instances wrapped as tools on the root. The root calls
one, receives its grounded result, and then produces the customer response.

ADK 2.6 recommends attaching `mode='single_turn'` agents through `sub_agents`. That worked in the
internal Agent Runtime stream, but the deployed A2A surface completed without emitting the final
assistant message. The explicit `AgentTool` boundary is therefore a deliberate compatibility
choice backed by a live test, not accidental use of an older pattern. It also makes the product
contract obvious: specialists return evidence to the root; they do not own the customer voice.

Wrapped specialists use `mode='chat'`. `AgentTool` creates a nested runner and ADK rejects any
other mode for that nested root; this was caught from the deployed A2A error log and is asserted
structurally in the test suite.

The A2A route currently opts into ADK's legacy event converter. With the new 2.6 executor, the
correct final root message was present in the managed-session event log but absent from the
stream consumed by `agents-cli run --mode a2a`. The legacy converter publishes that same final
message as an A2A artifact. This is a compatibility pin with a live regression test, not a
permanent preference; remove it when the new executor and client interoperate.

The unit test asserts that exactly the Returns and Billing agents are wrapped and that the root
has no transfer-based sub-agents. The deployed A2A smoke is the behavioural proof.

### Callback parameter naming (BUILD_PLAN §5)

`app/callbacks.py`:

```python
async def save_conversation_to_memory(callback_context: Context) -> None:
    try:
        await callback_context.add_session_to_memory()
    except ValueError:
        logger.debug("No memory service available; skipping memory write.")
```

**The parameter must be named `callback_context`.** ADK invokes after-agent callbacks by keyword
(`base_agent.py:556`), and the docstring example showing `ctx` fails at runtime. Caught by an
integration test, not the unit tests.

### `preload_memory_tool` on the root, not `load_memory` on specialists

`load_memory` attached to specialists went unused. The prompt listed "it's me again" as a
trigger, and a second conversation opening with exactly those words produced no memory call.

The cue reaches the **root** — it sees the customer's words. The specialist receives a delegated
task, not the customer's phrasing. So the root carries `preload_memory_tool`, which runs on every
request without relying on the model to remember it. The preload still incurs memory-service
retrieval latency and usage, while avoiding a separate model decision to invoke memory.

**Specialists have since had `load_memory_tool` removed entirely.** Unused wiring plus
instructions describing behaviour that never happened is worse than neither. The deciding
argument is the authority boundary: the specialist is the only agent that issues a ruling, and
BUILD_PLAN §5 says memory may never decide one. Removing the tool makes that structural instead of
instructed.

Asserted both ways in `tests/unit/test_architecture.py`: the root must have `preload_memory`, and
neither specialist may have any memory tool.

### Optional state placeholders

Root instruction uses `{user:contact_method?}` / `{user:contact_detail?}`. The `?` suffix renders
an unset value as blank instead of raising.

---

## 2. Retrieval

`app/retrieval.py`. Uses the `agentplatform` client — `vertexai.rag` is deprecated, and
`agentplatform` is the only client that can express serverless mode.

### Two sentinel return values, never an exception

```python
CORPUS_UNAVAILABLE = "POLICY_LOOKUP_UNAVAILABLE: ..."
NO_MATCH           = "..."
```

A retrieval failure is a product event the specialist must report honestly, not a stack trace.
Both `except` blocks now call `logger.exception` as well — see §5.

### Scope assertion (BUILD_PLAN §6)

Each specialist's search drops chunks whose source document it does not own. One corpus, two
scoped tools. Verified: the same refund query returns only billing chunks under billing scope,
and those chunks are discarded under returns scope.

### The distance threshold is deliberately loose

`MAX_DISTANCE = float(os.getenv("RAG_MAX_DISTANCE", "0.60"))`.

Measured distances against this corpus (lower = closer):

| Question | Covered? | Best distance |
|---|---|---|
| "When will my refund arrive?" | yes | 0.369 |
| "Is the Solstice Edition returnable?" | yes | 0.411 |
| "Do you accept Klarna?" | **no** | 0.431 |
| "Is there a student discount?" | **no** | 0.505 |
| nonsense control | no | 0.624 |

Covered and uncovered **overlap**. A threshold separating 0.411 from 0.431 would be fitted to
five queries, not a mechanism. So 0.60 removes only obvious junk, and results carry
`_RELEVANCE_WARNING` telling the model these are the *closest* passages, not an answer.

**Consequence, and it is a product-level one:** refusal correctness rests entirely on the model.
Recorded in BUILD_PLAN §§6 and 10 for that reason.

### Corpus resolution

Resolved by display name (`RAG_CORPUS_DISPLAY_NAME`), never by ID — the ID changes on every
`make rag-up`. `reset_cache()` clears the memoised corpus name and client, used by tests and
after a re-ingest.

---

## 3. Services factory

`app/app_utils/services.py` extends the scaffold's existing factory. ADK web, A2A and the
runtime adapter resolve shared process-wide services rather than separate histories.

Session service resolution order:

1. `SESSION_SERVICE_URI` if set
2. `GOOGLE_CLOUD_AGENT_ENGINE_ID` → `VertexAiSessionService` (this is what fires on Agent Runtime)
3. `SESSION_BACKEND=sqlite` (the default) → `SqliteSessionService`
4. `InMemorySessionService`

Memory: `GOOGLE_CLOUD_AGENT_ENGINE_ID` → `VertexAiMemoryBankService`, else `InMemoryMemoryService`.

Both managed services take `project=config.PROJECT_ID`, not `os.environ["GOOGLE_CLOUD_PROJECT"]`
— see §5.

`Runner` takes the two independently: `session_service` required, `memory_service` optional
(`google/adk/runners.py:210`).

---

## 4. Configuration and credentials

### `.env` contents become deployment environment variables

The **file** does not ship. But `agents-cli deploy` reads it and turns every line into a
deployment env var, readable by anyone with project access.

- **No secrets in `.env`.** `GEMINI_API_KEY` and `ARIZE_API_KEY` arrive in Agent Runtime from
  Secret Manager. Preserve both bindings on update-only deployment; the current secrets are
  `gemini-api-key` and `arize-api-key`.
- Local development puts both API keys in the git-ignored `.env.local`. `app/__init__.py`
  loads `.env` first and then applies `.env.local` only when it is not running in Agent Runtime.
  This gives local development the Gemini Developer API key without turning it into a deployment
  environment variable.
- Specialists receive deterministic records and scoped RAG evidence through
  `get_returns_context(question, order_id, ...)` or
  `get_billing_context(question, order_id, invoice_id, ...)`. Raw extracts stay in the tool
  result for agent reasoning and controlled eval analysis. Default Arize tracing masks them;
  access to raw evidence requires a separate approved capture/evaluation path.
- **`GOOGLE_CLOUD_PROJECT` is stripped** as a reserved variable. The deployed agent never sees
  it.
- Local-only settings must not be set in `.env` at all, or they travel. `SESSION_BACKEND` and
  `SESSION_DB_PATH` default in code for this reason.

### Stale `GOOGLE_APPLICATION_CREDENTIALS`

`_ignore_stale_adc()` runs at import in `app/config.py`: a pointer to a **missing** file is
dropped so ADC falls back to gcloud credentials. A valid pointer is never touched.

It lives in `config.py` — which everything imports — rather than in one script, because the
stale value broke the agent, the integration tests and the playground, not just the corpus tool.
It is inherited from the launching terminal, not set in any profile file.

**It does not cover `agents-cli` itself**, which is an external tool. `unset
GOOGLE_APPLICATION_CREDENTIALS` before deploying.

### Project resolution

```python
def _resolve_project() -> str:
    if env := os.getenv("GOOGLE_CLOUD_PROJECT"):
        return env
    try:
        import google.auth
        _, project = google.auth.default()
        return project or ""
    except Exception:
        return ""
```

---

## 5. The deploy, and the four things it exposed

The initial September backend bootstrap took four deployments. These failures did not
reproduce in local testing; live serving verification exposed configuration, packaging and
identity assumptions. This historical account is separate from the October Arize rollout.

| # | Failure | Cause | Fix |
|---|---|---|---|
| 1 | Retrieval returned nothing | Agent Runtime does not set `GOOGLE_CLOUD_PROJECT`; code read only that env var. Worked locally *because* `.env` set it | ADC fallback in `_resolve_project()` |
| 2 | Every order lookup died | `FileNotFoundError: /code/data/fixtures.json` — the deploy packages the agent directory, so a repo-root `data/` does not travel | Moved to `app/data/fixtures.json` |
| 3 | No diagnostics for any of it | Retrieval `except` blocks returned a clean sentinel and logged nothing | `logger.exception` in both handlers |
| 4 | Retrieval still returned nothing | Service account had only `roles/aiplatform.reasoningEngineServiceAgent`, which carries **no RAG permissions** | Granted `roles/aiplatform.user` |

**#3 is the one that cost the most.** The agent failed *correctly* every time — told the customer
it could not check, escalated, never guessed about a refund — while leaving no record of why. A
loud crash would have been found in one deploy instead of four. Safe failure without diagnostics
is half a design.

**#4 involves two different service accounts, which is what made it hard to see:**

| Account | Role |
|---|---|
| `service-<num>@gcp-sa-aiplatform` | General Vertex AI service agent. Already had `aiplatform.user` via Terraform |
| `service-<num>@gcp-sa-aiplatform-re` | **Reasoning Engine** service agent — the one that executes a deployed agent. Had nothing useful |

Terraform *looked* like it granted the role. It granted it to the wrong identity.

### Both manual cloud fixes are now declared

Granted by hand during the build, then written into Terraform so a rebuild reproduces them:

- `reasoning_engine_sa_roles` in `deployment/terraform/single-project/iam.tf` + `variables.tf`
- `vectorsearch.googleapis.com` in `apis.tf` (serverless RAG is backed by Vector Search)

These declarations have not been validated with `terraform plan`; Terraform was unavailable
on the build machine. Matching live resources do not establish Terraform ownership or make a
full apply safe. Reconcile state and import applicable existing resources before planning an
apply; inspect changes rather than recreating them after a 409.

### Project resolution in the managed services — a tidy-up, not a fix

`get_session_service` and `get_memory_service` passed
`os.environ.get("GOOGLE_CLOUD_PROJECT")`, which is `None` on Agent Runtime. They now pass
`config.PROJECT_ID`.

**Tested against the real API before changing it: the SDK resolves the project from ADC by
itself when handed `None`.** Sessions and Memory Bank were working. The change removes reliance
on an undocumented fallback that looks identical to failure #1, but it did not fix a live bug,
and the commit says so.

---

## 6. RAG Engine mode mechanics (BUILD_PLAN §6)

The backing store is a **project + location singleton** (`ragEngineConfig`), not a property of
any corpus. Deleting a corpus never changes it.

| Mode | Backing store | Cost shape |
|---|---|---|
| `basic` / `scaled` | Spanner | Provisioned, bills continuously |
| `serverless` | Vector Search | Per use, no provisioned instance |
| `unprovisioned` | none | Nothing, and all RAG data is deleted |

Spanner mode is **allowlist-only for new projects** in `us-central1`, `us-east1`, `us-east4`.
Corpus creation failed outright until we switched to serverless.

**`get_config` reports the configured mode, not a running instance.** An early draft of the plan
claimed the project was "billing right now" on the strength of `Tier: Basic` with no corpus
present. `basic` is simply the default when nothing has been set. A mode reading is not evidence
of a bill.

Lifecycle lives in `scripts/rag_corpus.py`: `up` / `status` / `down` / `serverless` /
`unprovision`.

---

## 7. Testing

The verified 1 October suite passed **147 Python checks with one opt-in live-credit check
skipped**, including **58 focused trace checks**. The latter cover real automatic ADK spans,
collector acceptance, duplicate-native filtering and exported-copy privacy, including exception
events and status descriptions. The 50-check result from 13 September is historical.

These checks cover architecture, storage transitions, serving contracts, parser failures and
privacy boundaries. They prove properties such as foreign-document rejection, create/read-only
agent tools and keyword-compatible callbacks. Deterministic model adapters also exercise real
ADK workflows without a model bill. They do not establish the quality of live policy judgement.

For the isolated suite, disable Arize export and choose an unused integration-server port:

```bash
env ARIZE_ENABLED=false TEST_SERVER_PORT=8088 uv run pytest tests/unit tests/integration -q
```

Earlier frontend acceptance passed 30 source/parser/proxy tests, TypeScript, the production
build and dependency audit. Browser verification covered navigation, drafts, search, copy,
sources, mobile/focus, a gateway outage and live stop recovery. These are dated acceptance
results; documentation updates do not rerun them.

`make smoke` (`scripts/smoke.py`) runs real conversations end to end. It spends credits.
`scripts/arize_trace_smoke.py` checks automatic instrumentation and transport; its `--real`
mode also spends Gemini/RAG credits. Its outbound SDK inspection rejects unsafe payloads before
transport. A smoke success is not a representative behavioural eval or configured Arize eval job.

**`make playground`, not `agents-cli playground`.** The latter shells out to `adk web .` with no
service flags, so it runs on ADK's in-memory defaults: conversations lost on restart, no memory
service, `user:` state not carried between sessions. Confirmed by checking that a playground
session never reached the SQLite file. `make playground` passes `--session_service_uri` and
`--memory_service_uri` explicitly. `make playground-plain` keeps the unconfigured behaviour for
comparison.

---

## 8. Firestore support actions

- **Customer id** — `user:customer_id`, set by `lookup_order` from the fixture.
- **Request refs** — `REQ-` plus ten uppercase characters from an idempotency-key hash,
  generated in `app/support_actions.py`.

Agent Runtime and Cloud Run select `FirestoreSupportActionStore`; local development and unit
tests default to the same interface backed by memory. A request reference is returned only after
Firestore confirms creation. Repeated delivery of the same tool call resolves to the existing
record rather than filing a duplicate.

Review transitions execute in Firestore transactions with an expected version. This makes a
stale dashboard tab fail visibly instead of silently overwriting another reviewer's decision.
Each change appends actor, time, event and note to the audit list. The agent toolset contains
create and status lookup only; approval and completion exist solely behind the reviewer API.

The first live storage smoke moved a request through `pending → in_review → approved →
completed`, retained four audit events, then deleted the synthetic record.

---

## 9. Private Support Operations dashboard

`app/dashboard_app.py` is deployed separately to the private Cloud Run service
`support-ops-dashboard`. Its dedicated service account can read and update Firestore. The
customer-facing Agent Runtime identity has no decision tool in its application capability
surface.

The portfolio MVP uses Cloud Run IAM to reject unauthenticated traffic and maps every admitted
request to the one configured reviewer account. This is honest single-reviewer
attribution, not a pretend multi-user login system. If more reviewers are added, put IAP in front
of the service and use its authenticated-user header so each audit event retains the real actor.

The dashboard polls every 15 seconds. Firestore remains the operational source of truth;
BigQuery is intentionally deferred until history size or analytical complexity makes an export
useful.

The current URL, IAM verification, authenticated local proxy and update/rollback steps are
documented in [the deployment guide](deployment.md). Anonymous `/ops/` access returns 403;
the customer UI should never be linked as if it were hosted on this Cloud Run service.

Cloud resources were provisioned manually during the live build and also declared in Terraform.
Because this repository had no initialised Terraform state and Terraform is unavailable on this
machine, import the existing database, service account and Artifact Registry repository before a
future full apply.

`TODAY = date(2026, 9, 12)` is pinned in `app/fixtures.py` so `days_since_delivery` is stable for
evals.

---

## 10. Known gaps

- There is no commerce connector. A reviewer records completion but no code issues a refund or
  modifies an order.
- The dashboard identity mode supports one reviewer. Multi-user production use requires IAP or
  an equivalent identity-aware proxy.
- `MEMORY_BACKEND` and `SESSION_BACKEND` remain visible as legacy Agent Runtime environment
  values after update-only deploys. Runtime selection checks the injected engine ID first, so
  managed sessions and Memory Bank still win; a future full configuration replacement should
  remove the dead values.
- Latency remains above the proposed 15-second p95 gate. The latest corrected frontend trace
  took 35.14 seconds; a separate 143.3-second workflow included a 124.7-second initial root call
  and exceeded the gateway's 120-second turn limit. No repeated latency or cost baseline exists.
  [The cost guide](costs.md) gives planning assumptions, not a measured production bill.
- Default masking at the Arize export boundary does not change original native Cloud spans.
  Review native capture settings, retention and access separately before handling real data.
- Export monitoring, alert thresholds, policy-ingestion ownership and retention/consent are
  planned. Trace ingestion alone does not configure them or hosted Arize evaluation jobs.
- The customer gateway has a process-local turn guard. Public or multi-instance use needs
  verified customer/order access, a shared lease/usage store and customer quotas.

---

## 11. Customer frontend and deployed-runtime gateway

`frontend/` is Next.js App Router/React/TypeScript with Tailwind and Radix-backed components.
Its same-origin API proxy connects to `app/chat_gateway.py`; only Python holds Google ADC and
calls the existing deployed runtime through the Agent Platform SDK. `make chat-ui` binds
`127.0.0.1:3010`; `make chat-gateway` binds `127.0.0.1:8081`. Cloud Run continues to host the
private reviewer dashboard. ADK web is a development playground, not the customer interface.

The proxy allowlists routes, rejects foreign origins, caps streamed request bodies at 40,000
bytes and rejects invalid UTF-8. The gateway separately limits a message to 6,000 characters,
validates identifiers and enforces ownership of managed conversations by a signed HttpOnly
visitor cookie. Browser Authorization is not forwarded. These controls protect this demo's
conversation boundary; they do not authenticate a customer or authorise access to real orders.

The gateway's default metadata/API timeout is 30 seconds and turn timeout is 120 seconds;
the Next.js upstream wait is 150 seconds. The stream parser requires a terminal event and
surfaces malformed or interrupted streams instead of silently treating them as success.
An upstream chat failure is not automatically retryable because the remote workflow may have
filed a request. Confirmed request references and saved history support recovery.

Stop aborts browser display, not guaranteed remote execution. The gateway retains the active
visitor/conversation guard until completion or timeout. It recovers saved results where possible;
another action request must not be blindly resent after an ambiguous interruption. A gateway
restart loses the process-local guard, which is why deployment currently requires one worker
until coordination moves to a shared store.

Only known policy documents may become source chips. Explicit source-labelled names can resolve
to a **Referenced policy** link; this is not a fabricated excerpt or retrieval verification.
New conversation creates a real managed session and titles it from its first message. Drafts
survive switching during the browser session; messages are not stored in browser local storage.
Production setup, UX acceptance evidence and launch gaps are in [frontend guide](frontend.md).
