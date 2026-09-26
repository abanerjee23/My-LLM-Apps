# SourceLens — Implementation Roadmap and Cost Plan

**Date:** 24 September 2026  
**Status:** Working MVP deployed to Cloud Run; Galileo and Qdrant Cloud credentials remain.  
**Product brief:** [PRODUCT.md](PRODUCT.md)

**Agreed initial build envelope:** $10–$12 estimated usage, covering 10–15 total live investigations including 5–10 evaluation investigations. The application now enforces a configurable live-run ceiling and model-cost ledger. See section 10.1 for assumptions and exclusions.

## 1. Build objective and latest scope

Build a working investigation workspace against a real Google BigQuery connection containing a coherent reference business. A user submits a brief; the agent independently inspects data, executes validated SQL, retrieves feedback evidence, tests explanations, updates visuals, and produces a reviewable brief. Accepted, edited-and-accepted, and rejected briefs become dated notebook snapshots.

This plan incorporates decisions made after the initial product brief: a generated reference database is the primary integrated demonstration. Real Amazon reviews remain a separate optional dataset demonstration. Do not represent generated sales joined to historical Amazon feedback as verified business outcomes.

Priorities are a complete vertical slice, visible evidence-backed agent actions, trustworthy SQL, and measurable performance. Authentication remains out of scope for the application. Infrastructure credentials and service permissions are still required.

## 2. Proposed architecture

**Cost revision:** Start with the lean development configuration in section 10.1. The hosted architecture below is a later deployment target, not the initial resource footprint. BigQuery and raw Cloud Storage remain real cloud services; the FastAPI application, worker, frontend, and PostgreSQL run locally during development. Query execution stays in BigQuery and only bounded results reach the local application. Do not provision Cloud SQL or hosted workers initially.

```text
React / TypeScript workspace
  chat + action timeline | charts + evidence + brief | notebook
                         |
                   FastAPI / Cloud Run
                         |
          Persisted investigation state + event stream
                         |
             Queued investigation worker / Cloud Run
                         |
             OpenAI Agents SDK: Sol investigator
                         |
        Schema tools / validated SQL / evidence retrieval
             |                           |
     BigQuery business tables     Saved feedback + search index

Cloud Storage: raw snapshots, generated exports, source manifests
Cloud SQL PostgreSQL: investigations, events, reviews, notebook versions
Sol preparation worker: structured extraction from feedback
Galileo: evaluation and AI traces
```

Use BigQuery for analytical data and Cloud SQL PostgreSQL for transactional application state. Do not put notebook writes in BigQuery or persist application state on Cloud Run's ephemeral filesystem.

Use Qdrant Cloud's free tier for semantic retrieval, with product/date/theme filters and source-record references in payloads. BigQuery remains the analytical store and Cloud Storage the raw evidence archive. The Qdrant index is derived and rebuildable, never the only copy of evidence. Start with a suitable free Qdrant Cloud Inference embedding model, recording its name, version, dimensions, and preprocessing settings; verify current availability and retrieval quality before selection. Do not silently switch to paid embeddings.

The verified free cluster includes 4 GB disk, 1 GB RAM, and 0.5 vCPU on one node; disk includes vectors, indexes, and payloads. Free clusters suspend after one week of inactivity and are deleted after four weeks of inactivity if not reactivated. Sources: [pricing](https://qdrant.tech/pricing/), [cluster lifecycle](https://qdrant.tech/documentation/cloud/create-cluster/), [inference](https://qdrant.tech/documentation/cloud/inference/). Recheck these terms when implementing operations.

Use Cloud Tasks for bounded, idempotent worker requests with persisted checkpoints. Break work at task time boundaries; retries must resume safely. Use Cloud Run Jobs for longer bulk generation or preparation tasks. The UI receives persisted events through server-sent events with reconnection support; browser disconnection does not cancel or lose the investigation.

## 3. Data model and reference sources

### Initial scale targets

| Profile | Products | History | Sales lines | Feedback records | Purpose |
| --- | ---: | --- | ---: | ---: | --- |
| Development fixture | 20 | 6 months | 10,000 | 500 | Fast correctness checks |
| Full product demonstration | 500 | 24 months | 2 million | 10,000 | Complete investigation experience |
| Scale exercise | 2,000 | 36 months | 20 million | 100,000 | Query, processing, and cost validation |

These are generation targets, not proven capacity. Measure actual bytes and query cost before scaling. Millions of numeric rows do not require millions of model calls.

### Tables and explicit grain

| Table | Grain / important fields |
| --- | --- |
| products | One SKU: family, variant, category, launch date |
| sales_lines | One order line: SKU, date, channel, region, units, price, discount |
| price_history | SKU/channel price effective interval |
| promotions | Promotion, dates, eligible SKUs/channels, discount rules |
| inventory_daily | SKU/location/day: stock availability and stockout duration |
| returns | Return event linked to sales line: units, amount, date, reason |
| support_cases | Case: SKU, issue date, reported problem, batch when known |
| feedback | Source record: SKU, date, original text, rating, provenance |
| feedback_observations | Extracted theme/sentiment/evidence span per feedback version |

Define gross sales, discounts, refunds, net revenue, units, realised price, timezones, currency, and return attribution before analysis. Start with one currency. Returns occurring after a sale require explicit calendar-period versus sales-cohort metrics. Prevent fan-out joins between reviews and transaction lines.

### Scenario catalogue

Generate price sensitivity, product-quality deterioration, inventory constraints, seasonality, and product/channel mix scenarios, alongside unaffected controls and ambiguous cases. Some apparent correlations should be misleading. Include missing records, duplicates, changing review volumes, and mixed sentiment.

Use versioned Python/SQL source builders for business events and numerical relationships. Generate records in chunks and stage partitioned Parquet in Cloud Storage rather than assembling everything in laptop memory. Bulk-load into BigQuery; partition by relevant dates and cluster by frequently filtered entities where measurements justify it.

Generate a bounded amount of natural-language feedback with Sol, conditioned on business events. Keep scenario identifiers and answer labels out of agent-visible tables, metadata, and retrieval results. Separate evaluation ground truth using permissions, not just prompt instructions. Avoid obvious template repetition and evaluate on held-out data versions.

Persist source-builder version, parameters, object hashes, manifests, and load-job IDs. Retain original exports and subsequent versions for auditability.

## 4. Model responsibilities

### Agent count and orchestration

The investigation has three OpenAI Agents SDK roles with typed handoffs: a Sol Research Planner, a Sol Evidence Analyst, and a Sol Lead Investigator. Python is the deterministic orchestrator and source of workflow truth. It resolves the scope, runs controlled SQL, retrieves evidence, computes artifacts, validates each role's typed response, and persists the output.

The Planner owns hypotheses and analysis direction; the Evidence Analyst owns challenge, counterevidence, gaps, and the next check; the Lead Investigator owns final synthesis. SQL, calculations, provenance, and write operations remain outside model control. Each role is separately observable and costed.

| Work | Model / mechanism | Initial policy |
| --- | --- | --- |
| Interpret brief, plan investigation, construct SQL, compare explanations | `gpt-5.6-sol` | Start at medium reasoning; evaluate high for difficult cases |
| Reconcile themes, evaluate counterevidence, synthesise cited conclusions | Sol | Bounded calls with structured outputs |
| Extract themes, sentiment, entities, and evidence spans from feedback | `gpt-5.6-sol` | Start at low reasoning; process only new/changed records |
| Reference feedback phrasing | Sol | Offline preparation with separate accounting |
| Filtering, maths, schema checks, query enforcement, chart rendering | Python/SQL | No model needed |
| Production traces and run monitoring | Galileo | Correlate model, tool, cost, latency, errors, and human ratings by investigation ID |
| Offline quality assessment | Local deterministic checks and calibrated judges | Versioned artifacts; never rely on one automated judge |

Keep the three roles bounded by structured schemas rather than sharing an unbounded conversation. Model IDs, reasoning settings, prompts, handoff payloads, and extraction versions are configuration recorded on runs.

Never send entire warehouse tables to Sol. Send schema descriptions, bounded aggregate results, relevant evidence, and compact investigation state. Count context before calls and cap tool results. Confidence must come from evidence checks, not a model's unsupported numeric self-rating.

## 5. Safe SQL and visible actions

Provide typed tools such as `list_sources`, `describe_table`, `profile_scope`, `dry_run_query`, `run_readonly_query`, `retrieve_evidence`, and `publish_artifact`.

The query service must:

1. Parse GoogleSQL and allow a single approved read query; reject writes, scripts, DDL, exports, unapproved routines, remote functions, and unapproved tables.
2. Enforce restrictions with least-privilege IAM as well as application validation. The generator's write identity and evaluation ground-truth access are separate from the investigator.
3. Require appropriate partition filters, parameterise values, check a dry-run byte estimate, and set maximum bytes billed.
4. Bound concurrency, result rows/bytes, elapsed time, query count, and cumulative investigation spend. A SQL `LIMIT` does not by itself bound scanned bytes.
5. Save query text, parameters, purpose, job ID, scanned bytes, result snapshot/reference, duration, error, and retry history.
6. Validate metric definitions and join grain. Syntax-valid SQL can still be analytically wrong.

Stream user-facing events: scope established, analysis proposed, query started, query completed, evidence retrieved, hypothesis revised, artifact updated, and brief ready. Show expandable SQL and results. Explanations describe actual actions and assumptions; do not expose private chain-of-thought or manufacture thinking steps.

Quantitative citations point to reproducible query results and input versions. Qualitative citations point to exact source records and passages. Findings can require both.

## 6. Implementation roadmap

Each phase produces a reviewable result and a completion gate. Sequence is more important than calendar promises; effort ranges are provisional focused engineering days, excluding account access delays.

| Phase | Deliverables | Completion gate | Effort |
| --- | --- | --- | --- |
| 0. Foundation | SourceLens scaffold, configuration, data contracts, metric definitions, scenario specifications, eval cases | Complete | 1–2 days |
| 1. Cloud data foundation | Raw bucket, BigQuery dataset, versioned reference source, provenance manifest | Complete | 2–3 days |
| 2. SQL investigation slice | Sol narrative refinement, dry run and query enforcement, persisted runs, event log, API trigger | Complete | 2–4 days |
| 3. Feedback preparation and RAG | Evidence adapter, source viewer, Qdrant-compatible index, local fallback | Code complete; Qdrant Cloud credentials pending | 2–3 days |
| 4. Hybrid workspace | React activity trail, typed charts, evidence drawer, follow-up direction | Complete for the focused scenario | 3–5 days |
| 5. Review and notebook | Three decisions, editable summary, two ratings, dated immutable snapshots | Complete | 1–2 days |
| 6. Scale, evals, and deployment | 124,747 sales lines, ten live evals, cost telemetry, Cloud Run release | Deployed; Galileo connection pending | 3–5 days |

Indicative total: 14–24 focused engineering days, to be revised after the first working slice. Instrumentation and basic evaluations begin in phases 0–2; phase 6 broadens them rather than introducing them late.

### Suggested project structure

```text
sourcelens/
  PRODUCT.md
  BUILD.md
  backend/src/sourcelens/
    api/ agents/ connectors/ query/ evidence/ artifacts/ notebook/
  frontend/
  reference_data/      # source builder, schemas, scenario specifications
  evals/              # ground-truth artifacts never exposed to app tools
  infra/              # infrastructure definitions and deployment settings
  tests/
```

### First end-to-end acceptance scenario

A brief asks why a reference product's revenue declined. The agent inspects coverage, decomposes the change, tests at least one plausible alternative, consults feedback, shows SQL and charts, and produces a qualified conclusion with evidence. The user edits and accepts it; a dated notebook tile reopens the exact reviewed analysis. A paired insufficient-evidence case must produce an explicit gap rather than an invented cause.

## 7. Evaluation and release gates

- Deterministic checks: reference scenario totals, keys, join fan-out, metric calculations, time filtering, citation resolution, SQL restrictions, version consistency, and idempotent job handling.
- Agent checks: hypothesis quality, supporting and contradictory evidence, missing-data handling, useful follow-up questions, and unnecessary user intervention.
- Retrieval checks: evidence recall and relevance on labelled cases; do not confuse retrieval scores with prevalence.
- UI checks: true progress, query inspection, interruption, reconnect, scope updates, all notebook outcomes, and preserved revisions.
- Operational checks: rate limits, timeouts, cancelled queries, malformed outputs, retry duplication, source prompt injection, and per-run spending enforcement.
- Compare a fixed evaluation set across prompt/model changes, with held-out scenario versions and human calibration. Log model, prompts, data versions, token counts, query bytes, duration, and review feedback.

Proposed first-release gates: no known destructive-query bypass; all accepted-brief citations resolve; exact fixture arithmetic passes; all notebook paths preserve snapshots; and at least 90% supported material claims in a human-reviewed benchmark. The last threshold is a proposal, not a measured achievement. Record latency and cost baselines before fixing performance targets.

### Galileo observability decision

Galileo is the selected trace and production-observability platform. The integration will correlate every live run with its investigation ID and record model/workflow version, latency, token usage, estimated cost, tool actions, errors, and human ratings. Capture only bounded evidence references or redacted content where full source text would violate retention expectations. Local structured events and JSON eval artifacts remain available for replay and vendor-independent audit.

Required runtime configuration: `GALILEO_API_KEY`, `GALILEO_PROJECT`, and `GALILEO_LOG_STREAM`. Trace export is disabled when any required credential is absent; investigation execution must continue without failing. Galileo pricing and retention must be verified before enabling broad traffic.

## 8. Cost assumptions and verified unit rates

Prices checked against official sources on **24 September 2026**. All amounts are **USD**, before taxes. Use **us-central1** as the illustrative Google Cloud region, with co-located services; confirm the actual region before provisioning. UK/EU residency, different regions, billing currency, and networking choices can change the estimate. No trial credits or committed-use discounts are assumed.

Free tiers may already be consumed by other projects sharing the billing account. Totals below conservatively use gross usage costs unless stated otherwise. This is a planning model, not a provider quote.

### OpenAI rates

| Model | Uncached input / 1M tokens | Cached input / 1M tokens | Output / 1M tokens |
| --- | ---: | ---: | ---: |
| GPT-5.6 Sol | $4.00 | $0.40 | $20.00 |

Sources: [Sol model](https://developers.openai.com/api/docs/models/gpt-5.6-sol), [API pricing](https://developers.openai.com/api/docs/pricing).

Examples assume standard processing, uncached input, no cache-write charges, and requests below the long-context threshold. Published model guidance applies 2× input and 1.5× output rates to the full request when input exceeds 272K tokens. Do not budget only visible answer text: use billed output including reasoning tokens. Retries, repeated conversation input, and tool results also contribute. Fast processing, hosted tools, regional processing premiums, and discounts are excluded. Verify project access before running; model availability in Codex is not proof of API access. Application API spend is separate from a Codex/ChatGPT subscription.

### Google Cloud rates and planning allowances

| Service | Basis | Planning interpretation |
| --- | --- | --- |
| BigQuery on-demand queries | $6.25/TiB scanned; first 1 TiB/month free where available | 0.5 TiB = $3.13 gross; 2 TiB = $12.50; 10 TiB = $62.50 |
| BigQuery active logical storage | Approximately $0.023/GiB-month in the reference region | 10 / 25 / 100 GiB ≈ $0.23 / $0.58 / $2.30 gross |
| Cloud Run request-based services | $0.000024/vCPU-second + $0.0000025/GiB-second + $0.40/million requests at reference rates | A 1-vCPU, 1-GiB service costs about $0.0954 per active instance-hour before free tier |
| Cloud Storage | Budget assumption of $0.02–$0.03/GiB-month for regional Standard storage; confirm exact regional SKU | 20 / 50 / 200 GiB → allow $1 / $2 / $6 including a modest operations allowance |
| Cloud SQL PostgreSQL | Published shared-core compute reference: db-f1-micro $0.0105/hour | About $7.67 for 730 hours of compute alone; allow $12–$20/month with small storage/backups |
| Cloud Tasks, Secret Manager, logs, image registry/builds, network | Usage-dependent | Reserve $5–$15 for light use; larger stress allowance below |

BigQuery source: [pricing](https://cloud.google.com/bigquery/pricing). Free storage eligibility and query allowances must be checked on the billing account. Row count is not a scan-cost estimate; column selection and partition pruning matter.

Other sources: [Cloud Run](https://cloud.google.com/run/pricing), [Cloud Storage](https://cloud.google.com/storage/pricing), [Cloud SQL](https://cloud.google.com/sql/pricing), [Cloud Tasks](https://cloud.google.com/tasks/pricing), [Secret Manager](https://cloud.google.com/secret-manager/pricing).

Cloud SQL shared-core is a small demo choice without the shared-core Cloud SQL SLA; it is not a high-availability production sizing recommendation. More capable instances, HA, extra storage, and backups raise the idle baseline. Cloud Run Jobs use their own billing configuration; budget their generation/preparation usage separately rather than silently applying the service rate. Active time includes time waiting on model responses while requests are open. Avoid an always-on VPC connector unless needed; its cost is not included here.

## 9. Worked model-cost examples

Use this formula for every stage, summed across calls:

`cost = uncached_input_tokens / 1M × input_rate + cached_input_tokens / 1M × cached_rate + billed_output_tokens / 1M × output_rate`

### One ordinary investigation

| Stage | Assumed total tokens across the investigation | Cost |
| --- | --- | ---: |
| Sol planning, SQL, interpretation, brief | 50,000 input + 10,000 billed output | $0.40 |
| Optional Sol small-task work | 20,000 input + 3,000 billed output | $0.14 |
| **Base total** | Excludes initial corpus extraction and evaluation | **$0.54** |

An intensive investigation using 200K total Sol input and 40K billed output costs $1.60 for Sol, provided individual calls stay below the premium threshold. Token totals are workload assumptions to replace with measured traces, not promises. A long agent loop can cost substantially more.

### One-time or incremental feedback preparation

Assume 600 input tokens and 100 billed output tokens per review on Sol, including amortised instructions. Cost is $0.0044 per record:

| Records processed | Sol extraction cost |
| ---: | ---: |
| 10,000 | $44.00 |
| 100,000 | $440.00 |
| 1,000,000 | $4,400.00 |

Reprocessing after changing the extraction schema can incur this cost again. Cache by source hash and extraction version. Numeric sales rows need no LLM extraction.

Reference feedback generation, separately: at 200 input + 250 billed output tokens per record, Sol costs $0.0058 each, or $58 for 10,000 and $580 for 100,000. Add extraction only when running the pipeline over that generated text. At 10,000 reviews, generation plus extraction is therefore $102 before retries. This makes large-scale model generation an explicit future decision; template-based generation is the economical default for the current reference dataset.

Fifty ordinary evaluation investigations cost about $27 in agent tokens before separate judge calls, SQL, and Galileo charges. Repeated data versions and model comparisons multiply this. Evaluation is an explicit cost centre.

## 10. Illustrative monthly budgets

### 10.1 Recommended starting configuration: lean development

The user has selected a bounded initial build: **10–15 total live investigations, including 5–10 evaluation investigations**, not that many each month. For example, five manual demonstrations plus five or ten evaluations. Retries/resumed model calls consume the same usage budget; no additional live evaluation batches without revisiting it. Deterministic tests and recorded-tool replay do not consume live model calls. The storage estimates below cover the first month.

- Keep BigQuery as the actual analytical database and Cloud Storage for original evidence. Running the application locally does not require downloading or analysing the full warehouse locally.
- Run FastAPI, the worker, React, and a small PostgreSQL instance on the development machine. No Cloud SQL, always-on cloud server, or paid vector cluster initially. Local application storage is suitable for development, not the eventual durable hosted notebook.
- Start with 100K deterministic sales rows and 1K feedback records, then scale numeric rows after measuring cost. Limit model-generated feedback; numerical data generation does not require LLM calls.
- Use Sol for every model-backed role and preparation task. Prepare feedback once; reuse it across investigations. Use 5–10 evaluation investigations within the total 10–15 run allocation, with deterministic checks and recorded tool fixtures for routine development. This is a bounded evaluation, not a comprehensive production certification.
- Integrate Galileo when the available plan and costs are confirmed. Do not assume a free entitlement. Preserve compatible local traces in the meantime.

| Item | Starting assumption | Estimated cost |
| --- | --- | ---: |
| BigQuery queries | 0.1 TiB total scans/month, counting investigations and preparation | $0.63 gross; potentially $0 with available free allowance |
| BigQuery storage | 2 GiB logical storage | $0.05 gross |
| Cloud Storage | 2 GiB retained data, modest operations | $0.10 allowance |
| Other cloud usage | Small transfer/operations contingency | $1.00 allowance |
| Local app and PostgreSQL | Existing machine | $0 incremental cloud hosting; local electricity/hardware excluded |
| Qdrant Cloud | Free-tier cluster and a selected free inference model, within limits | $0 |
| 10–15 total investigations, including evals | Original $0.4076 ordinary-run assumption | $4.08–$6.11 |
| Initial 1K feedback generation + extraction | One-time standard-rate token assumption from section 9 | **$0.58** |
| **First-month build estimate** | Initial preparation plus the entire live investigation allocation | **$6.44–$8.47** |

**Agreed build envelope: $10–$12**, with headroom over the first-month estimate. This is a finite development exercise, not a recurring investigation subscription or a hosted-service quote. Galileo, independent paid judge calls, taxes, and new large ingestion batches are excluded until explicitly budgeted. Free inference selection is a condition of the $0 embedding line. No trial credits are required for the gross Google Cloud estimate.

Retain the two-million-row scale test as an explicit exercise rather than a recurring rebuild. More numeric rows need not materially increase cost when queries prune data well; repeated Sol investigations and bulk text generation are the main controls here.

Proposed application model-spend ceiling: $9 across this build's investigation, preparation, and evaluation calls, with a pause before dispatch when the next call's reserved maximum would exceed the remaining budget. Also enforce the 15-investigation maximum. Limit concurrency and account for retries. Pair this with BigQuery byte quotas and per-query limits; billing alerts alone do not enforce a total cap. The $10–$12 envelope remains an estimate, not a guaranteed cloud bill cap. Longer-than-assumed investigations can exhaust the model allowance before all runs complete.

After completion, disable live investigation endpoints when not demonstrating the product. Retained BigQuery/Cloud Storage data still has a small ongoing cost (about $0.15/month at these gross storage/operations allowances, with actual usage varying). A public interactive deployment has separate hosting and usage costs; initially publish product screenshots, a recording, or a read-only artifact. Do not promise an always-on free private notebook.

### 10.2 Later hosted configurations (not the starting recommendation)

Assumptions: the dataset is already generated; 100 / 500 / 2,000 ordinary investigations; 0.5 / 2 / 10 TiB queried across application, preparation, and evaluation work; 10 / 25 / 100 GiB BigQuery storage; 20 / 50 / 200 GiB raw storage; roughly 20 / 100 / 400 active Cloud Run service instance-hours. Cloud SQL allowances increase in larger profiles. Recheck database sizing under load.

| Component | Light product use | Active development | Scale exercise |
| --- | ---: | ---: | ---: |
| BigQuery queries, gross | $3.13 | $12.50 | $62.50 |
| BigQuery storage, gross | $0.23 | $0.58 | $2.30 |
| Cloud Storage allowance | $1 | $2 | $6 |
| Cloud Run services allowance | $2 | $10 | $40 |
| Cloud SQL allowance | $15 | $25 | $60 |
| Other GCP services allowance | $5 | $10 | $25 |
| **GCP subtotal** | **$26.36** | **$60.08** | **$195.80** |
| Investigation model usage | $40.76 | $203.80 | $815.20 |
| **Base monthly subtotal** | **$67.12** | **$263.88** | **$1,011.00** |
| **With 25% planning contingency** | **$83.90** | **$329.85** | **$1,263.75** |

These later hosted subtotals exclude initial generation/extraction, incremental new feedback, evaluation judge charges, Galileo subscription/usage, paid embeddings, a paid Qdrant cluster, exceptional networking, domains, and development labour. Contingency is not a substitute for pricing these exclusions. Initial Qdrant usage uses the verified free tier; confirm upgrade costs before exceeding its limits. Galileo pricing remains unconfirmed. These examples do not override the user's 10–15 total-run allocation.

For the light first month, a concrete planning example is $83.90 recurring allowance + $5.80 preparation for 10K generated reviews + $20.38 for 50 evaluation runs + a provisional $5–$15 cloud bulk-processing allowance: **about $115–$125, plus unpriced Galileo/judging/embedding charges**. This is not an all-inclusive ceiling.

The most important economic distinction: warehouse row count primarily affects storage and scanned bytes; investigation count, model reasoning output, and repeated text processing drive model spend.

## 11. Cost controls and operations

- Begin with the development fixture, then scale to the reference dataset only after measured query and token costs are recorded.
- Proposed starting application limits: $2/model investigation budget, 20 SQL queries, 10 GiB estimated scan per query, and 50 GiB per investigation; tune after benchmarks. Reserve expected call cost before dispatch and reconcile actual usage afterward. These limits must include concurrent work and retries.
- Use BigQuery maximum bytes billed, project query quotas, partition filters, and allowlisted datasets. Dry runs inform decisions; result row limits are separate controls. See [BigQuery cost controls](https://docs.cloud.google.com/bigquery/docs/best-practices-costs).
- Set Cloud Run minimum instances to zero for the service and restrict maximum instances/concurrency. Cloud SQL remains an idle baseline; stopping it does not eliminate retained storage costs.
- Configure billing alerts and an application-level daily spending ledger. Budget alerts are notifications, not guaranteed spending caps.
- Persist extraction results and reusable aggregates. Avoid rerunning the whole corpus for every user question.
- Keep raw data and cloud analytical processing co-located. Send bounded evidence to external model APIs; measure egress instead of exporting the warehouse.
- Record token usage, estimated model charge, actual query bytes, retries, and elapsed time per investigation. Reconcile against provider billing.
- Since app auth is deferred, use reference data for the hosted demo, rate-limit costly endpoints, and avoid making cloud/database credentials available to the browser. The shared notebook is not an account-isolated private service.

## 12. Start here

### README requirement: Qdrant reactivation and recovery

The [README](README.md#reactivate-or-rebuild-qdrant) contains the recovery procedure. During implementation, add and test exact re-indexing commands. Check cluster status and reactivate a suspended cluster through Qdrant Cloud; if deleted, create a new free cluster, securely update its endpoint/key, recreate the versioned collection schema and payload indexes, and re-index retained evidence with the same embedding configuration. Restore a compatible snapshot when available. Verify record counts, source citations, filters, and a sample retrieval before resuming investigations. Record model availability changes and require a new index version if embeddings change. Do not invent CLI commands before implementation or keep clusters alive with artificial traffic.

The implemented re-index command is `uv run python scripts/index_qdrant.py`. It creates the versioned collection, records the embedding configuration, adds the product filter, and idempotently upserts retained evidence. The exact recovery procedure is in the [README](README.md#9-qdrant-setup-reactivation-and-recovery).

Cloud Run revision `sourcelens-00007-d4f` is live at `https://sourcelens-tdghggm6ma-uc.a.run.app`, scales to zero, and is capped at one instance. Uploaded sources and manifests persist in Cloud Storage and repopulate the catalog after a restart. The public no-auth build uses the deterministic investigation path to avoid exposing paid model calls. The full three-role agent workflow has passed a live verification and can be enabled through runtime configuration. Remaining release work is credential-bound: activate Qdrant Cloud and Galileo. Before multi-instance hosting, replace ephemeral investigation and notebook state with a durable store.
