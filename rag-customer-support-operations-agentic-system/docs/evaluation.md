# Evaluation workflow and evidence

> Build update — 4 October 2026: the results below are historical; the two seed
> cases now reflect policy-only guidance, but have not been rerun or calibrated.
> They do not validate the new single-agent scope. Firestore/action capabilities
> have been removed from source. Expand the seed coverage and calibrate
> the judge before using these results as acceptance evidence.

Reviewed on 1 October 2026. The formal evaluation engine is **`agents-cli` with
a local custom Gemini judge**. The Galileo → Arize AI migration changes the
observability destination to Arize AX; it does not replace the grading engine.
See the [main README](../README.md) for setup and the
[observability guide](observability.md) for tracing, privacy and rollout evidence.

## What is implemented

| Layer | Current implementation | Evidence and limits |
|---|---|---|
| Dataset | Two synthetic single-turn seed cases in [`basic-dataset.json`](../tests/eval/datasets/basic-dataset.json) | Final-sale exception and faulty-item policy guidance; no holdout split or new-design results. |
| Agent execution | `agents-cli eval generate`, using the project's ADK HTTP server | Current source is a single policy agent; historical results used root/specialists. |
| Scoring | `custom_response_quality` in [`eval_config.yaml`](../tests/eval/eval_config.yaml), implemented in [`response_quality.py`](../tests/eval/response_quality.py) | Local Python function calls Gemini `gemini-3.7-flash`; grading is billed. |
| AI observability | Automatic OpenInference ADK spans exported to Arize AX | Agent inference can be traced; content is redacted by default. |
| Hosted Arize evaluations | No evaluator, experiment, score-upload or online-evaluation configuration in this repository | Trace delivery is verified separately; hosted evaluation setup has not been verified or implemented here. |

The judge receives the user prompt, final response, optional reference and full
`agent_data` trace. Its rubric checks policy correctness, source citations,
clarity, empathy and refusal to claim actions were executed. It penalizes policy
decisions based on memory/general knowledge rather than retrieved evidence.
The revised rubric is an uncalibrated seed, not an acceptance evaluator.

The judge uses temperature 0 and a structured score/explanation schema. These
reduce variation and parsing failures; they do not guarantee deterministic
grading. Valid scores are clamped to 1–5. Missing parsed output returns 0;
a failed grading call can appear as a null score with an error in the CLI report.
Neither a release threshold nor a pass-rate rule is configured. Inspect scores,
errors and explanations rather than treating a zero CLI exit code as acceptance.

`agent_turn_count` is defined but not selected. It counts conversation turns in
the saved trace, not model calls or specialist hops. Both current cases have one
conversation turn despite taking several model calls.

## Dated baseline

The preserved local artifacts record one generated two-case dataset and two
grading attempts on **13 September 2026**:

| Artifact | Result |
|---|---|
| [`traces_20260913_162051.json`](../artifacts/traces/traces_20260913_162051.json) | Captured both agent conversations and tool events. |
| [`results_20260913_162125.json`](../artifacts/grade_results/results_20260913_162125.json) | 0 valid cases, 2 grading errors: the judge lacked an API key. No quality score. |
| [`results_20260913_162305.json`](../artifacts/grade_results/results_20260913_162305.json) | 2 valid cases, 0 errors; both scored 5/5, mean 5.0, standard deviation 0.0. `pass_rate` is null. |
| [`results_20260913_162305.html`](../artifacts/grade_results/results_20260913_162305.html) | Human-readable report containing replies, traces and judge explanations. |

The successful result's creation timestamp is `2026-09-13T15:23:05.477521Z`.
Both grading reports contain identical captured agent responses and traces. The
change proves recovery from a judge-credential failure, not an improvement in
agent behaviour.

| Case | Score | What the judge accepted |
|---|---:|---|
| `final_sale_is_refused_not_filed` | 5/5 | Catalogue override applied, policy/catalogue cited, no return claimed or filed. |
| `faulty_exchange_creates_pending_handoff` | 5/5 | Faulty-goods policy checked, request reference given, pending human review stated, source retained. |

The second trace contains a `request_exchange` result with `filed: true`, a
reference and `completed: false`. This evaluates the recorded tool result and
customer wording; the judge does not independently read Firestore or verify a
reviewer's decision. That operational proof belongs to the separate action
workflow described in the main README.

These reports are historical evidence for two development cases. They do not
establish billing quality, retrieval recall, safety rates, latency percentiles
or behaviour of every later revision. The report embeds case/reference data but
does not record a Git revision, judge configuration revision or policy/corpus
version. `artifacts/` is ignored by Git, so these local evidence links may be
absent in a fresh checkout. Preserve reviewed synthetic reports with a run
manifest when publishing a reproducible comparison; do not overwrite existing
evidence.

## Run a local baseline

Run commands from the repository root. Use the locked project environment and
the installed `agents-cli` version described in the main README. Configure the
Gemini key in ignored `.env.local`, non-secret settings in `.env`, and ADC for
the Google Cloud RAG client. The policy corpus must be available; the judge
cannot turn broken retrieval into evidence of grounded behaviour.

```bash
uv sync --locked
uv run python scripts/rag_corpus.py status
agents-cli eval run --help
agents-cli eval generate --help
agents-cli eval grade --help
```

The following command uses live model/RAG calls and a billed Gemini judge, while
keeping support requests and sessions in local memory. It disables trace export
for this standalone baseline so trace credentials are not another dependency:

```bash
env -u GOOGLE_CLOUD_AGENT_ENGINE_ID -u K_SERVICE -u SESSION_SERVICE_URI \
  SESSION_BACKEND=memory SUPPORT_ACTION_BACKEND=memory \
  GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=false ARIZE_ENABLED=false \
  agents-cli eval run \
  --dataset tests/eval/datasets/basic-dataset.json \
  --config tests/eval/eval_config.yaml \
  --metrics custom_response_quality --concurrency 1 --qps 1
```

Keep `.env.local` limited to local secrets rather than overrides of these
isolation settings. The application loads that file with override enabled.
`make eval` checks corpus availability and then calls the CLI; it does not itself
isolate the support-action backend. With Firestore selected, the exchange case
can create a real pending review request. In-memory runs check logical filing
and wording, not durable storage.

These overrides apply when a new server starts. The CLI can reuse a project
server recorded in `.adk/`, whose existing backend settings do not change with
this command. For a fresh baseline, use `agents-cli run --stop-server` when that
project development server can be stopped, or target a dedicated ADK server
whose settings you control. Do not assume the shell overrides reconfigure a
reused server or a `--url` target.

By default, the CLI starts or reuses the project's FastAPI/ADK server. New servers
use an available port starting at 18080; a server started for generation is
stopped afterward. It creates a fresh session per
case but uses the fixed `eval-cli-user` identity. Local memory can therefore carry
between cases within a run. `--concurrency 1` limits parallel dispatch; it does
not provide independent memory identities. Strict case isolation is remaining
work, particularly for future memory/conflicting-policy experiments.

### Separate inference from grading

Use a new directory per experiment, changing the example run label each time:

```bash
mkdir -p artifacts/eval_runs/baseline-YYYYMMDD-01

env -u GOOGLE_CLOUD_AGENT_ENGINE_ID -u K_SERVICE -u SESSION_SERVICE_URI \
  SESSION_BACKEND=memory SUPPORT_ACTION_BACKEND=memory \
  GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=false ARIZE_ENABLED=false \
  agents-cli eval generate \
  --dataset tests/eval/datasets/basic-dataset.json \
  --output artifacts/eval_runs/baseline-YYYYMMDD-01/traces.json \
  --concurrency 1

agents-cli eval grade \
  --traces artifacts/eval_runs/baseline-YYYYMMDD-01/traces.json \
  --config tests/eval/eval_config.yaml --metrics custom_response_quality \
  --output artifacts/eval_runs/baseline-YYYYMMDD-01/grades --qps 1
```

`eval run --output` controls the grade-results directory; its intermediate traces
still use the default trace directory. A bare `eval grade` loads every JSON trace
there, including older runs. An explicit `--traces` file avoids mixing samples.
Re-grading reuses captured responses and incurs judge calls; it does not rerun
the agent or validate today's live policy behaviour.

Compare complete result JSON files after inspecting errors and case identities:

```bash
agents-cli eval compare path/to/baseline-results.json path/to/candidate-results.json
```

For an already running agent, `eval generate --url <ADK-base-URL> --app-name app`
requires ADK `/apps/.../sessions` and `/run_sse` routes. The customer frontend's
`/api/chat` endpoint and an Agent Runtime resource name are different interfaces.
`--project`/`--region` configure managed grading; they do not select the custom
judge's Gemini client backend or change agent inference location. The current
judge uses environment/ADC/API-key configuration instead.

## Dataset and scoring discipline

The [dataset README](eval-datasets.md) documents the actual input
shape. Give cases stable IDs, synthetic inputs and outcome-based references.
Keep policy correctness, action authorization and customer wording explicit.
Do not fit references to an incorrect generated answer to make a score improve.

For a comparison, preserve the Git revision, dataset/reference hashes, judge
model and rubric revision, root/specialist model IDs, fixture clock, policy
document/corpus revision, runtime/backend settings, inference and grading times,
case-level errors and repeats. Current reports do not capture that complete
manifest. Repeated samples and human review are needed to distinguish a real
improvement from model/judge variation. A Gemini judge shares model-family
biases with this Gemini-based agent, and one overall score can hide which layer
failed. Adversarial judge-input handling and judge/human agreement have not been
measured.

## Coverage and release gaps

The broader [product success metrics](build-plan.md#7-evaluation-plan) are
proposed gates, not measured results of the two-case suite. The coverage plan is:

| Area | Remaining cases or measurements |
|---|---|
| Routing and billing | Billing-only and cross-domain requests; authorization holds vs duplicate charges; refund routing/timing; invoices, split payments and discounts. |
| Retrieval and ruling | Standard returns, boundary dates/conditions, correct-clause hit rate, document scoping, citation accuracy and further catalogue overrides. |
| Refusal and injection | Unsupported questions, unavailable corpus, instruction-like retrieved text and customer pressure. Measure critical unsafe/unsupported claims separately. |
| Actions and approval | Missing confirmation, failed writes, retries/duplicates, nonexistent references and pending/completed wording. Keep durable/concurrent-review checks separate from an LLM opinion. |
| Memory and continuity | Cross-session recall, contact reuse, old memory conflicting with policy, fresh retrieval on repeated questions and isolation between customers/cases. |
| UX and operations | Recovery/timeouts, repeated-information friction, human override/decision time and grounded-resolution rate. |
| Latency and cost | Repeated p50/p95 time to first useful text and completed turn, eligible LLM call/token counts, retries and cost per grounded completion; judge cost separately. |
| Generalization | Separate development and holdout datasets, repeated runs, policy variants/paraphrases and a human-labelled subset. |

No holdout dataset, repeated quality distribution or enforced release gate is
currently checked in. Structural/privacy/store tests cover important software
boundaries, but their pass count is not a behavioural accuracy percentage. The
Arize smoke verifies instrumentation, export acceptance and one fixture turn;
its duration is not a p95 result or a replacement for this evaluation suite.

## Relationship to automatic Arize AX traces

Importing `app.agent` initializes Arize instrumentation when enabled; the FastAPI
entry point also attaches it after Google telemetry setup. Evaluation **agent
inference** therefore follows the normal automatic ADK tracing path when
`ARIZE_ENABLED=true` and the [Arize configuration](observability.md#configuration)
is present. To observe a controlled synthetic run, enable that setting instead
of the baseline command's `ARIZE_ENABLED=false`, keeping content capture false.

The custom judge executes in the CLI process outside ADK and does not initialize
this integration. Its Gemini grading calls and scores are not exported by the
existing ADK instrumentor. No hosted Arize evaluator, experiment upload, online
score pipeline or alert threshold is configured here. Setting Arize environment
variables enables tracing; it does not create evaluations.

`eval_case_id` survives in local evaluation artifacts, but the CLI does not send
it, a run ID or trace context to the agent. Its generated cases also omit server
session/invocation IDs. Automatic score-to-Arize-trace correlation is therefore
not implemented. Time windows and operational session metadata can help manual
triage; an exact automated join needs explicit case/run correlation before it
can be claimed.

Use the root trace duration for total turn time and its hierarchy to locate
slow work; parent durations include children. For usage comparisons, count the
eligible OpenInference LLM spans once. Native Google GenAI children and workflow
aggregate totals must not be added again. The export filter prevents native
duplicate spans from inflating Arize's view.

## Privacy-safe synthetic evaluation

Arize's default `ARIZE_CAPTURE_CONTENT=false` masks message/tool content and
sanitizes exported errors. It leaves operational structure and usage metadata
for diagnosis. Native Google capture remains separately configured, as detailed
in the observability guide.

The CLI's local JSON/HTML artifacts and custom Gemini judge still contain full
prompts, responses, references, tool results and agent instructions. They are
not redacted by Arize's export policy. Use demonstration orders and invented
contact data, never customer transcripts or credentials, and inspect artifacts
before sharing them. Keep keys in `.env.local` locally and Secret Manager when
deployed, outside datasets, reports and command-line arguments.

Content-dependent hosted evaluations cannot be inferred from metadata-only
trace delivery. If hosted Arize scoring is introduced later, first define the
synthetic dataset, rubric, content boundary, retention, run/case correlation,
human-review calibration and score destination. Until then, the authoritative
quality evidence is the explicitly generated and graded local report.

## Reference

The [official agents-cli evaluation guide](https://google.github.io/agents-cli/guide/evaluation/)
describes generation, grading and custom metrics. Installed `--help` and the
checked-in configuration remain the authority for this project's commands.
