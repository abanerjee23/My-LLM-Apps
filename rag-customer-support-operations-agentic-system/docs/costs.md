# Cost and measurement

Planning reference checked **1 October 2026**. The goal is an affordable,
grounded completion with an accountable handoff, not the cheapest model call
in isolation. Costs below are in USD; account discounts, tax, credits and
other workloads are not known from the trace evidence.

## Model rates and routing

The current configuration uses the **Gemini Developer API**, not Vertex AI model
endpoints. RAG and managed cloud services still use Google Cloud credentials.

| Model | Role | Standard input / output per million tokens |
|---|---|---|
| `gemini-3.8-flash` | Root routing and customer delivery | $0.75 / $3.75 through 31 December 2026; $1.50 / $7.50 from 1 January 2027 |
| `gemini-3.1-pro-preview` | Returns/exchanges and billing specialists | $2 / $12 for prompts ≤200k tokens; $4 / $18 above 200k |
| `gemini-3.7-flash` | Custom evaluation judge | $0.75 / $3.75 through 31 December 2026; $1.50 / $7.50 from 1 January 2027 |

Output pricing includes thinking tokens. These are published paid Standard API
rates, not a claim about this account's bill or free-tier eligibility. Confirm
rates before the next forecast or experiment. [Official Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing).

## What has actually been measured

The corrected deployed frontend trace
`ee18fb93d34d639e485828b08573974e` showed **four model calls**, **11,424 tokens**,
**35.14 seconds** and an Arize model-cost estimate of **$0.019899**. This is one
final-sale question. Arize's estimate is not an invoice and does not include
hosting, storage, memory generation, evaluation or the Arize account plan.

The number of agents is not the number of model requests. Do not forecast each
turn as one root call plus one specialist call when tool use can require several
requests. Count eligible LLM spans once; do not add native duplicate spans,
workflow aggregates or parent totals again. Similarly, parent durations include
children and must not be summed into customer latency.

For an explicit illustration, **1,000 conversations × two turns × $0.019899 =
about $39.80 in model costs** if every turn resembles that one sample. This is
not a representative monthly forecast. Different routes, longer histories,
thinking tokens, retries, failed turns and memory/judge calls can change it.
The previous fixed monthly estimate has been replaced by this transparent
assumption and a measurement plan.

For each request, estimate model spend as:

```text
sum over model calls:
  input_tokens × input_rate / 1,000,000
  + billable_output_tokens_including_thinking × output_rate / 1,000,000
```

Account for caching or other pricing modes only when actually enabled and
verified. Track judge spend separately from customer inference. The current
CLI's custom judge runs outside the ADK trace integration, so the Arize inference
tree does not automatically account for evaluation grading costs.

## Service cost drivers

The account's actual allowances and charges have not been audited. Avoid
assuming that a small demonstration, scale-to-zero or a provider free tier makes
every component free.

| Component | Cost driver and measurement needed |
|---|---|
| Agent Runtime | Active compute/memory, instance duration and cold-start behaviour; current scaling is 0–10 instances |
| Managed sessions | Stored conversation events, retention and traffic |
| Memory Bank | Stored/retrieved memories plus generation calls; continuity adds work beyond the immediate answer |
| Serverless RAG | Ingestion, embeddings, storage/retrieval and any reranking; verify the corpus mode and region |
| Firestore | Reads/writes, transactional action updates, audit history and dashboard polling |
| Cloud Run | Active requests/compute for the private reviewer dashboard |
| Secret Manager | Active secret versions and access operations |
| Build and artifact storage | Cloud Build minutes, Artifact Registry images and staging Cloud Storage |
| Cloud Logging and Trace | Telemetry ingestion, storage/retention and downstream processing |
| Arize AX | The account's plan, span/storage volume and any future hosted evaluation usage; plan/billing not verified |
| Customer frontend hosting | A future hosting choice; no public frontend-hosting cost is established here |

Use current provider billing reports rather than assigning each line a guaranteed
zero. Relevant references: [Cloud Run pricing](https://cloud.google.com/run/pricing),
[Firestore pricing](https://cloud.google.com/firestore/pricing),
[Google Cloud pricing](https://cloud.google.com/pricing) and
[Arize plans](https://arize.com/pricing/).

## Evaluation and smoke budgets

| Operation | Cost boundary |
|---|---|
| Unit/integration checks with Arize disabled | Most use deterministic adapters; the billed live check is opt-in |
| Default Arize smoke | Deterministic ADK model adapter, real OTLP delivery; no model bill, but sends telemetry |
| Arize smoke with `--real` | Live agent/model/retrieval usage |
| Behavioural dataset generation | Real model/tool calls; can mutate configured support-action storage |
| Custom judge grading | Additional Gemini calls, including failed/repeated grading attempts |
| Deployed memory/action smokes | Real managed-service/model calls; synthetic data must be isolated and cleaned |

Estimate cases × repetitions × expected call usage before a run. Separate
inference generation from grading to avoid paying for unchanged responses again.
Preserve explicit trace/report files so a regrade uses the intended cases.
See [evaluation](evaluation.md) for backend/server isolation and report handling.

## Measurement and iteration plan

Record cost per grounded completed turn and per resolved conversation, including
failed turns and retries. Pair it with task completion, critical safety failures,
citation validity, p50/p95 latency and reviewer effort. A cheaper model that
creates more escalations or incorrect decisions may cost the product more.

Establish a repeated baseline on a fixed dataset and retain holdouts before an
all-Flash comparison. Keep model IDs, input versions, backend/session settings,
judge rubric and run dates stable; inspect critical failures rather than relying
on an average score. The current two-case baseline cannot settle a model choice.

Google Cloud budget alerts, model-call budgets, Arize alerts and routine billing
review are proposed operational controls; they are **not configured by the
tracing migration**. Assign owners and thresholds before a customer launch.
See [the build plan](../BUILD_PLAN.md) and [observability](observability.md).
