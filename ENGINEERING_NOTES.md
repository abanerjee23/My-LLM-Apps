# Engineering Notes

Implementation detail that used to live in `BUILD_PLAN.md`. It was moved here so the plan reads
as a product document and this reads as the thing you hand an engineer.

**`BUILD_PLAN.md` owns the decisions and their costs. This file owns how they are implemented,
and every trap found in implementing them.** Where a section here maps to a decision there, the
reference is given.

---

## 1. ADK specifics that are easy to get wrong

### Explicit specialist AgentTools (BUILD_PLAN 2.2)

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

### Callback parameter naming (BUILD_PLAN 2.8)

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
request, cannot be forgotten by the model, and costs no extra round trip.

**Specialists have since had `load_memory_tool` removed entirely.** Unused wiring plus
instructions describing behaviour that never happened is worse than neither. The deciding
argument is not tidiness: the specialist is the only agent that issues a ruling, and BUILD_PLAN
2.8 says memory may never decide one. Removing the tool makes that structural instead of
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

### Scope assertion (BUILD_PLAN 2.4)

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
Recorded in BUILD_PLAN 2.3 for that reason.

### Corpus resolution

Resolved by display name (`RAG_CORPUS_DISPLAY_NAME`), never by ID — the ID changes on every
`make rag-up`. `reset_cache()` clears the memoised corpus name and client, used by tests and
after a re-ingest.

---

## 3. Services factory

`app/app_utils/services.py`. We **extended the scaffold's existing factory** rather than writing
a second one; a parallel implementation would have been exactly the two-code-path bug that
BUILD_PLAN 2.7 warns about.

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

- **No secrets in `.env`.** `GEMINI_API_KEY` arrives in Agent Runtime from Secret Manager via
  `--secrets GEMINI_API_KEY=gemini-api-key`.
- Local development puts `GEMINI_API_KEY` in the git-ignored `.env.local`. `app/__init__.py`
  loads `.env` first and then applies `.env.local` only when it is not running in Agent Runtime.
  This gives local development the Gemini Developer API key without turning it into a deployment
  environment variable.
- Specialists receive deterministic records and scoped RAG evidence through
  `get_returns_context` or `get_billing_context`. Raw extracts stay in the tool result for traces
  and eval failure analysis; the model still makes the policy judgement.
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

Four deploys were needed. **None of the four failures was reproducible locally** — that is the
main lesson, and it is why BUILD_PLAN 6.6 exists as its own phase.

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

Not validated with `terraform plan` — Terraform is not installed on the build machine. The IAM
binding already exists live, so an apply should be a no-op; on a 409, `terraform import` rather
than recreate.

### Project resolution in the managed services — a tidy-up, not a fix

`get_session_service` and `get_memory_service` passed
`os.environ.get("GOOGLE_CLOUD_PROJECT")`, which is `None` on Agent Runtime. They now pass
`config.PROJECT_ID`.

**Tested against the real API before changing it: the SDK resolves the project from ADC by
itself when handed `None`.** Sessions and Memory Bank were working. The change removes reliance
on an undocumented fallback that looks identical to failure #1, but it did not fix a live bug,
and the commit says so.

---

## 6. RAG Engine mode mechanics (BUILD_PLAN 2.5)

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

`uv run pytest` — 50 passing checks and one intentionally opt-in live-credit check.

They cover **wiring, not judgement**: that specialists are explicit root tools, that scope assertion
drops foreign chunks, that no `request_*` tool can mark a request approved, that the callback
signature is what ADK will call. Model output is non-deterministic and belongs in evals, not in
pytest.

`make smoke` (`scripts/smoke.py`) runs real conversations end to end. It spends credits.

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
- Latency remains above target: recent grounded turns took roughly 22–29 seconds. Repeated model
  and architecture experiments are still required.
